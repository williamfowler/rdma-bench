# RDMA-Bench Project Guide

## Project Overview

This is a class project using the **Collie framework** for automated RDMA performance testing and anomaly detection. Collie is based on the NSDI'22 paper "Collie: Finding Performance Anomalies in RDMA Subsystems" and uses simulated annealing to explore RDMA parameter spaces to discover performance anomalies.

### Key Paper Insights
- **Paper**: nsdi22-paper-kong.pdf (included in repo)
- **Goal**: Automatically find RDMA performance anomalies in datacenter networks
- **Two types of anomalies**:
  - **PFC pause frame storms** (return -1): Critical anomalies that trigger MFS (Minimal Feature Set) computation
  - **Low throughput** (return -2): Performance degradation anomalies that are logged but do NOT trigger MFS
- **Success rate**: ~46% test success rate is NORMAL with aggressive parameter exploration

## Hardware Setup

- **NIC**: Mellanox ConnectX-5 (CX-5), 25 GbE
- **Interface name**: `enp94s0f0np0` (used for mlnx_perf monitoring)
- **GPUs**: NVIDIA P100s available (not currently used by default workloads)
- **Nodes**:
  - Node A: 192.168.100.2 (username: wfowler)
  - Node B: 192.168.100.1 (username: wfowler)

## Directory Structure

```
rdma-bench/
├── test0-basic/                    # Basic RDMA tests
│   └── scripts/config_check.py     # Configuration validation
├── test1-performance/              # Main Collie framework
│   ├── example2.json               # Configuration file for tests
│   ├── search/
│   │   ├── collie.py              # Main entry point
│   │   ├── anneal.py              # Simulated annealing + logging
│   │   ├── bone.py                # Anomaly detection logic
│   │   ├── engine.py              # Workload execution
│   │   ├── space.py               # Parameter space definition
│   │   └── mfs.py                 # Minimal Feature Set computation
│   ├── traffic_engine/
│   │   ├── collie_engine          # Binary for running traffic
│   │   └── helper.hpp             # C++ helper code
│   └── logs/                      # Test results (gitignored)
│       ├── result/                # Performance metrics per test
│       ├── reproduce/             # Scripts to reproduce tests
│       └── mfs/                   # MFS results (if any)
└── nsdi22-paper-kong.pdf          # Reference paper
```

## Key Configuration: example2.json

```json
{
  "username": "wfowler",
  "iplist": ["192.168.100.2", "192.168.100.1"],
  "logpath": "./logs",
  "engine": "/users/wfowler/rdma-bench/test1-performance/traffic_engine/collie_engine",
  "iters": 1000,
  "bars": {
    "tx_pfc_bar": 0,
    "rx_pfc_bar": 0,
    "bps_bar": 90.0,
    "pps_bar": 4000000.0
  }
}
```

### Configuration Fields
- **iters**: Total number of test attempts (NOT retries per test)
- **bars**: Anomaly detection thresholds
  - `tx_pfc_bar` / `rx_pfc_bar`: PFC pause frame duration thresholds (0 = any pause triggers anomaly)
  - `bps_bar`: Throughput threshold in Gbps (90.0 Gbps)
  - `pps_bar`: Packets per second threshold (4M pps)

### IMPORTANT: Threshold Semantics
- **PFC thresholds**: Values ABOVE threshold = anomaly
- **Throughput thresholds**: Values BELOW threshold = anomaly
- Current thresholds (90 Gbps, 4M pps) are set HIGH to specifically look for PFC anomalies
- For 25 GbE hardware, these throughput thresholds are intentionally impossible to meet

## Running Tests

```bash
cd test1-performance
python3 search/collie.py --config example2.json
```

### What Happens During a Test
1. Collie generates random RDMA traffic parameters (QP types, message sizes, batch sizes, etc.)
2. Sets up traffic on both nodes using `collie_engine` binary
3. Monitors performance counters via `mlnx_perf` on interface `enp94s0f0np0`
4. Checks for anomalies using thresholds in `bars`
5. Logs results to `logs/result/N` and reproduction scripts to `logs/reproduce/N`
6. If PFC anomaly detected (return -1), computes MFS to find minimal triggering conditions

## Understanding Results

### Result Files (logs/result/N)

Each result file contains:
```
{"Traffics": [...]}         # Traffic configuration (JSON)

is_anomaly: false           # NEW: Anomaly status field

tx_vport_rdma_unicast_bytes:  19.64629
rx_vport_rdma_unicast_bytes:  38.52323
tx_vport_rdma_unicast_packets:  1198330.0
rx_vport_rdma_unicast_packets:  1385207.0
tx_prio3_pause_duration:  0.0
rx_prio3_pause_duration:  0.0
```

### Traffic Configuration Fields
- **qp_type**: RC (Reliable Connection), UC (Unreliable Connection), UD (Unreliable Datagram)
- **msg_size**: Message size in bytes
- **recv_batch** / **send_batch**: Batching parameters (0 = no batching, suggests CPU-only mode)
- **qp_num**: Number of queue pairs
- **burst**: Burst size

