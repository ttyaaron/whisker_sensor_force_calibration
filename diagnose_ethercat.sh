#!/usr/bin/env bash
set -euo pipefail

echo "=========================================="
echo "EtherCAT Device Diagnostic Tool"
echo "=========================================="
echo ""

echo "1. Checking for EtherCAT kernel modules..."
if lsmod | grep -q ethercat; then
    echo "   ✓ EtherCAT modules loaded:"
    lsmod | grep ethercat
else
    echo "   ✗ No EtherCAT kernel modules found"
    echo "   (bota_driver uses its own userspace EtherCAT implementation)"
fi
echo ""

echo "2. Checking wired network interfaces..."
for iface in enp14s0 enxa0cec874e0d6; do
    if [[ -d "/sys/class/net/$iface" ]]; then
        echo "   Interface: $iface"
        carrier=$(cat /sys/class/net/$iface/carrier 2>/dev/null || echo "unknown")
        operstate=$(cat /sys/class/net/$iface/operstate 2>/dev/null || echo "unknown")
        speed=$(cat /sys/class/net/$iface/speed 2>/dev/null || echo "unknown")
        echo "      Carrier: $carrier (1=link up)"
        echo "      State: $operstate"
        echo "      Speed: ${speed} Mbps"
        
        if command -v ip >/dev/null 2>&1; then
            ips=$(ip -4 -br addr show "$iface" 2>/dev/null | awk '{print $3}' || echo "none")
            echo "      IPv4: $ips"
        fi
    fi
done
echo ""

echo "3. Scanning for EtherCAT traffic (5 sec per interface)..."
for iface in enp14s0 enxa0cec874e0d6; do
    if [[ -d "/sys/class/net/$iface" ]]; then
        echo "   Scanning $iface..."
        if command -v tcpdump >/dev/null 2>&1; then
            result=$(sudo timeout 5 tcpdump -i "$iface" -nn -c 3 'ether proto 0x88a4' 2>&1 || true)
            if echo "$result" | grep -q "captured"; then
                packet_count=$(echo "$result" | grep "captured" | awk '{print $1}')
                echo "      ✓ EtherCAT frames detected: $packet_count packets"
            else
                echo "      ✗ No EtherCAT frames detected (sensor may not be connected or powered)"
            fi
        else
            echo "      ✗ tcpdump not available"
        fi
    fi
done
echo ""

echo "4. Summary:"
echo "   - If 'No EtherCAT frames detected' on all interfaces:"
echo "     → Sensor is not visible on the EtherCAT bus"
echo "     → Check: sensor power, cable connection, correct NIC port"
echo "   - If 'EtherCAT frames detected':"
echo "     → Sensor is transmitting, driver issue or config problem"
echo ""
echo "=========================================="
