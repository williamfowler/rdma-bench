# RDMA-Bench Latency Recording System Architecture

## Overview

This document explains how the Collie framework records RDMA operation latencies, from the low-level C++ traffic engine measurements to the Python-based log aggregation. The latency recording system measures **end-to-end software latency** for RDMA operations from the time a work request is posted until its completion is detected.

---

## Architecture Summary

The latency recording system uses a **software-based timing approach** with **file-based inter-process communication**:

1. **C++ Traffic Engine** (`collie_engine` binary) captures timestamps at send and completion
2. **Aggregates statistics** (min, avg, median, p95, p99, max) every 10 samples
3. **Writes to temp file** (`/tmp/collie_hw_latency_stats.txt`)
4. **Python framework** reads from temp files (local + SSH remote)
5. **Combines multi-machine statistics** into final result
6. **Logs to result files** in `logs/result/N`

---

## Component 1: Traffic Engine Latency Recording (C++)

### Key Files

- **`context.cpp`** (lines 811-830, 989-1036): Core latency calculation and file writing
- **`endpoint.cpp`** (lines 88-103): Timestamp capture at send time
- **`endpoint.hpp`** (line 56): Timestamp queue storage
- **`helper.hpp/cpp`** (lines 75-85, 50-51): Timing functions and flags

### How It Works

#### Step 1: Initialization (context.cpp lines 144-156)

When `--hw_ts` flag is enabled via command line:
```cpp
if (FLAGS_hw_ts) {
    // Query device for hardware timestamp capability
    ibv_query_device_ex(context_, nullptr, &dattr);
    // Initialize timestamp support (though currently using software clock)
}
```

**Command construction** (`engine.py` lines 66-68):
```python
server_cmd = "... {} --hw_ts ...".format(self._binary)
```

#### Step 2: Send Timestamp Capture (endpoint.cpp lines 88-103)

When posting RDMA operations:
```cpp
uint64_t send_timestamp = 0;
if (FLAGS_hw_ts) {
    send_timestamp = Now64Ns();  // Software clock: CLOCK_REALTIME in nanoseconds
}

// Post the actual RDMA work request
if (ibv_post_send(qp_, wr_list, &bad_wr)) {
    PLOG(ERROR) << "ibv_post_send() failed";
    return -1;
}

// Store timestamp in FIFO queue for later retrieval
if (FLAGS_hw_ts && send_timestamp != 0) {
    send_timestamps_.push(send_timestamp);
}
```

**Key detail**: Only **one timestamp per batch** is captured, corresponding to the last (signaled) work request in the batch.

#### Step 3: Completion and Latency Calculation (context.cpp lines 813-828)

When completion queue is polled:
```cpp
void rdma_context::ParseEachEx(struct ibv_cq_ex *cq_ex) {
    // Read operation type from completion queue entry
    auto opcode = cq_ex->read_opcode(cq_ex);

    // Only measure send operations (not receives)
    if (opcode == IBV_WC_RDMA_WRITE || opcode == IBV_WC_RDMA_READ || opcode == IBV_WC_SEND) {
        // Retrieve the stored send timestamp
        uint64_t send_timestamp = ep->PopSendTimestamp();

        if (send_timestamp > 0) {
            // Capture completion time
            uint64_t completion_timestamp = Now64Ns();

            // Calculate latency: completion - send
            uint64_t rdma_latency = completion_timestamp - send_timestamp;

            // Store in vector for batch processing
            nic_process_time_.push_back(rdma_latency);
        }
    }

    // Write stats every 10 samples (frequent output for continuous monitoring)
    if (nic_process_time_.size() >= 10) {
        WriteLatencyStatsToFile();
        nic_process_time_.clear();
    }
}
```

**What this measures**: Time from `ibv_post_send()` call to completion event detection, including:
- Queue processing time
- Network transmission time
- Remote processing time (for RDMA_WRITE/RDMA_READ)
- Completion queue polling detection time

#### Step 4: Statistics Aggregation and File Output (context.cpp lines 989-1036)