### Performance Metrics
- **tx_vport_rdma_unicast_bytes** / **rx_vport_rdma_unicast_bytes**: Throughput in Gbps
- **tx_vport_rdma_unicast_packets** / **rx_vport_rdma_unicast_packets**: Packets per second
- **tx_prio3_pause_duration** / **rx_prio3_pause_duration**: PFC pause frame duration

## Anomaly Detection Logic

Located in `test1-performance/search/bone.py` (lines 77-91):

```python
def check_bone(self, result):
    # First, check pause duration (PFC anomaly)
    if (result["tx_prio3_pause_duration"] > self._tx_pfc_bar or
            result["rx_prio3_pause_duration"] > self._rx_pfc_bar):
        return -1  # PFC anomaly - triggers MFS computation

    # Check throughput (low throughput anomaly)
    if (result["tx_vport_rdma_unicast_bytes"] < self._bps_bar and
            result["rx_vport_rdma_unicast_bytes"] < self._bps_bar):
        if (result["tx_vport_rdma_unicast_packets"] < self._pps_bar and
                result["rx_vport_rdma_unicast_packets"] < self._pps_bar):
            return -2  # Throughput anomaly - NO MFS, just logging
    return 0  # No anomaly
```

## Common Issues and Fixes

### 1. Process Cleanup Issues
**Problem**: Old processes not cleaned up between tests
**Fix**: Added `sudo` to killall commands in engine.py
```python
os.system("sudo killall -9 collie_engine")
```

### 2. mlnx_perf Monitoring
**Problem**: Wrong interface name causing monitoring failures
**Fix**: Changed interface to `enp94s0f0np0` in engine.py (line ~XX)

### 3. Zero Anomalies Found
**Symptom**: All tests pass, no anomalies detected
**Likely causes**:
- Thresholds set incorrectly (too permissive)
- For PFC: Check if `tx_pfc_bar` and `rx_pfc_bar` are set to 0
- For throughput: Check if `bps_bar` and `pps_bar` match hardware capabilities

### 4. No MFS Computation
**Symptom**: "Print Anomalous point X" messages appear but no MFS is computed
**This is NORMAL**: MFS only computes for PFC anomalies (return -1), not throughput anomalies (return -2)

### 5. Test Success Rate ~46%
**This is NORMAL**: Collie aggressively explores parameter space, many combinations fail to set up correctly

## Recent Modifications

### Added is_anomaly Field (Pending)
The `is_anomaly` field has been added to result files to make it easier to identify anomalous tests without manually checking thresholds.

**Changes required in `test1-performance/search/anneal.py`:**

1. **Modified `log_result()` function** (line 34): Added `anomaly_status` parameter
2. **Updated `simulated_annealing()`** (line ~303): Moved `check_bone()` call before logging
3. **Updated `random()`** (line ~256): Moved `check_bone()` call before logging
4. **Updated other calls**: All `log_result()` calls now include anomaly status

The field shows:
- `is_anomaly: false` - No anomaly detected
- `is_anomaly: true (PFC pause frame anomaly)` - PFC storm detected
- `is_anomaly: true (low throughput anomaly)` - Throughput below thresholds

## Potential Research Directions

1. **Characterize "success space"**: What parameter combinations achieve high throughput without anomalies?
2. **GPU workload testing**: Enable GPU-Direct RDMA (check recv_batch/send_batch parameters)
3. **Threshold sensitivity analysis**: How do different thresholds affect anomaly discovery?
4. **QP type performance**: Compare RC vs UC vs UD performance characteristics
5. **Algorithm efficiency**: Can simulated annealing be improved? Compare to random search
6. **MFS validation**: Are computed MFS actually minimal? Can they be simplified?
7. **Reproducibility study**: Do reproduction scripts always trigger the same anomalies?
8. **Parameter correlation**: Which parameters most strongly correlate with anomalies?

## Useful Commands

### View Test Results
```bash
# Count total tests
ls logs/result/ | wc -l

# Count anomalies (after is_anomaly field added)
grep -r "is_anomaly: true" logs/result/ | wc -l

# View specific test
cat logs/result/1
```

### Clean Logs
```bash
rm -rf logs/result/* logs/reproduce/* logs/mfs/*
```

### Check Running Processes
```bash
ps aux | grep collie_engine
sudo killall -9 collie_engine
```

### Monitor NIC Counters
```bash
mlnx_perf -i enp94s0f0np0
```

## Git Status

Current branch: `main`

Modified files:
- `test0-basic/scripts/config_check.py`
- `test1-performance/search/collie.py`
- `test1-performance/search/engine.py`
- `test1-performance/search/space.py`
- `test1-performance/traffic_engine/helper.hpp`

Untracked:
- `.vscode/` (editor config)
- `test1-performance/logs/` (test results - gitignored)
- `test1-performance/clean.sh` (utility script)
- Various pycache directories

## Notes for Future Sessions

- The user is working on this for a **class project**
- Focus is on understanding the framework and running interesting experiments
- Don't be overly cautious - this is a research/learning environment
- When suggesting changes, provide clear lists before implementing
- The remote machine has the actual hardware, local copy is for code editing
- Always check if changes need to be synced to remote before running tests
