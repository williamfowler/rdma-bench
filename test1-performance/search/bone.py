# MIT License

# Copyright (c) 2021 ByteDance Inc. All rights reserved.
# Copyright (c) 2021 Duke University. All rights reserved.

# See LICENSE for license information


# Bone is what Collie likes! (Say, the anomaly of the RDMA NIC)

import subprocess


class BaseBoneMon(object):
    '''
        BoneMon should collects throughput and pause framesor any other anomaly signal.
        @bps_bar: the throughput threshold in bits per sec. (float)
        @pps_bar: the throughput threshold in pkts per sec. (float)
        @intf: the interface BoneMon to monitor. (str)

    '''

    def __init__(self, bps_bar, pps_bar):
        super(BaseBoneMon, self).__init__()
        self._bps_bar = float(bps_bar)
        self._pps_bar = float(pps_bar)

    def monitor(self, intf):
        raise NotImplementedError

    def check_bone(self, intf):
        raise NotImplementedError


class MlnxBoneMon(BaseBoneMon):
    '''
        Mellanox Bone Monitor
    '''

    def __init__(self, bps_bar, pps_bar, tx_pfc_bar, rx_pfc_bar):
        super(MlnxBoneMon, self).__init__(bps_bar, pps_bar)
        self._tx_pfc_bar = float(tx_pfc_bar)
        self._rx_pfc_bar = float(rx_pfc_bar)
        self._metrics = [
            # bits per second (TX)      in Mbps
            "tx_vport_rdma_unicast_bytes",
            # bits per second (RX)      in Mbps
            "rx_vport_rdma_unicast_bytes",
            # packets per second (TX)   in pps
            "tx_vport_rdma_unicast_packets",
            # packets per second (RX)   in pps
            "rx_vport_rdma_unicast_packets",
            "tx_prio3_pause_duration",  # pfc duration per second (TX)  in us
            "rx_prio3_pause_duration"   # pfc duration per second (RX)  in us
        ]

    def monitor(self, intf):
        result = {key: 0.0 for key in self._metrics}
        cmd = "mlnx_perf -i {} -c 1".format(intf)
        try:
            output = subprocess.check_output(cmd, shell=True)
        except Exception as e:
            print(e)
            return {key: -1.0 for key in self._metrics}
        output = output.decode().split('\n')
        for line in output:
            for metric in self._metrics:
                if metric in line:
                    line = line.strip(' ').split(' ')
                    if "bytes" in metric:
                        result[metric] = float(
                            line[-2].replace(',', '')) / 1000.0
                    else:
                        result[metric] = float(line[-1].replace(',', ''))
        return result

    def check_bone(self, result):
        # First, we check pause duration
        if (result["tx_prio3_pause_duration"] > self._tx_pfc_bar or
                result["rx_prio3_pause_duration"] > self._rx_pfc_bar):
            return -1

        if (result["tx_vport_rdma_unicast_bytes"] < self._bps_bar and
                result["rx_vport_rdma_unicast_bytes"] < self._bps_bar):
            # bps does not achieve, chk with pps
            # However, pps_bar is pretty hard to set accurately.
            # In production, we chk with bps for most scenarios.
            if (result["tx_vport_rdma_unicast_packets"] < self._pps_bar and
                    result["rx_vport_rdma_unicast_packets"] < self._pps_bar):
                return -2
        return 0

    # HW_TS_LATENCY: Collect hardware timestamp latency statistics
    def _read_latency_file(self, content):
        """Parse latency stats from file content"""
        latency_stats = {
            "latency_samples": 0,
            "latency_min_ns": None,
            "latency_avg_ns": None,
            "latency_median_ns": None,
            "latency_p95_ns": None,
            "latency_p99_ns": None,
            "latency_max_ns": None
        }

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

    def collect_hw_latency_stats(self, username=None, iplist=None):
        """
        Read latency statistics from /tmp/collie_hw_latency_stats.txt
        If iplist provided, collect from all machines via SSH and combine
        Returns dict with latency metrics in same format as other metrics
        """
        all_stats = []

        # Collect from local machine
        filename = "/tmp/collie_hw_latency_stats.txt"
        try:
            with open(filename, 'r') as f:
                content = f.read()
                stats = self._read_latency_file(content)
                if stats["latency_samples"] > 0:
                    all_stats.append(stats)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Error reading local latency stats: {e}")

        # Collect from remote machines via SSH
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
                    print(f"Error reading latency stats from {ip}: {e}")

        # Combine stats from all machines
        if not all_stats:
            return {
                "latency_samples": 0,
                "latency_min_ns": None,
                "latency_avg_ns": None,
                "latency_median_ns": None,
                "latency_p95_ns": None,
                "latency_p99_ns": None,
                "latency_max_ns": None
            }

        # Average the statistics across all machines
        combined = {
            "latency_samples": sum(s["latency_samples"] for s in all_stats),
            "latency_min_ns": min(s["latency_min_ns"] for s in all_stats if s["latency_min_ns"]),
            "latency_avg_ns": sum(s["latency_avg_ns"] for s in all_stats if s["latency_avg_ns"]) / len([s for s in all_stats if s["latency_avg_ns"]]),
            "latency_median_ns": sum(s["latency_median_ns"] for s in all_stats if s["latency_median_ns"]) / len([s for s in all_stats if s["latency_median_ns"]]),
            "latency_p95_ns": max(s["latency_p95_ns"] for s in all_stats if s["latency_p95_ns"]),
            "latency_p99_ns": max(s["latency_p99_ns"] for s in all_stats if s["latency_p99_ns"]),
            "latency_max_ns": max(s["latency_max_ns"] for s in all_stats if s["latency_max_ns"])
        }

        return combined
