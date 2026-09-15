# link-testkit-suite# Elite USB Wi-Fi Dongle Stress Tester v3.1

## 📖 Overview
The **Elite USB Wi-Fi Dongle Stress Tester** is a professional-grade, comprehensive hardware validation suite designed to push USB Wi-Fi adapters to their absolute limits. It rigorously tests both **Managed (Client)** and **Monitor (Raw RF)** modes, extracting deep hardware metrics, stress-testing throughput, and monitoring for underlying driver or USB bus failures.

This tool is engineered for hardware reviewers, penetration testers, and network engineers who need empirical data on a dongle's real-world performance, stability, and RF sensitivity.

---

## 🌟 Key Features

### Managed Mode (Layer 2 & 3 Testing)
* **Auth & Association Stress:** Rapidly connects and disconnects from a target AP to test the stability and speed of the WPA/WPA2 handshake and baseband processor.
* **Link Quality & Stability:** Monitors real-time signal strength (RSSI), TX/RX bitrates, and hardware-level frame retries/failures via `iw station dump`.
* **Layer 3 Availability:** Executes high-frequency ICMP ping floods to measure packet loss, latency (RTT), and jitter.
* **Throughput Limits:** Utilizes `iperf3` to measure maximum TCP/UDP bandwidth limits.

### Monitor Mode (Raw RF Testing)
* **Deep Passive Sniffing:** Captures raw 802.11 frames to calculate real-world Packet-Per-Second (PPS) rates, beacon density, and retry rates.
* **FCS Error Tracking:** Enables `fcsfail` at the driver level to capture and calculate the Frame Check Sequence (FCS) error rate, indicating RF signal corruption.
* **TX Injection Stress:** Injects 10,000 raw beacon frames in a tight loop to test the dongle's maximum transmit packet rate and CSMA/CA throttling behavior.
* **Sensitivity Mapping:** Records the absolute lowest RSSI (dBm) the dongle can successfully decode.

### Hardware & System Monitoring
* **NetworkManager Neutralization:** Automatically isolates the interface from desktop network managers to prevent connection conflicts.
* **Kernel/USB Watchdog:** A background thread monitors `dmesg` in real-time for USB disconnects, driver resets, or kernel panics to detect hardware overheating or bus failures.
* **Power Save Disabling:** Forces the Wi-Fi power save mode off to prevent the dongle from entering low-power states during monitor mode.

---

## ⚙️ Prerequisites & Installation

This script is designed for **Linux** environments and requires **root privileges**.

### 1. System Dependencies
Install the required networking and diagnostic tools:
```bash
sudo apt update
sudo apt install iw iproute2 wpa_supplicant ethtool usbutils iperf3 curl dhclient python3 python3-pip
```

### 2. Python Dependencies
Install the required Python packet manipulation library:
```bash
sudo pip3 install scapy
```

---

## 🚀 Usage

Run the script with `sudo`. 

### Command Line Arguments

| Argument | Short | Description | Required |
| :--- | :---: | :--- | :---: |
| `--device` | `-d` | The wireless interface name (e.g., `wlan0`, `wlx00e048...`). | **Yes** |
| `--ssid` | `-s` | The target Wi-Fi network name for Managed mode tests. | **Yes** |
| `--password` | `-p` | The WPA/WPA2 password for the target network. | **Yes** |
| `--target-ip` | `-t` | The IP address of the router/target for Ping and `iperf3` throughput tests. | No* |
| `--duration` | | Duration in seconds for the stability and sniffing phases (Default: 60). | No |

*\*Note: If `--target-ip` is omitted, Layer 3 throughput and ping tests will be skipped or fallback to public DNS (8.8.8.8).*

### Example Execution
```bash
sudo -E python3 wifi_stress_test_final.py \
  -d wlx00e0480d97e3 \
  -s "MyNetworkSSID" \
  -p "SuperSecretPassword" \
  -t 192.168.1.1 \
  --duration 120
```