Every 10+ samples:
```cpp
void rdma_context::WriteLatencyStatsToFile() {
    if (nic_process_time_.empty()) return;

    // Sort for percentile calculation
    std::sort(nic_process_time_.begin(), nic_process_time_.end());

    size_t size = nic_process_time_.size();

    // Calculate statistics
    uint64_t min_lat = nic_process_time_[0];
    uint64_t median_lat = nic_process_time_[size / 2];
    uint64_t p95_lat = nic_process_time_[(size_t)(size * 0.95)];
    uint64_t p99_lat = nic_process_time_[(size_t)(size * 0.99)];
    uint64_t max_lat = nic_process_time_[size - 1];

    uint64_t sum = 0;
    for (auto lat : nic_process_time_) {
        sum += lat;
    }
    uint64_t avg_lat = sum / size;

    // Write to temp file (OVERWRITES previous content)
    std::string filename = "/tmp/collie_hw_latency_stats.txt";
    std::ofstream out(filename);

    out << "latency_samples: " << size << "\n";
    out << "latency_min_ns: " << min_lat << "\n";
    out << "latency_avg_ns: " << avg_lat << "\n";
    out << "latency_median_ns: " << median_lat << "\n";
    out << "latency_p95_ns: " << p95_lat << "\n";
    out << "latency_p99_ns: " << p99_lat << "\n";
    out << "latency_max_ns: " << max_lat << "\n";

    out.close();
}
```

**Output format** (key-value pairs):
```
latency_samples: 15
latency_min_ns: 1234567
latency_avg_ns: 2345678
latency_median_ns: 2341234
latency_p95_ns: 3123456
latency_p99_ns: 3987654
latency_max_ns: 4123456
```

### Timing Mechanism Details

**Function**: `Now64Ns()` in `helper.cpp` (lines 81-85)
```cpp
uint64_t Now64Ns() {
    struct timespec tv;
    clock_gettime(CLOCK_REALTIME, &tv);
    return (uint64_t)tv.tv_sec * 1000000000llu + (uint64_t)tv.tv_nsec;
}
```

- **Clock source**: Software `CLOCK_REALTIME` (not hardware NIC timestamps)
- **Resolution**: Nanoseconds (1 billion ticks per second)
- **Why software**: Ensures consistent timing across both send and completion sides

### Important Implementation Notes

1. **Hardware timestamps read but not used** (lines 794-809): The code reads hardware timestamps from the NIC via `ibv_wc_read_completion_ts()`, but ultimately uses software clock for actual latency calculation

2. **One timestamp per batch**: Only the last (signaled) work request in each batch gets a timestamp

3. **Send operations only**: RDMA_WRITE, RDMA_READ, and SEND are measured; receives are excluded

4. **File overwrite behavior**: Each write overwrites the previous temp file content (not append)

5. **Frequent updates**: Statistics written every 10 samples (very frequent for continuous monitoring)

---

## Component 2: Python Latency Collection (bone.py)

### Key Functions

- **`collect_hw_latency_stats()`** (lines 119-190): Main entry point for latency collection
- **`_read_latency_file()`** (lines 94-117): Parser for temp file content

### Collection Flow

#### Step 1: Local File Read
```python
def collect_hw_latency_stats(self, username=None, iplist=None):
    all_stats = []

    # Read from local machine
    filename = "/tmp/collie_hw_latency_stats.txt"
    try:
        with open(filename, 'r') as f:
            content = f.read()
            stats = self._read_latency_file(content)
            if stats["latency_samples"] > 0:
                all_stats.append(stats)
    except FileNotFoundError:
        # No latency data available locally
        pass
```

#### Step 2: Remote File Collection via SSH
```python
    # Collect from each remote machine
    if username and iplist:
        for ip in iplist:
            try:
                cmd = f"ssh {username}@{ip} 'cat /tmp/collie_hw_latency_stats.txt 2>/dev/null || echo'"
                result = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL)
                content = result.decode()

                if content.strip():
                    stats = self._read_latency_file(content)
                    if stats["latency_samples"] > 0:
                        all_stats.append(stats)
            except Exception as e:
                # Handle SSH failures gracefully
                pass
```

#### Step 3: Parsing (lines 94-117)
```python
def _read_latency_file(self, content):
    latency_stats = {
        "latency_samples": 0,
        "latency_min_ns": None,
        "latency_avg_ns": None,
        "latency_median_ns": None,
        "latency_p95_ns": None,
        "latency_p99_ns": None,
        "latency_max_ns": None
    }

    # Parse each line (key: value format)
    for line in content.split('\n'):
        line = line.strip()
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            if key in latency_stats:
                if key == "latency_samples":
                    latency_stats[key] = int(value)
                else:
                    latency_stats[key] = float(value)

    return latency_stats
```

