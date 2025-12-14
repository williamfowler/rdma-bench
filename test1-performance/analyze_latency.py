#!/usr/bin/env python3
"""
Analyze RDMA latency logs and create scatter plots showing the relationship
between different traffic parameters and median latency.
"""

import json
import os
import glob
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict

def parse_log_file(filepath):
    """Parse a single log file and extract relevant parameters and latency."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()

        # Split into sections
        lines = content.strip().split('\n')

        # Parse JSON traffic configuration (first line)
        try:
            traffic_json = json.loads(lines[0])
        except json.JSONDecodeError:
            return None

        # Extract latency_median_ns from the file
        latency_median = None
        for line in lines:
            if 'latency_median_ns:' in line:
                try:
                    latency_median = float(line.split(':')[1].strip())
                except (ValueError, IndexError):
                    pass

        # Skip if no latency data
        if latency_median is None:
            return None

        # Extract parameters from traffic configuration
        # Note: There can be multiple traffics, we'll use the first one
        if not traffic_json.get('Traffics'):
            return None

        traffic = traffic_json['Traffics'][0]

        # Extract parameters
        data = {
            'latency_median_ns': latency_median,
            'mtu': traffic.get('mtu'),
            'qp_type': traffic.get('qp_type'),
            'qp_num': traffic.get('qp_num'),
            'process_num': traffic.get('process_num'),
        }

        # For wq_depth, send_batch, recv_batch: take max of client/server
        # (one is typically 0, we want the active value)
        client = traffic.get('client', {})
        server = traffic.get('server', {})

        data['wq_depth'] = max(
            client.get('send_wq_depth', 0),
            client.get('recv_wq_depth', 0),
            server.get('send_wq_depth', 0),
            server.get('recv_wq_depth', 0)
        )

        data['send_batch'] = max(
            client.get('send_batch', 0),
            server.get('send_batch', 0)
        )

        data['recv_batch'] = max(
            client.get('recv_batch', 0),
            server.get('recv_batch', 0)
        )

        # For buf_num, mr_num, buf_size: take max of client/server
        data['buf_num'] = max(
            client.get('buf_num', 0),
            server.get('buf_num', 0)
        )

        data['mr_num'] = max(
            client.get('mr_num', 0),
            server.get('mr_num', 0)
        )

        data['buf_size'] = max(
            client.get('buf_size', 0),
            server.get('buf_size', 0)
        )

        return data

    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return None


def load_all_logs(log_dir):
    """Load all log files and extract data."""
    log_files = glob.glob(os.path.join(log_dir, '*'))

    all_data = []
    for log_file in log_files:
        data = parse_log_file(log_file)
        if data:
            all_data.append(data)

    print(f"Successfully parsed {len(all_data)} out of {len(log_files)} log files")
    return all_data


def bin_parameter(param_name, value):
    """Bin parameter values into ranges for better visualization."""
    if param_name in ['send_batch', 'recv_batch']:
        # Bin batch sizes
        if value == 0:
            return '0'
        elif value <= 16:
            return '1-16'
        elif value <= 32:
            return '17-32'
        elif value <= 64:
            return '33-64'
        else:
            return '65-127'
    elif param_name == 'wq_depth':
        # Bin work queue depth
        if value <= 100:
            return '0-100'
        elif value <= 250:
            return '101-250'
        elif value <= 500:
            return '251-500'
        elif value <= 750:
            return '501-750'
        else:
            return '751+'
    elif param_name == 'buf_size':
        # Bin buffer sizes (in bytes)
        if value <= 10000:
            return '0-10K'
        elif value <= 50000:
            return '10K-50K'
        elif value <= 100000:
            return '50K-100K'
        else:
            return '100K+'
    return str(value)


def create_scatter_plots(data, output_dir='./'):
    """Create 6 plots showing parameter vs median latency (box plots for categorical, binned box plots for continuous)."""

    # Parameters to plot
    params = ['mtu', 'wq_depth', 'qp_type', 'send_batch', 'recv_batch', 'qp_num']

    # Parameters that should use box plots (categorical/discrete with few values)
    box_plot_params = ['mtu', 'qp_type', 'qp_num']

    # Parameters that should use binned box plots (many unique values)
    binned_box_plot_params = ['send_batch', 'recv_batch', 'wq_depth']

    # Create figure with 2 rows, 3 columns
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    fig.suptitle('RDMA Parameter Effects on Median Latency', fontsize=16, fontweight='bold')

    axes = axes.flatten()

    for idx, param in enumerate(params):
        ax = axes[idx]

        # Organize data by parameter value for box plots (categorical or binned)
        if param in box_plot_params or param in binned_box_plot_params:
            # Group latencies by parameter value (or binned value)
            grouped_data = defaultdict(list)

            for d in data:
                if d.get(param) is not None and d.get('latency_median_ns') is not None:
                    x_val = d[param]
                    y_val = d['latency_median_ns'] / 1e6  # Convert to milliseconds

                    # Bin the value if this is a binned parameter
                    if param in binned_box_plot_params:
                        x_val = bin_parameter(param, x_val)

                    grouped_data[x_val].append(y_val)

            if not grouped_data:
                ax.text(0.5, 0.5, f'No data for {param}',
                       ha='center', va='center', transform=ax.transAxes)
                continue

            # Prepare data for box plot
            if param == 'qp_type':
                # For QP type, maintain order: RC, UC, UD
                qp_order = ['RC', 'UC', 'UD']
                labels = [qp for qp in qp_order if qp in grouped_data]
                box_data = [grouped_data[qp] for qp in labels]
            elif param in binned_box_plot_params:
                # For binned parameters, use predefined order
                if param in ['send_batch', 'recv_batch']:
                    bin_order = ['0', '1-16', '17-32', '33-64', '65-127']
                elif param == 'wq_depth':
                    bin_order = ['0-100', '101-250', '251-500', '501-750', '751+']
                labels = [b for b in bin_order if b in grouped_data]
                box_data = [grouped_data[b] for b in labels]
            else:
                # For numeric parameters, sort by value
                sorted_keys = sorted(grouped_data.keys())
                labels = [str(k) for k in sorted_keys]
                box_data = [grouped_data[k] for k in sorted_keys]

            # Create box plot
            bp = ax.boxplot(box_data, labels=labels, patch_artist=True,
                           showmeans=True, meanline=True,
                           boxprops=dict(facecolor='lightblue', alpha=0.7),
                           meanprops=dict(color='red', linewidth=2),
                           medianprops=dict(color='darkblue', linewidth=2))

            # Labeling
            ax.set_xlabel(param, fontsize=12, fontweight='bold')
            ax.set_ylabel('Median Latency (ms)', fontsize=12)
            total_samples = sum(len(vals) for vals in box_data)
            title_suffix = ' (binned)' if param in binned_box_plot_params else ''
            ax.set_title(f'{param} vs Median Latency{title_suffix}\n({total_samples} samples)', fontsize=11)
            ax.grid(True, alpha=0.3, axis='y')

            # Rotate x-labels if too many
            if len(labels) > 5:
                ax.tick_params(axis='x', rotation=45)

        else:
            # This shouldn't happen anymore, but keep as fallback
            ax.text(0.5, 0.5, f'No plotting strategy for {param}',
                   ha='center', va='center', transform=ax.transAxes)

    plt.tight_layout()

    # Save figure
    output_path = os.path.join(output_dir, 'latency_analysis.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved scatter plots to: {output_path}")

    # Also save individual plots for closer inspection
    for idx, param in enumerate(params):
        fig_single, ax_single = plt.subplots(1, 1, figsize=(10, 6))

        if param in box_plot_params or param in binned_box_plot_params:
            # Create box plot for categorical or binned parameters
            grouped_data = defaultdict(list)

            for d in data:
                if d.get(param) is not None and d.get('latency_median_ns') is not None:
                    x_val = d[param]
                    y_val = d['latency_median_ns'] / 1e6  # Convert to milliseconds

                    # Bin the value if this is a binned parameter
                    if param in binned_box_plot_params:
                        x_val = bin_parameter(param, x_val)

                    grouped_data[x_val].append(y_val)

            if grouped_data:
                # Prepare data for box plot
                if param == 'qp_type':
                    qp_order = ['RC', 'UC', 'UD']
                    labels = [qp for qp in qp_order if qp in grouped_data]
                    box_data = [grouped_data[qp] for qp in labels]
                elif param in binned_box_plot_params:
                    # For binned parameters, use predefined order
                    if param in ['send_batch', 'recv_batch']:
                        bin_order = ['0', '1-16', '17-32', '33-64', '65-127']
                    elif param == 'wq_depth':
                        bin_order = ['0-100', '101-250', '251-500', '501-750', '751+']
                    labels = [b for b in bin_order if b in grouped_data]
                    box_data = [grouped_data[b] for b in labels]
                else:
                    sorted_keys = sorted(grouped_data.keys())
                    labels = [str(k) for k in sorted_keys]
                    box_data = [grouped_data[k] for k in sorted_keys]

                # Create box plot
                bp = ax_single.boxplot(box_data, labels=labels, patch_artist=True,
                                      showmeans=True, meanline=True,
                                      boxprops=dict(facecolor='lightblue', alpha=0.7),
                                      meanprops=dict(color='red', linewidth=2, label='Mean'),
                                      medianprops=dict(color='darkblue', linewidth=2, label='Median'),
                                      flierprops=dict(marker='o', markerfacecolor='red', markersize=4, alpha=0.5))

                ax_single.set_xlabel(param, fontsize=14, fontweight='bold')
                ax_single.set_ylabel('Median Latency (ms)', fontsize=14)
                total_samples = sum(len(vals) for vals in box_data)
                title_suffix = ' (binned)' if param in binned_box_plot_params else ''
                ax_single.set_title(f'{param} vs Median Latency{title_suffix} ({total_samples} samples)',
                                  fontsize=14, fontweight='bold')
                ax_single.grid(True, alpha=0.3, axis='y')

                # Add legend explaining box plot elements
                legend_elements = [
                    plt.Line2D([0], [0], color='darkblue', linewidth=2, label='Median'),
                    plt.Line2D([0], [0], color='red', linewidth=2, label='Mean'),
                    plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='red',
                              markersize=6, label='Outliers')
                ]
                ax_single.legend(handles=legend_elements, loc='upper right')

                # Rotate x-labels if needed
                if len(labels) > 5:
                    plt.xticks(rotation=45, ha='right')

                plt.tight_layout()
                single_output = os.path.join(output_dir, f'latency_{param}.png')
                plt.savefig(single_output, dpi=150, bbox_inches='tight')
                print(f"Saved individual plot: {single_output}")
                plt.close(fig_single)

    plt.show()


def create_additional_plots(data, output_dir='./'):
    """Create additional plots for buf_num, mr_num, process_num, and buf_size."""

    # Additional parameters to plot
    params = ['buf_num', 'mr_num', 'process_num', 'buf_size']

    # Parameters that should use box plots (few discrete values)
    box_plot_params = ['buf_num', 'mr_num', 'process_num']

    # Parameters that should use binned box plots
    binned_box_plot_params = ['buf_size']

    # Create figure with 2 rows, 2 columns
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Additional RDMA Parameter Effects on Median Latency', fontsize=16, fontweight='bold')

    axes = axes.flatten()

    for idx, param in enumerate(params):
        ax = axes[idx]

        # Organize data by parameter value for box plots (categorical or binned)
        grouped_data = defaultdict(list)

        for d in data:
            if d.get(param) is not None and d.get('latency_median_ns') is not None:
                x_val = d[param]
                y_val = d['latency_median_ns'] / 1e6  # Convert to milliseconds

                # Bin the value if this is a binned parameter
                if param in binned_box_plot_params:
                    x_val = bin_parameter(param, x_val)

                grouped_data[x_val].append(y_val)

        if not grouped_data:
            ax.text(0.5, 0.5, f'No data for {param}',
                   ha='center', va='center', transform=ax.transAxes)
            continue

        # Prepare data for box plot
        if param in binned_box_plot_params:
            # For binned parameters, use predefined order
            if param == 'buf_size':
                bin_order = ['0-10K', '10K-50K', '50K-100K', '100K+']
            labels = [b for b in bin_order if b in grouped_data]
            box_data = [grouped_data[b] for b in labels]
        else:
            # For numeric parameters, sort by value
            sorted_keys = sorted(grouped_data.keys())
            labels = [str(k) for k in sorted_keys]
            box_data = [grouped_data[k] for k in sorted_keys]

        # Create box plot
        bp = ax.boxplot(box_data, labels=labels, patch_artist=True,
                       showmeans=True, meanline=True,
                       boxprops=dict(facecolor='lightgreen', alpha=0.7),
                       meanprops=dict(color='red', linewidth=2),
                       medianprops=dict(color='darkgreen', linewidth=2))

        # Labeling
        ax.set_xlabel(param, fontsize=12, fontweight='bold')
        ax.set_ylabel('Median Latency (ms)', fontsize=12)
        total_samples = sum(len(vals) for vals in box_data)
        title_suffix = ' (binned)' if param in binned_box_plot_params else ''
        ax.set_title(f'{param} vs Median Latency{title_suffix}\n({total_samples} samples)', fontsize=11)
        ax.grid(True, alpha=0.3, axis='y')

        # Rotate x-labels if too many
        if len(labels) > 5:
            ax.tick_params(axis='x', rotation=45)

    plt.tight_layout()

    # Save figure
    output_path = os.path.join(output_dir, 'latency_additional.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved additional plots to: {output_path}")

    # Also save individual plots for closer inspection
    for param in params:
        fig_single, ax_single = plt.subplots(1, 1, figsize=(10, 6))

        # Create box plot for all parameters
        grouped_data = defaultdict(list)

        for d in data:
            if d.get(param) is not None and d.get('latency_median_ns') is not None:
                x_val = d[param]
                y_val = d['latency_median_ns'] / 1e6  # Convert to milliseconds

                # Bin the value if this is a binned parameter
                if param in binned_box_plot_params:
                    x_val = bin_parameter(param, x_val)

                grouped_data[x_val].append(y_val)

        if grouped_data:
            # Prepare data for box plot
            if param in binned_box_plot_params:
                if param == 'buf_size':
                    bin_order = ['0-10K', '10K-50K', '50K-100K', '100K+']
                labels = [b for b in bin_order if b in grouped_data]
                box_data = [grouped_data[b] for b in labels]
            else:
                sorted_keys = sorted(grouped_data.keys())
                labels = [str(k) for k in sorted_keys]
                box_data = [grouped_data[k] for k in sorted_keys]

            # Create box plot
            bp = ax_single.boxplot(box_data, labels=labels, patch_artist=True,
                                  showmeans=True, meanline=True,
                                  boxprops=dict(facecolor='lightgreen', alpha=0.7),
                                  meanprops=dict(color='red', linewidth=2, label='Mean'),
                                  medianprops=dict(color='darkgreen', linewidth=2, label='Median'),
                                  flierprops=dict(marker='o', markerfacecolor='red', markersize=4, alpha=0.5))

            ax_single.set_xlabel(param, fontsize=14, fontweight='bold')
            ax_single.set_ylabel('Median Latency (ms)', fontsize=14)
            total_samples = sum(len(vals) for vals in box_data)
            title_suffix = ' (binned)' if param in binned_box_plot_params else ''
            ax_single.set_title(f'{param} vs Median Latency{title_suffix} ({total_samples} samples)',
                              fontsize=14, fontweight='bold')
            ax_single.grid(True, alpha=0.3, axis='y')

            # Add legend explaining box plot elements
            legend_elements = [
                plt.Line2D([0], [0], color='darkgreen', linewidth=2, label='Median'),
                plt.Line2D([0], [0], color='red', linewidth=2, label='Mean'),
                plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='red',
                          markersize=6, label='Outliers')
            ]
            ax_single.legend(handles=legend_elements, loc='upper right')

            # Rotate x-labels if needed
            if len(labels) > 5:
                plt.xticks(rotation=45, ha='right')

            plt.tight_layout()
            single_output = os.path.join(output_dir, f'latency_{param}.png')
            plt.savefig(single_output, dpi=150, bbox_inches='tight')
            print(f"Saved individual plot: {single_output}")
            plt.close(fig_single)

    plt.show()


def create_publication_plots(data, output_dir='./'):
    """Create publication-quality plots with larger fonts for the 7 key parameters."""

    # Parameters to plot (6 main + buf_size)
    params = ['mtu', 'wq_depth', 'qp_type', 'send_batch', 'recv_batch', 'qp_num', 'buf_size']

    # Classification
    box_plot_params = ['mtu', 'qp_type', 'qp_num']
    binned_box_plot_params = ['send_batch', 'recv_batch', 'wq_depth', 'buf_size']

    # Publication font sizes
    TITLE_SIZE = 20
    LABEL_SIZE = 18
    TICK_SIZE = 16

    for param in params:
        fig, ax = plt.subplots(1, 1, figsize=(8, 6))

        # Organize data
        grouped_data = defaultdict(list)

        for d in data:
            if d.get(param) is not None and d.get('latency_median_ns') is not None:
                x_val = d[param]
                y_val = d['latency_median_ns'] / 1e6  # Convert to milliseconds

                # Bin the value if this is a binned parameter
                if param in binned_box_plot_params:
                    x_val = bin_parameter(param, x_val)

                grouped_data[x_val].append(y_val)

        if not grouped_data:
            plt.close(fig)
            continue

        # Prepare data for box plot
        if param == 'qp_type':
            qp_order = ['RC', 'UC', 'UD']
            labels = [qp for qp in qp_order if qp in grouped_data]
            box_data = [grouped_data[qp] for qp in labels]
        elif param in binned_box_plot_params:
            # For binned parameters, use predefined order
            if param in ['send_batch', 'recv_batch']:
                bin_order = ['0', '1-16', '17-32', '33-64', '65-127']
            elif param == 'wq_depth':
                bin_order = ['0-100', '101-250', '251-500', '501-750', '751+']
            elif param == 'buf_size':
                bin_order = ['0-10K', '10K-50K', '50K-100K', '100K+']
            labels = [b for b in bin_order if b in grouped_data]
            box_data = [grouped_data[b] for b in labels]
        else:
            sorted_keys = sorted(grouped_data.keys())
            labels = [str(k) for k in sorted_keys]
            box_data = [grouped_data[k] for k in sorted_keys]

        # Create box plot with larger elements
        bp = ax.boxplot(box_data, labels=labels, patch_artist=True,
                       showmeans=True, meanline=True,
                       boxprops=dict(facecolor='lightblue', alpha=0.7, linewidth=2),
                       meanprops=dict(color='red', linewidth=3),
                       medianprops=dict(color='darkblue', linewidth=3),
                       whiskerprops=dict(linewidth=2),
                       capprops=dict(linewidth=2),
                       flierprops=dict(marker='o', markerfacecolor='red', markersize=6, alpha=0.5, markeredgewidth=1.5))

        # Labeling with larger fonts
        param_display = param.replace('_', ' ').title()
        ax.set_xlabel(param_display, fontsize=LABEL_SIZE, fontweight='bold')
        ax.set_ylabel('Median Latency (ms)', fontsize=LABEL_SIZE, fontweight='bold')

        # Larger tick labels
        ax.tick_params(axis='both', which='major', labelsize=TICK_SIZE)

        # Grid
        ax.grid(True, alpha=0.3, axis='y', linewidth=1.5)

        # Rotate x-labels if needed
        if len(labels) > 3:
            plt.xticks(rotation=45, ha='right')

        plt.tight_layout()

        # Save with 'pub' prefix
        output_path = os.path.join(output_dir, f'latency_{param}_pub.png')
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        print(f"Saved publication plot: {output_path}")
        plt.close(fig)


def print_summary_statistics(data):
    """Print summary statistics for each parameter."""
    print("\n" + "="*80)
    print("SUMMARY STATISTICS")
    print("="*80)

    params = ['mtu', 'wq_depth', 'qp_type', 'send_batch', 'recv_batch', 'qp_num',
              'buf_num', 'mr_num', 'process_num', 'buf_size']

    for param in params:
        print(f"\n{param.upper()}:")
        print("-" * 40)

        # Group by parameter value
        groups = defaultdict(list)
        for d in data:
            if d.get(param) is not None and d.get('latency_median_ns') is not None:
                groups[d[param]].append(d['latency_median_ns'] / 1e6)  # ms

        if not groups:
            print("  No data available")
            continue

        # Print statistics for each unique value
        for val in sorted(groups.keys(), key=lambda x: (isinstance(x, str), x)):
            latencies = groups[val]
            print(f"  {val}: n={len(latencies):4d}, "
                  f"mean={np.mean(latencies):6.1f}ms, "
                  f"std={np.std(latencies):6.1f}ms, "
                  f"min={np.min(latencies):6.1f}ms, "
                  f"max={np.max(latencies):6.1f}ms")


if __name__ == '__main__':
    # Configuration
    log_dir = './logs/result'
    output_dir = './logs'

    print("RDMA Latency Analysis")
    print("="*80)
    print(f"Loading logs from: {log_dir}")

    # Load all log data
    data = load_all_logs(log_dir)

    if not data:
        print("ERROR: No valid log data found!")
        exit(1)

    # Print summary statistics
    print_summary_statistics(data)

    # Create main plots
    print("\n" + "="*80)
    print("Creating main parameter plots...")
    print("="*80)
    create_scatter_plots(data, output_dir)

    # Create additional plots
    print("\n" + "="*80)
    print("Creating additional parameter plots...")
    print("="*80)
    create_additional_plots(data, output_dir)

    # Create publication-quality plots with larger fonts
    print("\n" + "="*80)
    print("Creating publication-quality plots (larger fonts)...")
    print("="*80)
    create_publication_plots(data, output_dir)

    print("\nAnalysis complete!")
