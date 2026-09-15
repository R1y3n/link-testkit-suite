#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import threading
import urllib.request

class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'

VERBOSE = False

def log_debug(msg):
    if VERBOSE:
        print(f"{Colors.CYAN}[DEBUG {time.strftime('%T')}] {msg}{Colors.NC}")

def log_info(msg):
    print(f"{Colors.BLUE}[INFO] {msg}{Colors.NC}")

def log_ok(msg):
    print(f"{Colors.GREEN}[PASS] {msg}{Colors.NC}")

def log_warn(msg):
    print(f"{Colors.YELLOW}[WARN] {msg}{Colors.NC}")

def log_fail(msg):
    print(f"{Colors.RED}[FAIL] {msg}{Colors.NC}")

# --- Network & System Helpers ---
def get_default_route():
    try:
        out = subprocess.check_output(['ip', 'route', 'show', 'default']).decode()
        parts = out.split()
        return parts[4], parts[2] # iface, gateway
    except Exception as e:
        log_fail(f"Could not detect default route: {e}")
        sys.exit(1)

def get_ethtool_stats(iface):
    try:
        out = subprocess.check_output(['ethtool', '-S', iface], stderr=subprocess.DEVNULL).decode()
        stats = {}
        for line in out.splitlines():
            if ':' in line:
                key, val = line.split(':', 1)
                stats[key.strip()] = int(val.strip())
        return stats
    except Exception as e:
        log_debug(f"ethtool -S failed: {e}")
        return {}

