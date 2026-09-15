#!/usr/bin/env python3
import subprocess
import sys
import re
import argparse

# Reliable 100MB test file
#TEST_URL = "https://speed.hetzner.de/100MB.bin" #this url was shut down
#TEST_URL = "https://proof.ovh.net/files/100Mb.dat" #only if your connection is good
TEST_URL = "https://proof.ovh.net/files/10Mb.dat"


def run_speed_test(interface):
    print("=" * 50)
    print(" 100MB WI-FI DOWNLOAD SPEED TEST")
    print("=" * 50)
    print(f"Target    : {TEST_URL}")
    print(f"Interface : {interface}")
    print("Downloading... (this may take a minute)", flush=True)
    
    # curl command:
    # --interface : Forces DNS and traffic through this specific interface
    # -s          : Silent mode (no progress bar clutter)
    # -o /dev/null: Discard the actual file data
    # -w          : Output specific metrics (size in bytes, time in seconds, speed in bytes/sec)
    cmd = [
        "curl", "-s", "-o", "/dev/null", 
        "--interface", interface,
        "-w", "size:%{size_download}\ntime:%{time_total}\nspeed:%{speed_download}",
        TEST_URL
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        output = result.stdout
        
        # Parse the metrics from curl's output
        size_match = re.search(r'size:(\d+)', output)
        time_match = re.search(r'time:([\d.]+)', output)
        speed_match = re.search(r'speed:([\d.]+)', output)
        
        if size_match and time_match and speed_match:
            size_bytes = int(size_match.group(1))
            time_sec = float(time_match.group(1))
            speed_bps = float(speed_match.group(1)) # curl reports bytes per second
            
            # Convert to human-readable formats
            size_mb = size_bytes / (1024 * 1024)
            speed_mbs = speed_bps / (1024 * 1024)
            speed_mbps = (speed_bps * 8) / (1024 * 1024)
            
            print("\n" + "=" * 50)
            print(" RESULTS ")
            print("=" * 50)
            print(f" Interface Used   : {interface}")
            print(f" Total Downloaded : {size_mb:.2f} MB")
            print(f" Time Taken       : {time_sec:.2f} seconds")
            print(f" Average Speed    : {speed_mbs:.2f} MB/s  ({speed_mbps:.2f} Mbps)")
            print("=" * 50)
        else:
            print("\n[ERROR] Failed to parse download metrics.")
            print(f"Raw output: {output}")
            sys.exit(1)
            
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Download failed (curl exited with code {e.returncode})")
        if e.stderr:
            print(f"Details: {e.stderr.strip()}")
        print("\nTroubleshooting:")
        print("1. Ensure the interface is actually connected to the internet.")
        print("2. Check if the interface name is spelled correctly.")
        sys.exit(1)
    except FileNotFoundError:
        print("\n[ERROR] 'curl' is not installed on this system.")
        print("Please install it via: sudo apt install curl")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Quick Wi-Fi Download Speed Test")
    parser.add_argument("-i", "--interface", required=True, help="The network interface to use (e.g., wlx76012dc4e98b)")
    args = parser.parse_args()
    
    run_speed_test(args.interface)
