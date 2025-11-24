#!/bin/bash
# Quick diagnostic script to check latency files on both machines

echo "=== Checking /tmp/collie_hw_latency_stats.txt on both machines ==="
echo ""

for ip in 192.168.100.1 192.168.100.2; do
    echo "--- Machine: $ip ---"
    ssh wfowler@$ip "ls -lh /tmp/collie_hw_latency_stats.txt 2>/dev/null && echo 'File exists!' && cat /tmp/collie_hw_latency_stats.txt || echo 'File NOT found'"
    echo ""
done

echo "=== Checking if collie_engine processes are running ==="
for ip in 192.168.100.1 192.168.100.2; do
    echo "--- Machine: $ip ---"
    ssh wfowler@$ip "ps aux | grep collie_engine | grep -v grep || echo 'No processes running'"
    echo ""
done