def get_link_speed(iface):
    try:
        out = subprocess.check_output(['ethtool', iface], stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            if 'Speed:' in line:
                return line.split(':')[1].strip()
    except:
        pass
    return "Unknown"

def get_tcp_retransmits():
    """Reads the kernel's TCP retransmission counter from /proc/net/netstat"""
    try:
        with open('/proc/net/netstat', 'r') as f:
            lines = f.readlines()
            for i in range(0, len(lines), 2):
                if lines[i].startswith('TcpExt:'):
                    headers = lines[i].split()
                    values = lines[i+1].split()
                    if 'TCPRetransSegs' in headers:
                        idx = headers.index('TCPRetransSegs')
                        return int(values[idx])
    except Exception as e:
        log_debug(f"Failed to read TCP retransmits: {e}")
    return 0

def check_link_carrier(iface):
    try:
        with open(f'/sys/class/net/{iface}/carrier', 'r') as f:
            return f.read().strip() == '1'
    except:
        try:
            with open(f'/sys/class/net/{iface}/operstate', 'r') as f:
                return f.read().strip() == 'up'
        except:
            return True

# --- 1GB Download Test ---
def download_1gb():
    urls = [
        "http://proof.ovh.net/files/1Gb.dat",
        "http://speed.cloudflare.com/__down?bytes=1000000000"
    ]
    
    for url in urls:
        log_info(f"Attempting to download 1GB from {url}...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            start_time = time.time()
            downloaded = 0
            
            with urllib.request.urlopen(req, timeout=120) as response:
                while True:
                    chunk = response.read(1024 * 1024) # Read in 1MB chunks
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    mb = downloaded / (1024*1024)
                    sys.stdout.write(f"\r  Downloaded: {mb:.1f} MB / 1000.0 MB")
                    sys.stdout.flush()
                    
            elapsed = time.time() - start_time
            print() # newline
            speed_mbps = (downloaded * 8) / (elapsed * 1000000)
            log_ok(f"Successfully downloaded {downloaded / (1024*1024):.1f} MB in {elapsed:.2f}s ({speed_mbps:.2f} Mbps)")
            return True, speed_mbps
        except Exception as e:
            log_warn(f"Failed to download from {url}: {e}")
            continue
            
    return False, 0

# --- Wiggle Test Monitor ---
class WiggleMonitor:
    def __init__(self, iface, gateway):
        self.iface = iface
        self.gateway = gateway
        self.link_drops = 0
        self.ping_sent = 0
        self.ping_recv = 0
        self.stop_event = threading.Event()
        self.ping_proc = None
        
    def ping_loop(self):
        # Run a fast ICMP ping (0.1s interval requires root, which we have)
        cmd = ['ping', '-i', '0.1', '-q', self.gateway]
        self.ping_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # FIXED: Use communicate() to safely read the output, then decode
        stdout_data, _ = self.ping_proc.communicate()
        out = stdout_data.decode('utf-8', errors='ignore')
        
        for line in out.splitlines():
            if 'packet loss' in line:
                parts = line.split()
                self.ping_sent = int(parts[0])
                self.ping_recv = int(parts[3])

    def link_loop(self):
        while not self.stop_event.is_set():
            if not check_link_carrier(self.iface):
                self.link_drops += 1
            time.sleep(0.1)

    def start(self):
        self.t1 = threading.Thread(target=self.ping_loop)
        self.t2 = threading.Thread(target=self.link_loop)
        self.t1.start()
        self.t2.start()

    def stop(self):
        self.stop_event.set()
        if self.ping_proc:
            self.ping_proc.terminate()
        self.t1.join()
        self.t2.join()

# --- Main Execution ---
def main():
    global VERBOSE
    
    if '-v' in sys.argv or '--verbose' in sys.argv:
        VERBOSE = True

    if os.geteuid() != 0:
        log_fail("This script must be run as root (sudo) to read hardware counters and run fast pings.")
        sys.exit(1)

    log_info("Auto-detecting network configuration...")
    IFACE, GATEWAY = get_default_route()
    log_info(f"Using Interface: {IFACE} | Target (Router): {GATEWAY}")

    if not subprocess.call(['which', 'ethtool'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
        log_fail("Required tool 'ethtool' is missing. Install via: sudo apt install ethtool")
        sys.exit(1)

    # --- Phase 1: Baseline ---
    log_info("Phase 1: Checking link negotiation and baseline physical errors...")
    LINK_SPEED = get_link_speed(IFACE)
    log_info(f"Link negotiated at: {LINK_SPEED}")
    
    baseline_stats = get_ethtool_stats(IFACE)
    baseline_crc = baseline_stats.get('rx_crc_errors', 0) + baseline_stats.get('rx_fcs_errors', 0) + baseline_stats.get('tx_errors', 0)
    baseline_retransmits = get_tcp_retransmits()
    
    log_debug(f"Baseline Hardware CRC Errors: {baseline_crc}")
    log_debug(f"Baseline TCP Retransmits: {baseline_retransmits}")

    if LINK_SPEED == "100Mb/s":
        log_warn("Link is 100Mbps. Cable is likely Cat5, damaged, or poorly terminated.")
    elif LINK_SPEED == "1000Mb/s":
        log_ok("Link is 1Gbps.")

    # --- Phase 2: 1GB Download & Retransmission Test ---
    log_info("Phase 2: Downloading 1GB from the internet to stress the cable and track TCP retries...")
    success, speed = download_1gb()
    
    if not success:
        log_fail("Could not download 1GB from any mirror. Check your internet connection.")
        sys.exit(1)

    post_download_stats = get_ethtool_stats(IFACE)
    post_download_crc = post_download_stats.get('rx_crc_errors', 0) + post_download_stats.get('rx_fcs_errors', 0) + post_download_stats.get('tx_errors', 0)
    post_download_retransmits = get_tcp_retransmits()
    
    crc_delta_download = post_download_crc - baseline_crc
    retransmits_delta = post_download_retransmits - baseline_retransmits

    log_debug(f"Download Hardware CRC Errors Delta: +{crc_delta_download}")
    log_debug(f"Download TCP Retransmits Delta: +{retransmits_delta}")

    if retransmits_delta > 50:
        log_fail(f"TCP Test: {retransmits_delta} retransmissions detected! High packet loss/cable defects.")
    elif retransmits_delta > 0:
        log_warn(f"TCP Test: {retransmits_delta} retransmissions detected. Minor signal degradation.")
    else:
        log_ok("TCP Test: 0 retransmissions. Perfect data integrity.")

    # --- Phase 3: Wiggle Test ---
    log_info("Phase 3: Physical Stress Test (Wiggle). Please wiggle/bend the cable for 20 seconds...")
    log_info(">>> START WIGGLING THE CABLE NOW! <<<")
    
    monitor = WiggleMonitor(IFACE, GATEWAY)
    monitor.start()
    time.sleep(20)
    monitor.stop()
    
    log_info(">>> STOP WIGGLING. Analyzing stress data... <<<")
    
    ping_loss = 0
    if monitor.ping_sent > 0:
        ping_loss = ((monitor.ping_sent - monitor.ping_recv) / monitor.ping_sent) * 100

    log_debug(f"Wiggle Ping Loss: {ping_loss:.2f}% | Link Drops: {monitor.link_drops}")

    if monitor.link_drops > 0:
        log_fail(f"Stress Test: Link physically disconnected {monitor.link_drops} time(s)! Broken internal wire.")
    elif ping_loss > 5:
        log_fail(f"Stress Test: {ping_loss:.2f}% packet loss during movement. Intermittent connection.")
    elif ping_loss > 0:
        log_warn(f"Stress Test: Minor ping loss ({ping_loss:.2f}%) during movement. Marginal connection.")
    else:
        log_ok("Stress Test: 0 link drops, 0 ping loss. Physical connection is solid.")

    # --- Phase 4: Final Verdict ---
    final_stats = get_ethtool_stats(IFACE)
    final_crc = final_stats.get('rx_crc_errors', 0) + final_stats.get('rx_fcs_errors', 0) + final_stats.get('tx_errors', 0)
    total_crc_delta = final_crc - baseline_crc

    print(f"\n{'='*42}")
    print(f"{Colors.CYAN}           FINAL CABLE VERDICT        {Colors.NC}")
    print(f"{'='*42}")

    VERDICT = "GOOD"
    REASON = "Passed all throughput, TCP retransmission, and physical stress tests."

    if monitor.link_drops > 0 or total_crc_delta > 0:
        VERDICT = "REJECT (Defective)"
        REASON = "Physical disconnects or hardware CRC errors detected. Replace immediately."
    elif retransmits_delta > 50 or ping_loss > 5:
        VERDICT = "DEGRADED"
        REASON = "High TCP retransmissions or ping loss. Cable has severe signal degradation."
    elif retransmits_delta > 0 or ping_loss > 0:
        VERDICT = "MARGINAL"
        REASON = "Minor retries or ping loss. Cable might be too long or poorly terminated."
    elif LINK_SPEED == "100Mb/s":
        VERDICT = "LIMITED (Cat5 or Damaged)"
        REASON = "Failed to negotiate 1Gbps. Likely an old Cat5 cable or a broken pair."

    if "REJECT" in VERDICT or "DEGRADED" in VERDICT or "LIMITED" in VERDICT:
        print(f"Status: {Colors.RED}{VERDICT}{Colors.NC}")
    elif "MARGINAL" in VERDICT:
        print(f"Status: {Colors.YELLOW}{VERDICT}{Colors.NC}")
    else:
        print(f"Status: {Colors.GREEN}{VERDICT}{Colors.NC}")

    print(f"Reason: {REASON}")
    print(f"{'='*42}\n")

if __name__ == "__main__":
    main()
