#!/bin/bash

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
VERBOSE=0
log_debug() { [[ $VERBOSE -eq 1 ]] && echo -e "${CYAN}[DEBUG $(date +%T)]${NC} $1"; }
log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[PASS]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_fail()  { echo -e "${RED}[FAIL]${NC} $1"; }

cleanup() {
    kill $PING_PID $LINK_MON_PID 2>/dev/null
    rm -f "$TMP_DIR"/ping_wiggle.log "$TMP_DIR"/link_events.log 2>/dev/null
    rmdir "$TMP_DIR" 2>/dev/null
}
trap cleanup EXIT

while getopts "vh" opt; do
    case $opt in
        v) VERBOSE=1 ;;
        h) echo "Usage: sudo $0 [-v for verbose]"; exit 0 ;;
        *) echo "Usage: sudo $0 [-v for verbose]"; exit 1 ;;
    esac
done

log_info "Auto-detecting network configuration..."
IFACE=$(ip route | awk '/default/ {print $5; exit}')
TARGET_IP=$(ip route | awk '/default/ {print $3; exit}')

if [[ -z "$IFACE" || -z "$TARGET_IP" ]]; then
    log_fail "Could not auto-detect default gateway/interface. Are you connected?"
    exit 1
fi

log_info "Using Interface: $IFACE | Target (Router): $TARGET_IP"
TMP_DIR=$(mktemp -d)

for cmd in ethtool ping; do
    if ! command -v $cmd &> /dev/null; then
        log_fail "Required tool '$cmd' is missing. Install via: sudo apt install iproute2 ethtool iputils-ping"
        exit 1
    fi
done

log_info "Phase 1: Checking link negotiation and baseline physical errors..."
LINK_SPEED=$(ethtool "$IFACE" 2>/dev/null | grep "Speed:" | awk '{print $2}')
log_info "Link negotiated at: $LINK_SPEED"

BASELINE_ERRORS=$(ethtool -S "$IFACE" 2>/dev/null | grep -iE "rx_crc_errors|rx_fcs_errors|rx_missed_errors|tx_errors" | awk '{sum+=$2} END {print sum+0}')
log_debug "Baseline Hardware Error Count: $BASELINE_ERRORS"

if [[ "$LINK_SPEED" == "100Mb/s" ]]; then
    log_warn "Link is 100Mbps. Cable is likely Cat5, damaged, or poorly terminated."
elif [[ "$LINK_SPEED" == "1000Mb/s" ]]; then
    log_ok "Link is 1Gbps."
elif [[ "$LINK_SPEED" == "10000Mb/s" ]]; then
    log_ok "Link is 10Gbps."
fi

log_info "Phase 2: Pushing ~1GB of local traffic to the router to stress the cable..."
log_info "(This uses large-packet ping to bypass internet variability)"

ping -c 15000 -s 65507 -q "$TARGET_IP" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    log_debug "Large packets blocked by router. Falling back to high-volume flood ping..."
    ping -f -c 50000 -q "$TARGET_IP" > /dev/null 2>&1
fi
log_ok "Local traffic stress test completed."

log_info "Phase 3: Physical Stress Test (Wiggle). Please wiggle/bend the cable for 20 seconds..."
log_info ">>> START WIGGLING THE CABLE NOW! <<<"

ping -i 0.1 -q "$TARGET_IP" > "$TMP_DIR/ping_wiggle.log" 2>&1 &
PING_PID=$!

(
    while true; do
        if ! ip link show "$IFACE" | grep -q "LOWER_UP"; then
            echo "$(date +%s.%N) LINK_DOWN" >> "$TMP_DIR/link_events.log"
        fi
        sleep 0.1
    done
) &
LINK_MON_PID=$!

sleep 20

kill $PING_PID $LINK_MON_PID 2>/dev/null
wait $PING_PID $LINK_MON_PID 2>/dev/null

log_info ">>> STOP WIGGLING. Analyzing stress data... <<<"

# Analyze Ping Loss
PING_LOSS=$(grep "packet loss" "$TMP_DIR/ping_wiggle.log" | awk -F'[% ]' '{for(i=1;i<=NF;i++) if($i == "loss") print $(i-1)}')
PING_LOSS=${PING_LOSS:-0}

# Analyze Link Drops
LINK_DROPS=0
if [ -f "$TMP_DIR/link_events.log" ]; then
    LINK_DROPS=$(wc -l < "$TMP_DIR/link_events.log")
fi

log_debug "Wiggle Ping Loss: ${PING_LOSS}% | Link Drops: $LINK_DROPS"

if [ "$LINK_DROPS" -gt 0 ]; then
    log_fail "Stress Test: Link physically disconnected $LINK_DROPS time(s)! Broken internal wire or bad crimp."
elif [ "$PING_LOSS" -gt 5 ]; then
    log_fail "Stress Test: ${PING_LOSS}% packet loss during movement. Intermittent connection."
elif [ "$PING_LOSS" -gt 0 ]; then
    log_warn "Stress Test: Minor ping loss (${PING_LOSS}%) during movement. Marginal connection."
else
    log_ok "Stress Test: 0 link drops, 0 ping loss. Physical connection is solid."
fi

log_info "Phase 4: Checking post-test physical layer errors..."
POST_ERRORS=$(ethtool -S "$IFACE" 2>/dev/null | grep -iE "rx_crc_errors|rx_fcs_errors|rx_missed_errors|tx_errors" | awk '{sum+=$2} END {print sum+0}')
ERROR_DELTA=$((POST_ERRORS - BASELINE_ERRORS))

log_debug "Post-test Hardware Error Count: $POST_ERRORS (Delta: +$ERROR_DELTA)"
if [ "$ERROR_DELTA" -gt 0 ]; then
    log_fail "Physical Layer: $ERROR_DELTA new hardware errors (CRC/FCS) detected! The cable has physical defects, crosstalk, or EMI interference."
else
    log_ok "Physical Layer: 0 new hardware errors. Shielding and wiring are perfectly intact."
fi

echo -e "\n=========================================="
echo -e "${CYAN}           FINAL CABLE VERDICT        ${NC}"
echo "=========================================="

VERDICT="GOOD"
REASON="Passed all local stress, wiggle, and physical layer tests."

if [ "$LINK_DROPS" -gt 0 ] || [ "$ERROR_DELTA" -gt 0 ]; then
    VERDICT="REJECT (Defective)"
    REASON="Physical disconnects or hardware CRC errors detected. Replace immediately."
elif [[ "$LINK_SPEED" == "100Mb/s" ]]; then
    VERDICT="LIMITED (Cat5 or Damaged)"
    REASON="Failed to negotiate 1Gbps. Likely an old Cat5 cable or a broken pair."
elif [ "$PING_LOSS" -gt 0 ]; then
    VERDICT="DEGRADED"
    REASON="Minor packet loss during stress. Cable might be too long or poorly terminated."
fi

if [[ "$VERDICT" == *"REJECT"* ]] || [[ "$VERDICT" == *"DEGRADED"* ]] || [[ "$VERDICT" == *"LIMITED"* ]]; then
    echo -e "Status: ${RED}$VERDICT${NC}"
else
    echo -e "Status: ${GREEN}$VERDICT${NC}"
fi

echo -e "Reason: $REASON"
echo "==========================================\n"