---

## 📊 Output & Reporting

Upon completion, the script generates two files in the current directory:

1. **`YYYYMMDD_HHMMSS_<interface>.log`**: A raw, timestamped execution log containing all debug traces, command outputs, and kernel warnings.
2. **`YYYYMMDD_HHMMSS_<interface>_report.json`**: A structured JSON file containing all calculated metrics.

### How to Read the JSON Report

#### `managed_connection`
* `success_rate`: Percentage of successful AP associations (Target: 100%).
* `avg_time`, `min_time`, `max_time`: Time in seconds to complete the WPA handshake. Lower is better.

#### `managed_link_quality`
* `ping_loss_percent`: Percentage of dropped ICMP packets. (Target: 0%).
* `ping_rtt_avg_ms` / `ping_rtt_max_ms`: Average and maximum latency in milliseconds. Lower is better.
* `tx_retries` / `tx_failed`: Hardware-level MAC retransmissions and dropped frames. Lower is better.

#### `monitor_sniff`
* `packet_rate_pps`: Packets captured per second. Higher indicates better RF sensitivity and bus throughput.
* `retry_rate_percent`: Percentage of captured frames that were retransmissions by the AP.
* `fcs_error_rate_percent`: Percentage of frames with corrupted checksums. (Target: 0.0%. High values indicate poor antenna gain or extreme distance).
* `rssi_stats`: Minimum, Maximum, and Average signal strength in dBm. (Closer to 0 is better. e.g., -50 is excellent, -90 is poor).

#### `monitor_tx_injection`
* `frames_sent`: Total raw frames injected (Always 10,000).
* `actual_rate_fps`: Frames Per Second. *Note: If this number drops significantly in "close" proximity compared to "far" proximity, it means the dongle's CSMA/CA (Carrier Sense) is actively throttling injection to avoid colliding with real network traffic.*

#### `hardware_stability`
* `STABLE` or `UNSTABLE`: Indicates if the background `dmesg` monitor caught any USB disconnects or driver crashes.

---

## 🛠️ Troubleshooting & Notes

### 1. "Network is down" or Scapy Socket Errors in Monitor Mode
* **Cause:** USB power management is putting the dongle to sleep, or the interface wasn't brought UP correctly before Scapy bound to it.
* **Fix:** The v3.1 script automatically disables power save (`iw dev <iface> set power_save off`). Ensure your USB port provides adequate power (try a powered USB 3.0 hub if using a high-gain dual-band dongle).

### 2. 100% Ping Loss in Managed Mode
* **Cause:** The dongle successfully connected to the Wi-Fi (Layer 2), but failed to get an IP address (Layer 3).
* **Fix:** Ensure `dhclient` is installed and functioning. If your router blocks ICMP (Ping), the ping test will show 100% loss even if the connection is perfect. Use the `iperf3` TCP test for a more reliable Layer 3 validation.

### 3. Throughput Tests Skipping
* **Cause:** The target IP does not have an `iperf3` server running.
* **Fix:** Run `iperf3 -s` on your target machine/router to enable throughput testing.

### 4. NetworkManager Interference
* **Cause:** Desktop Linux environments use NetworkManager, which will fight the script for control of the Wi-Fi dongle.
* **Fix:** The script automatically runs `nmcli device set <iface> managed no` to isolate the interface. If issues persist, temporarily stop the service: `sudo systemctl stop NetworkManager`.

---

## ⚠️ Disclaimer
* **TX Injection:** The monitor mode TX injection test blasts 10,000 raw beacon frames. **Do not run this in environments where it may interfere with critical infrastructure, aviation, or public networks.** It is intended for isolated lab testing.
* **Hardware Limits:** Continuous stress testing, especially TX injection and high-throughput TCP floods, can cause USB Wi-Fi dongles to overheat. If the `hardware_stability` reports `UNSTABLE`, the dongle has likely hit its thermal or USB bus limits.
