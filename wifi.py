#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, subprocess, time, datetime, logging, argparse, threading, socket, random, json, re, signal

try:
    from scapy.all import conf, sniff, Dot11, RadioTap, Dot11Beacon, sendp, Raw
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

class Colors:
    HEADER = '\033[95m'; OKBLUE = '\033[94m'; OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'; WARNING = '\033[93m'; FAIL = '\033[91m'
    ENDC = '\033[0m'; BOLD = '\033[1m'

def run_cmd(cmd, check=False, timeout=10):
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                text=True, check=check, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)

class LinkQualityPoller(threading.Thread):
    def __init__(self, iface, stop_event, interval=1.0):
        super().__init__(daemon=True)
        self.iface = iface; self.stop_event = stop_event; self.interval = interval
        self.data = []; self.lock = threading.Lock()

    def run(self):
        while not self.stop_event.is_set():
            rc, out, _ = run_cmd(['iw', 'dev', self.iface, 'station', 'dump'])
            if rc == 0:
                metrics = {'time': time.time(), 'signal': None, 'tx_rate': None, 
                           'rx_rate': None, 'tx_failed': 0, 'tx_retries': 0}
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith("signal:"): 
                        try: metrics['signal'] = int(line.split()[1])
                        except: pass
                    elif "tx bitrate:" in line: 
                        try: metrics['tx_rate'] = float(re.search(r'([\d.]+) MBit/s', line).group(1))
                        except: pass
                    elif "rx bitrate:" in line: 
                        try: metrics['rx_rate'] = float(re.search(r'([\d.]+) MBit/s', line).group(1))
                        except: pass
                    elif line.startswith("tx failed:"): 
                        try: metrics['tx_failed'] = int(line.split()[2])
                        except: pass
                    elif line.startswith("tx retries:"): 
                        try: metrics['tx_retries'] = int(line.split()[2])
                        except: pass
                with self.lock: self.data.append(metrics)
            self.stop_event.wait(self.interval)

class DmesgMonitor(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(daemon=True); self.stop_event = stop_event
        self.errors = []; self.lock = threading.Lock()
        self.keywords = ['USB disconnect', 'device descriptor read', 'driver error', 'reset', 'panic', 'WARN', 'BUG']

    def run(self):
        run_cmd(['dmesg', '-C'])
        proc = subprocess.Popen(['dmesg', '-w'], stdout=subprocess.PIPE, text=True)
        try:
            for line in proc.stdout:
                if self.stop_event.is_set(): break
                if any(kw.lower() in line.lower() for kw in self.keywords):
                    with self.lock: self.errors.append(line.strip())
                    logging.warning(f"{Colors.FAIL}KERNEL/USB ERROR: {line.strip()}{Colors.ENDC}")
        finally: proc.terminate()

class WifiStressTester:
    def __init__(self, args):
        self.iface = args.device; self.ssid = args.ssid; self.password = args.password
        self.target_ip = args.target_ip; self.duration = args.duration
        self.log_file = self._setup_logging(); self.results = {}
        self._stop_event = threading.Event(); self.phy_name = self._get_phy_name()

    def _setup_logging(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"{timestamp}_{self.iface}.log"
        logger = logging.getLogger(); logger.setLevel(logging.DEBUG)
        ch = logging.StreamHandler(); ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(f'{Colors.OKCYAN}[%(asctime)s]{Colors.ENDC} %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(ch)
        fh = logging.FileHandler(log_file); fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s [%(module)s]: %(message)s'))
        logger.addHandler(fh)
        return log_file

    def _get_phy_name(self):
        try:
            link = os.readlink(f"/sys/class/net/{self.iface}/phy80211")
            return link.split('/')[-1]
        except: return None

    def _pre_flight_checks(self):
        logging.info(f"{Colors.BOLD}Running pre-flight checks...{Colors.ENDC}")
        if os.geteuid() != 0:
            logging.error("Requires root. Run with sudo."); sys.exit(1)
        for tool in ['iw', 'ip', 'wpa_supplicant', 'wpa_cli', 'ping', 'lsusb', 'nmcli', 'dhclient']:
            if run_cmd(['which', tool])[0] != 0:
                logging.error(f"Missing tool: {tool}. Install via apt."); sys.exit(1)
        logging.info(f"{Colors.OKGREEN}Pre-flight checks passed.{Colors.ENDC}")

    def _neutralize_network_manager(self):
        logging.info("Neutralizing NetworkManager & Power Save...")
        run_cmd(['nmcli', 'device', 'set', self.iface, 'managed', 'no'])
        # CRITICAL: Disable power save to prevent monitor mode drops
        run_cmd(['iw', 'dev', self.iface, 'set', 'power_save', 'off'])
        run_cmd(['ip', 'link', 'set', self.iface, 'down'])
        time.sleep(1); run_cmd(['ip', 'link', 'set', self.iface, 'up']); time.sleep(1)

    def _restore_network_manager(self):
        run_cmd(['nmcli', 'device', 'set', self.iface, 'managed', 'yes'])

    def _set_mode(self, mode):
        logging.info(f"Switching {self.iface} to {mode} mode...")
        run_cmd(['ip', 'link', 'set', self.iface, 'down'])
        run_cmd(['iw', 'dev', self.iface, 'set', 'type', mode])
        run_cmd(['ip', 'link', 'set', self.iface, 'up'])
        time.sleep(2)

    def _run_managed_tests(self):
        logging.info(f"\n{Colors.HEADER}{'='*20} STARTING MANAGED MODE TESTS {'='*20}{Colors.ENDC}")
        self._neutralize_network_manager()
        self._set_mode('managed')
        self._test_connection_stress()
        self._test_link_quality_and_stability()
        if self.target_ip: self._test_throughput()
        self._restore_network_manager()

    def _test_connection_stress(self):
        logging.info(f"{Colors.BOLD}[Managed] Testing Connection & Auth Stress (5 cycles)...{Colors.ENDC}")
        config = subprocess.check_output(['wpa_passphrase', self.ssid, self.password], text=True)
        config += "\nctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n"
        conf_path = '/tmp/wpa_stress.conf'
        with open(conf_path, 'w') as f: f.write(config)
            
        connect_times = []
        for i in range(5):
            logging.info(f"  Cycle {i+1}/5...")
            run_cmd(['pkill', '-9', '-f', 'wpa_supplicant']); run_cmd(['dhclient', '-r', self.iface])
            time.sleep(1)
            start = time.time()
            subprocess.Popen(['wpa_supplicant', '-B', '-i', self.iface, '-c', conf_path], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            connected = False
            for _ in range(30):
                time.sleep(1)
                rc, out, _ = run_cmd(['wpa_cli', '-i', self.iface, 'status'], timeout=2)
                if rc == 0 and 'wpa_state=COMPLETED' in out:
                    connected = True; break
            if connected:
                # CRITICAL FIX: Get IP address via DHCP
                run_cmd(['dhclient', self.iface], timeout=15)
                connect_times.append(time.time() - start)
                logging.info(f"  {Colors.OKGREEN}SUCCESS{Colors.ENDC} in {connect_times[-1]:.2f}s")
                run_cmd(['wpa_cli', '-i', self.iface, 'disconnect']); run_cmd(['dhclient', '-r', self.iface])
                time.sleep(2)
            else:
                logging.error(f"  {Colors.FAIL}FAILED{Colors.ENDC}")
                
        if connect_times:
            self.results['managed_connection'] = {
                'avg_time': sum(connect_times)/len(connect_times),
                'min_time': min(connect_times), 'max_time': max(connect_times),
                'success_rate': len(connect_times)/5 * 100
            }

    def _test_link_quality_and_stability(self):
        logging.info(f"{Colors.BOLD}[Managed] Testing Link Stability ({self.duration}s)...{Colors.ENDC}")
        subprocess.Popen(['wpa_supplicant', '-B', '-i', self.iface, '-c', '/tmp/wpa_stress.conf'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3); run_cmd(['dhclient', self.iface], timeout=15); time.sleep(2)
        
        poller = LinkQualityPoller(self.iface, self._stop_event); poller.start()
        target = self.target_ip or '8.8.8.8'
        rc, out, _ = run_cmd(['ping', '-i', '0.2', '-c', str(self.duration * 5), target], timeout=self.duration + 15)
        self._stop_event.set(); poller.join(); self._stop_event.clear()
        
        ping_stats = {'loss': 100, 'rtt_avg': 0, 'rtt_max': 0}
        match = re.search(r'([\d.]+)% packet loss', out)
        if match: ping_stats['loss'] = float(match.group(1))
        match = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', out)
        if match:
            ping_stats['rtt_avg'] = float(match.group(2)); ping_stats['rtt_max'] = float(match.group(3))
            
        total_retries = total_failed = 0
        if poller.data:
            total_retries = poller.data[-1]['tx_retries'] - poller.data[0]['tx_retries']
            total_failed = poller.data[-1]['tx_failed'] - poller.data[0]['tx_failed']
            
        self.results['managed_link_quality'] = {
            'ping_loss_percent': ping_stats['loss'], 'ping_rtt_avg_ms': ping_stats['rtt_avg'],
            'ping_rtt_max_ms': ping_stats['rtt_max'], 'tx_retries': total_retries, 'tx_failed': total_failed
        }

    def _test_throughput(self):
        logging.info(f"{Colors.BOLD}[Managed] Testing Throughput Limits...{Colors.ENDC}")
        if run_cmd(['which', 'iperf3'])[0] == 0:
            logging.info(f"  Running TCP test against {self.target_ip}...")
            rc, out, _ = run_cmd(['iperf3', '-c', self.target_ip, '-t', '10', '-B', self.iface, '-J'], timeout=20)
            if rc == 0:
                try: self.results['tcp_mbps'] = json.loads(out)['end']['sum_sent']['bits_per_second'] / 1_000_000
                except: pass
        else: logging.warning("iperf3 not found. Skipping.")

    def _run_monitor_tests(self):
        if not SCAPY_AVAILABLE:
            logging.warning(f"{Colors.WARNING}Scapy not installed. Skipping Monitor Mode.{Colors.ENDC}"); return
        logging.info(f"\n{Colors.HEADER}{'='*20} STARTING MONITOR MODE TESTS {'='*20}{Colors.ENDC}")
        run_cmd(['pkill', '-9', '-f', 'wpa_supplicant']); run_cmd(['dhclient', '-r', self.iface])
        self._neutralize_network_manager() # Re-apply power save off
        self._set_mode('monitor')
        if self.phy_name: run_cmd(['iw', 'phy', self.phy_name, 'set', 'monitor', 'fcsfail'])
        
        # Ensure interface is strictly UP before Scapy touches it
        run_cmd(['ip', 'link', 'set', self.iface, 'up'])
        time.sleep(2) 
        
        self._test_passive_sniff()
        self._test_tx_injection()

    def _test_passive_sniff(self):
        logging.info(f"{Colors.BOLD}[Monitor] Passive Sniffing ({self.duration}s)...{Colors.ENDC}")
        stats = {'total': 0, 'retries': 0, 'beacons': 0, 'fcs_errors': 0, 'rssi': [], 'lock': threading.Lock()}

        def packet_handler(pkt):
            with stats['lock']:
                stats['total'] += 1
                if pkt.haslayer(Dot11):
                    if pkt[Dot11].FCfield & 0x08: stats['retries'] += 1
                    if pkt.haslayer(Dot11Beacon): stats['beacons'] += 1
                if pkt.haslayer(RadioTap):
                    try:
                        rssi = pkt[RadioTap].dBm_AntSignal
                        if rssi is not None: stats['rssi'].append(rssi)
                    except: pass
                    try:
                        if pkt[RadioTap].Flags & 0x10: stats['fcs_errors'] += 1
                    except: pass

        # Scapy sniff with error handling
        try:
            sniff(iface=self.iface, timeout=self.duration, prn=packet_handler, store=False)
        except Exception as e:
            logging.warning(f"Sniff interrupted: {e}")

        rssi_stats = {}
        if stats['rssi']:
            rssi_stats = {'min': min(stats['rssi']), 'max': max(stats['rssi']), 'avg': sum(stats['rssi'])/len(stats['rssi'])}

        self.results['monitor_sniff'] = {
            'packet_rate_pps': stats['total'] / self.duration,
            'beacon_rate': stats['beacons'] / self.duration,
            'retry_rate_percent': (stats['retries'] / stats['total'] * 100) if stats['total'] > 0 else 0,
            'fcs_error_rate_percent': (stats['fcs_errors'] / stats['total'] * 100) if stats['total'] > 0 else 0,
            'rssi_stats': rssi_stats
        }

    def _test_tx_injection(self):
        logging.info(f"{Colors.BOLD}[Monitor] TX Injection Stress Test...{Colors.ENDC}")
        count = 10000
        pkt = RadioTap()/Dot11(type=0, subtype=0, addr1="ff:ff:ff:ff:ff:ff", 
                               addr2="00:11:22:33:44:55", addr3="00:11:22:33:44:55")/Dot11Beacon()
        start = time.time()
        try:
            sendp(pkt, iface=self.iface, count=count, verbose=False)
        except Exception as e:
            logging.warning(f"TX Injection interrupted: {e}")
        elapsed = time.time() - start
        self.results['monitor_tx_injection'] = {
            'frames_sent': count, 'time_taken_s': elapsed,
            'actual_rate_fps': count / elapsed if elapsed > 0 else 0
        }

    def _generate_report(self):
        logging.info(f"\n{Colors.HEADER}{'='*20} FINAL STRESS TEST REPORT {'='*20}{Colors.ENDC}")
        report_path = self.log_file.replace('.log', '_report.json')
        with open(report_path, 'w') as f: json.dump(self.results, f, indent=4)
        logging.info(f"JSON report: {Colors.OKGREEN}{report_path}{Colors.ENDC}")
        logging.info(f"Raw log: {Colors.OKGREEN}{self.log_file}{Colors.ENDC}")
        
        if 'managed_connection' in self.results:
            mc = self.results['managed_connection']
            logging.info(f"{Colors.BOLD}[Summary] Auth Success:{Colors.ENDC} {mc['success_rate']}% (Avg: {mc['avg_time']:.2f}s)")
        if 'managed_link_quality' in self.results:
            lq = self.results['managed_link_quality']
            logging.info(f"{Colors.BOLD}[Summary] Ping Avail:{Colors.ENDC} {100 - lq['ping_loss_percent']:.1f}% | Retries: {lq['tx_retries']}")
        if 'tcp_mbps' in self.results:
            logging.info(f"{Colors.BOLD}[Summary] TCP Speed:{Colors.ENDC} {self.results['tcp_mbps']:.2f} Mbps")
        if 'monitor_sniff' in self.results:
            ms = self.results['monitor_sniff']
            logging.info(f"{Colors.BOLD}[Summary] Monitor PPS:{Colors.ENDC} {ms['packet_rate_pps']:.1f} | FCS Err: {ms['fcs_error_rate_percent']:.2f}%")

    def _cleanup(self):
        logging.info("Performing hardware cleanup...")
        self._stop_event.set()
        run_cmd(['pkill', '-9', '-f', 'wpa_supplicant']); run_cmd(['dhclient', '-r', self.iface])
        run_cmd(['ip', 'link', 'set', self.iface, 'down'])
        run_cmd(['iw', 'dev', self.iface, 'set', 'type', 'managed'])
        run_cmd(['ip', 'link', 'set', self.iface, 'up'])
        self._restore_network_manager()
        logging.info(f"{Colors.OKGREEN}Cleanup complete.{Colors.ENDC}")

    def run(self):
        print(f"{Colors.BOLD}{Colors.HEADER}=========================================================\n  ELITE USB WI-FI DONGLE STRESS TESTER v3.1 (FINAL)\n========================================================={Colors.ENDC}")
        try:
            self._pre_flight_checks()
            dmesg_mon = DmesgMonitor(self._stop_event); dmesg_mon.start()
            self._run_managed_tests()
            self._run_monitor_tests()
            self._generate_report()
            if dmesg_mon.errors:
                logging.critical(f"{Colors.FAIL}WARNING: {len(dmesg_mon.errors)} USB/Driver errors!{Colors.ENDC}")
                self.results['hardware_stability'] = 'UNSTABLE'
            else: self.results['hardware_stability'] = 'STABLE'
        except KeyboardInterrupt: logging.warning("\nInterrupted.")
        except Exception as e: logging.error(f"Fatal: {e}", exc_info=True)
        finally: self._cleanup()

def main():
    parser = argparse.ArgumentParser(description="Elite USB Wi-Fi Dongle Stress Tester v3.1")
    parser.add_argument("-d", "--device", required=True, help="Wireless interface")
    parser.add_argument("-s", "--ssid", required=True, help="Target SSID")
    parser.add_argument("-p", "--password", required=True, help="Target password")
    parser.add_argument("-t", "--target-ip", help="Target IP for throughput (e.g., router IP)")
    parser.add_argument("--duration", type=int, default=60, help="Duration for tests in seconds")
    args = parser.parse_args()
    tester = WifiStressTester(args)
    signal.signal(signal.SIGINT, lambda sig, frame: tester._stop_event.set())
    tester.run()

if __name__ == "__main__":
    main()
