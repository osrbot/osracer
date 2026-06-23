#!/usr/bin/env python3
"""Benchmark OSRacer policy inference latency without publishing robot commands."""

import argparse
import json
import re
import statistics
import time
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark OSRacer policy inference latency.")
    parser.add_argument("--policy", default=None, help="TorchScript policy.pt path")
    parser.add_argument("--format", choices=("torchscript", "trtexec-log"), default="torchscript")
    parser.add_argument("--trtexec-log", default=None, help="trtexec output log to summarize when --format trtexec-log")
    parser.add_argument("--device", default="cpu", help="Torch device, for example cpu or cuda:0")
    parser.add_argument("--obs-dim", type=int, default=14)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    parser.add_argument("--max-p95-ms", type=float, default=None, help="Fail if p95 latency exceeds this value")
    return parser.parse_args()


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * (pct / 100.0)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def load_torchscript(path, device):
    import torch

    model = torch.jit.load(str(path), map_location=device)
    model.eval()
    return torch, model


def sync_if_needed(torch, device):
    if str(device).startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize()


def benchmark_torchscript(args):
    policy_path = Path(args.policy).resolve()
    if not policy_path.is_file():
        raise FileNotFoundError(policy_path)
    if args.obs_dim <= 0 or args.batch_size <= 0 or args.warmup < 0 or args.iterations <= 0:
        raise ValueError("obs-dim, batch-size, and iterations must be positive; warmup must be non-negative")

    torch, model = load_torchscript(policy_path, args.device)
    obs = torch.zeros(args.batch_size, args.obs_dim, dtype=torch.float32, device=args.device)

    with torch.inference_mode():
        for _ in range(args.warmup):
            model(obs)
        sync_if_needed(torch, args.device)

        latencies_ms = []
        started = time.perf_counter()
        for _ in range(args.iterations):
            t0 = time.perf_counter()
            out = model(obs)
            sync_if_needed(torch, args.device)
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        total_s = time.perf_counter() - started

    output_shape = tuple(out.detach().cpu().shape)
    return {
        "format": args.format,
        "policy": str(policy_path),
        "device": args.device,
        "obs_dim": args.obs_dim,
        "batch_size": args.batch_size,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "output_shape": output_shape,
        "latency_ms": {
            "min": min(latencies_ms),
            "mean": statistics.fmean(latencies_ms),
            "median": statistics.median(latencies_ms),
            "p95": percentile(latencies_ms, 95),
            "p99": percentile(latencies_ms, 99),
            "max": max(latencies_ms),
        },
        "throughput_hz": args.iterations / total_s if total_s > 0 else None,
    }


def parse_number(text):
    return float(text.replace(",", ""))


def first_match(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return parse_number(match.group(1))
    return None


def parse_trtexec_log(args):
    if not args.trtexec_log:
        raise ValueError("--trtexec-log is required when --format trtexec-log")
    log_path = Path(args.trtexec_log).resolve()
    if not log_path.is_file():
        raise FileNotFoundError(log_path)
    text = log_path.read_text(errors="replace")
    latency = {
        "min": first_match((r"Latency:.*?min\s*=\s*([0-9.,]+)\s*ms", r"min\s*=\s*([0-9.,]+)\s*ms"), text),
        "mean": first_match((r"Latency:.*?mean\s*=\s*([0-9.,]+)\s*ms", r"mean\s*=\s*([0-9.,]+)\s*ms"), text),
        "median": first_match((r"Latency:.*?median\s*=\s*([0-9.,]+)\s*ms", r"median\s*=\s*([0-9.,]+)\s*ms"), text),
        "p95": first_match((r"percentile\(95%\)\s*=\s*([0-9.,]+)\s*ms", r"95%\s*=\s*([0-9.,]+)\s*ms"), text),
        "p99": first_match((r"percentile\(99%\)\s*=\s*([0-9.,]+)\s*ms", r"99%\s*=\s*([0-9.,]+)\s*ms"), text),
        "max": first_match((r"Latency:.*?max\s*=\s*([0-9.,]+)\s*ms", r"max\s*=\s*([0-9.,]+)\s*ms"), text),
    }
    throughput = first_match((r"Throughput:\s*([0-9.,]+)\s*qps", r"Throughput\s*=\s*([0-9.,]+)"), text)
    if latency["p95"] is None and latency["mean"] is None:
        raise ValueError("could not find TensorRT latency summary in trtexec log")
    return {
        "format": "trtexec-log",
        "policy": str(log_path),
        "device": "tensorrt",
        "obs_dim": args.obs_dim,
        "batch_size": args.batch_size,
        "warmup": None,
        "iterations": None,
        "output_shape": None,
        "latency_ms": latency,
        "throughput_hz": throughput,
    }


def print_text(report):
    lat = report["latency_ms"]
    print("policy_inference_benchmark:")
    print(f"  format: {report['format']}")
    print(f"  policy: {report['policy']}")
    print(f"  device: {report['device']}")
    print(f"  batch_size: {report['batch_size']}")
    print(f"  iterations: {report['iterations']}")
    output_shape = report.get("output_shape")
    print(f"  output_shape: {tuple(output_shape)}" if output_shape is not None else "  output_shape: unknown")
    print(
        "  latency_ms: "
        f"min={format_optional_ms(lat['min'])} mean={format_optional_ms(lat['mean'])} "
        f"median={format_optional_ms(lat['median'])} p95={format_optional_ms(lat['p95'])} "
        f"p99={format_optional_ms(lat['p99'])} max={format_optional_ms(lat['max'])}"
    )
    throughput = report["throughput_hz"]
    print(f"  throughput_hz: {throughput:.2f}" if throughput is not None else "  throughput_hz: unknown")


def format_optional_ms(value):
    return "unknown" if value is None else f"{value:.4f}"


def main():
    args = parse_args()
    if args.format == "torchscript":
        if not args.policy:
            raise ValueError("--policy is required when --format torchscript")
        report = benchmark_torchscript(args)
    elif args.format == "trtexec-log":
        report = parse_trtexec_log(args)
    else:
        raise ValueError(f"unsupported format: {args.format}")
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)
    if args.max_p95_ms is not None:
        p95 = report["latency_ms"].get("p95")
        if p95 is None:
            raise ValueError("--max-p95-ms requires a report with p95 latency")
        if p95 > args.max_p95_ms:
            raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