#### Step 4: Multi-Machine Aggregation (lines 164-190)
```python
    # Combine statistics from all machines
    if not all_stats:
        return {/* empty dict with None values */}

    combined = {
        "latency_samples": sum(s["latency_samples"] for s in all_stats),
        "latency_min_ns": min(s["latency_min_ns"] for s in all_stats if s["latency_min_ns"]),
        "latency_avg_ns": sum(s["latency_avg_ns"] for s in all_stats if s["latency_avg_ns"]) / len([...]),
        "latency_median_ns": sum(s["latency_median_ns"] for s in all_stats if s["latency_median_ns"]) / len([...]),
        "latency_p95_ns": max(s["latency_p95_ns"] for s in all_stats if s["latency_p95_ns"]),
        "latency_p99_ns": max(s["latency_p99_ns"] for s in all_stats if s["latency_p99_ns"]),
        "latency_max_ns": max(s["latency_max_ns"] for s in all_stats if s["latency_max_ns"])
    }

    return combined
```

**Aggregation logic**:
- `samples`: Sum across all machines
- `min`: Minimum across all machines
- `avg`, `median`: Average of machine averages/medians
- `p95`, `p99`, `max`: Maximum across all machines

---

## Component 3: Result Logging (anneal.py)

### Integration Points

The `log_result()` function (lines 34-55) receives latency stats and writes to result files:

```python
def log_result(path: str, point: Point, bone_results, hw_results, latency_stats=None):
    with open(path, "w") as f:
        # Write traffic configuration (JSON)
        logs = point.log_to_dict()
        json.dump(logs, f)
        f.write('\n\n')

        # Write hardware performance counters
        for key in hw_results:
            f.write("{}:  {}\n".format(key, hw_results[key]))
        f.write('\n')

        # Write anomaly detection results
        for key in bone_results:
            f.write("{}:  {}\n".format(key, bone_results[key]))
        f.write('\n')

        # Write latency statistics if available
        if latency_stats and latency_stats.get("latency_samples", 0) > 0:
            for key in latency_stats:
                f.write("{}:  {}\n".format(key, latency_stats[key]))
            f.write('\n')
```

### Called From Three Search Methods

1. **Random Search** (line 273-276):
```python
latency_stats = self._bonemon.collect_hw_latency_stats(
    username=self._username, iplist=self._iplist)
log_result(self._log_path + "result/{}".format(self._global_log_idx),
           point, bone_results, hw_results, latency_stats)
```

2. **Simulated Annealing** (line 323-326):
```python
latency_stats = self._bonemon.collect_hw_latency_stats(
    username=self._username, iplist=self._iplist)
log_result(self._log_path + "result/{}".format(self._global_log_idx),
           point, bone_results, hw_results, latency_stats)
```

3. **Sample Method** (line 239-242):
```python
latency_stats = self._bonemon.collect_hw_latency_stats(
    username=self._username, iplist=self._iplist)
log_result(self._log_path + "result/{}".format(self._global_log_idx),
           point, bone_results, hw_results, latency_stats)
```

---

## Data Flow: Traffic Commands → Latency Stats

### Complete Pipeline

```
1. Python anneal.py generates traffic parameters
   ↓
   Example point: {qp_type: "RC", msg_size: 8192, send_batch: 32, ...}

2. engine.py constructs collie_engine command
   ↓
   Command: "collie_engine --qp_type=RC --msg_size=8192 --send_batch=32 --hw_ts ..."

3. C++ collie_engine binary executes traffic
   ↓
   ClientDatapath() loop:
   - PostSend() with traffic parameters
   - Captures send_timestamp via Now64Ns()
   - Stores in send_timestamps_ queue

4. RDMA operations execute
   ↓
   - Work requests posted to NIC
   - Network transmission
   - Remote processing (if applicable)
   - Completion events generated

5. Completion queue polling
   ↓
   ParseEachEx():
   - Retrieves send_timestamp from queue
   - Captures completion_timestamp via Now64Ns()
   - Calculates: latency = completion_timestamp - send_timestamp
   - Stores in nic_process_time_ vector

6. Statistics aggregation (every 10 samples)
   ↓
   WriteLatencyStatsToFile():
   - Sorts latency samples
   - Calculates min, avg, median, p95, p99, max
   - Writes to /tmp/collie_hw_latency_stats.txt

7. Python collection
   ↓
   bone.py collect_hw_latency_stats():
   - Reads local temp file
   - SSH reads from remote machines
   - Combines statistics across machines

8. Result logging
   ↓
   anneal.py log_result():
   - Writes to logs/result/N
   - Includes traffic config + performance metrics + latency stats

9. Final result file
   ↓
   logs/result/N contains:
   {"Traffics": [...]}
   tx_vport_rdma_unicast_bytes: ...
   latency_samples: 30
   latency_min_ns: 15642778.0
   latency_avg_ns: 142739395.66666666
   ...
```

### Relationship Between Traffic Parameters and Latency

**Traffic parameters that directly affect latency**:

1. **Message size** (`msg_size`): Larger messages → higher latency
   - More data to transmit
   - More processing at sender/receiver
   - Example: 64B vs 8192B messages

2. **QP type** (`qp_type`): RC vs UC vs UD
   - RC (Reliable Connection): ACKs required → higher latency
   - UC (Unreliable Connection): No ACKs → lower latency
   - UD (Unreliable Datagram): Different processing path

3. **Batch size** (`send_batch`, `recv_batch`): Affects measured latency
   - Larger batches → amortized latency per batch
   - Note: Only one timestamp per batch (last signaled WR)

4. **Number of QPs** (`qp_num`): Contention and parallelism
   - More QPs → potential contention for NIC resources
   - Could increase latency variability

5. **Burst size** (`burst`): Traffic pattern
   - Affects network congestion
   - Can trigger PFC pauses → extreme latency spikes

**Example from logs**:

Result #300 (low latency):
```
qp_type: RC, msg_size: 1024, send_batch: 16
latency_min_ns: 3.3 ms (lowest observed)
latency_avg_ns: 85.5 ms
```

Result #5 (higher latency):
```
qp_type: RC, msg_size: 8192, send_batch: 64
latency_min_ns: 26.7 ms
latency_avg_ns: 280.6 ms
```

---

## Example Result File Format

```
{"Traffics": [{"workload_type": "bidirection", "server": {...}, "client": {...}}]}

tx_vport_rdma_unicast_bytes:  19.64629
rx_vport_rdma_unicast_bytes:  38.52323
tx_vport_rdma_unicast_packets:  1198330.0
rx_vport_rdma_unicast_packets:  1385207.0
tx_prio3_pause_duration:  0.0
rx_prio3_pause_duration:  0.0

latency_samples:  30
latency_min_ns:  15642778.0
latency_avg_ns:  142739395.66666666
latency_median_ns:  142834680.66666666
latency_p95_ns:  206864349.0
latency_p99_ns:  206864349.0
latency_max_ns:  206864349.0
```

### Latency Units and Interpretation

- **All latency values in nanoseconds**
- **Typical range**: 3-545 milliseconds (3,000,000 - 545,000,000 ns)
- **Sample count**: Usually 30 samples per test (3 batches of 10)

**Conversion reference**:
- 1 millisecond = 1,000,000 nanoseconds
- 15,642,778 ns = ~15.6 ms
- 142,739,395 ns = ~142.7 ms

---

## Key Insights

### 1. Software-Based Measurement
Despite the name "hw_ts" (hardware timestamp), the current implementation uses **software CLOCK_REALTIME** for both send and completion timestamps. This ensures:
- Consistent timing methodology
- No hardware clock conversion issues
- Portability across different NICs

### 2. End-to-End Latency
The measured latency includes:
- Software queue processing time
- NIC processing time
- Network transmission time
- Remote processing time (for RDMA operations)
- Completion polling detection time

This is **real-world application latency**, not just network wire time.

### 3. Batch-Level Granularity
Latency is measured per batch, not per individual operation:
- One timestamp per batch of work requests
- Represents the time for the entire batch to complete
- More efficient than per-operation timing

### 4. Continuous Monitoring
The system provides **near-real-time latency monitoring**:
- Statistics updated every 10 samples
- Temp file overwritten frequently
- Python reads after each test completes

### 5. Multi-Machine Support
In distributed tests:
- Each machine independently measures latency
- Statistics collected via SSH
- Combined using appropriate aggregation (sum/min/avg/max)

---

## Summary

The RDMA-Bench latency recording system provides comprehensive end-to-end latency measurements for RDMA operations:

1. **Captures**: Software timestamps at send and completion
2. **Aggregates**: Min, avg, median, p95, p99, max statistics
3. **Communicates**: Via temp files (`/tmp/collie_hw_latency_stats.txt`)
4. **Collects**: From multiple machines via SSH
5. **Logs**: To result files alongside performance metrics

The latency stats directly reflect the traffic parameters being tested, enabling correlation between RDMA configuration and observed latency behavior. This is particularly valuable for identifying anomalous conditions where latency spikes significantly (e.g., due to PFC pause frames or resource contention).
