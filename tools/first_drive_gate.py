#!/usr/bin/env python3
"""Aggregate first-drive go/no-go evidence for OSRacer Jetson deployment."""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy_summary = load_module("policy_replay_summary", TOOLS_DIR / "policy_replay_summary.py")
runtime_summary = load_module("jetson_runtime_summary", TOOLS_DIR / "jetson_runtime_summary.py")


def parse_args():
    parser = argparse.ArgumentParser(description="Check OSRacer first-drive go/no-go artifacts.")
    parser.add_argument("--package-dir", required=True, help="Jetson deployment package directory")
    parser.add_argument("--policy-replay", required=True, help="CSV produced by tools/policy_replay_csv.py")
    parser.add_argument("--sensor-summary", required=True, help="sensor_summary.json from tools/jetson_sensor_preflight.sh")
    parser.add_argument("--environment-report", required=True, help="JSON from tools/jetson_environment_report.py")
    parser.add_argument("--runtime-dir", default=None, help="Optional directory from tools/jetson_runtime_monitor.sh")
    parser.add_argument("--serial-latency", default=None, help="Optional serial_latency.json from tools/serial_latency_probe.py")
    parser.add_argument("--policy-benchmark", default=None, help="Optional JSON from tools/benchmark_policy_inference.py")
    parser.add_argument("--performance-profile", default=None, help="JSON from tools/jetson_performance_profile.sh --json-output")
    parser.add_argument("--output", default=None, help="Optional JSON report output path")
    parser.add_argument("--obs-dim", type=int, default=14, help="Policy observation dim for package verifier")
    parser.add_argument("--load-policy", action="store_true", help="Let package verifier load the policy artifact")
    parser.add_argument("--min-replay-rows", type=int, default=1)
    parser.add_argument("--max-speed-cmd", type=float, default=0.3)
    parser.add_argument("--max-abs-steering-cmd", type=float, default=0.488)
    parser.add_argument("--max-clamped-ratio", type=float, default=0.2)
    parser.add_argument("--min-topic-hz", type=float, default=5.0)
    parser.add_argument("--max-temp-c", type=float, default=85.0)
    parser.add_argument("--max-swap-mb", type=int, default=1024)
    parser.add_argument("--max-serial-latency-s", type=float, default=0.05)
    parser.add_argument("--max-policy-p95-ms", type=float, default=10.0)
    return parser.parse_args()


def check(ok, name, detail, report):
    status = "pass" if ok else "fail"
    report["checks"].append({"name": name, "status": status, "detail": detail})
    if not ok:
        report["failures"].append(f"{name}: {detail}")


def package_task(package_dir):
    manifest = Path(package_dir).resolve() / "manifest.json"
    if not manifest.is_file():
        return None
    with manifest.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return None
    return str(data.get("task", ""))


def run_package_verifier(args, report):
    command = [sys.executable, str(TOOLS_DIR / "verify_jetson_deployment.py"), str(Path(args.package_dir).resolve()), "--obs-dim", str(args.obs_dim)]
    if not args.load_policy:
        command.append("--skip-policy-load")
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    report["artifacts"]["deployment_package"] = {
        "path": str(Path(args.package_dir).resolve()),
        "command": command,
        "exit_code": result.returncode,
        "log": result.stdout.splitlines(),
    }
    check(result.returncode == 0, "deployment_package", f"verify_jetson_deployment exit={result.returncode}", report)
    log_text = result.stdout
    if "source authority snapshot: not included" in log_text:
        check(False, "source_authority_snapshot", "not included in deployment package", report)
    else:
        snapshot_ok = (
            result.returncode == 0
            and "[OK] source_authority_snapshot sources" in log_text
            and "[OK] source_authority_snapshot osrcore_contract" in log_text
            and "[OK] source_authority_snapshot osracer_contract" in log_text
        )
        detail = "verified by deployment package verifier" if snapshot_ok else "missing verifier OK lines"
        check(snapshot_ok, "source_authority_snapshot", detail, report)
    task = package_task(args.package_dir)
    if task and "Visual" not in task:
        check(True, "camera_calibration_overlay", f"not required for task={task}", report)
    else:
        camera_ok = result.returncode == 0 and "[OK] camera calibration overlay:" in log_text
        detail = "verified by deployment package verifier" if camera_ok else "missing verifier OK line"
        check(camera_ok, "camera_calibration_overlay", detail, report)


def run_policy_replay(args, report):
    csv_path = Path(args.policy_replay).resolve()
    summary = policy_summary.summarize(csv_path)
    class Thresholds:
        min_rows = args.min_replay_rows
        max_clamped_ratio = args.max_clamped_ratio
        max_speed_cmd = args.max_speed_cmd
        max_abs_steering_cmd = args.max_abs_steering_cmd
        max_abs_raw_speed = None
        max_abs_raw_steering = None
    failures = policy_summary.check_thresholds(summary, Thresholds)
    report["artifacts"]["policy_replay"] = {"path": str(csv_path), "summary": summary, "threshold_failures": failures}
    check(not failures, "policy_replay", "; ".join(failures) if failures else "low-speed replay thresholds passed", report)


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def run_sensor_summary(args, report):
    path = Path(args.sensor_summary).resolve()
    data = load_json(path)
    missing = data.get("missing_required_topics", [])
    overall = data.get("overall")
    topics = data.get("topics", {})
    report["artifacts"]["sensor_summary"] = {
        "path": str(path),
        "overall": overall,
        "missing_required_topics": missing,
        "topic_count": len(topics),
    }
    check(overall == "pass" and not missing, "sensor_summary", f"overall={overall} missing={missing}", report)


def run_environment_report(args, report):
    path = Path(args.environment_report).resolve()
    data = load_json(path)
    overall = data.get("overall")
    failures = data.get("failures", [])
    is_jetson = data.get("jetson", {}).get("is_jetson")
    ros_setup = data.get("ros", {}).get("setup_exists")
    report["artifacts"]["environment_report"] = {
        "path": str(path),
        "overall": overall,
        "is_jetson": is_jetson,
        "ros_setup_exists": ros_setup,
        "failures": failures,
    }
    ok = overall == "pass" and is_jetson is True and ros_setup is True and not failures
    check(ok, "environment_report", f"overall={overall} is_jetson={is_jetson} ros_setup={ros_setup} failures={failures}", report)


def run_serial_latency(args, report):
    if not args.serial_latency:
        check(False, "serial_latency", "not supplied", report)
        return
    path = Path(args.serial_latency).resolve()
    data = load_json(path)
    latency = data.get("latency_s", {}) if isinstance(data.get("latency_s"), dict) else {}
    p95 = latency.get("p95")
    overall = data.get("overall")
    ok = overall == "pass" and isinstance(p95, (int, float)) and p95 <= args.max_serial_latency_s
    report["artifacts"]["serial_latency"] = {
        "path": str(path),
        "overall": overall,
        "p95_latency_s": p95,
        "max_allowed_s": args.max_serial_latency_s,
    }
    check(ok, "serial_latency", f"overall={overall} p95={p95} max={args.max_serial_latency_s}", report)


def run_policy_benchmark(args, report):
    if not args.policy_benchmark:
        check(False, "policy_inference_benchmark", "not supplied", report)
        return
    path = Path(args.policy_benchmark).resolve()
    data = load_json(path)
    latency = data.get("latency_ms", {}) if isinstance(data.get("latency_ms"), dict) else {}
    p95 = latency.get("p95")
    fmt = data.get("format")
    device = data.get("device")
    throughput = data.get("throughput_hz")
    ok = isinstance(p95, (int, float)) and p95 <= args.max_policy_p95_ms
    report["artifacts"]["policy_benchmark"] = {
        "path": str(path),
        "format": fmt,
        "device": device,
        "p95_latency_ms": p95,
        "throughput_hz": throughput,
        "max_allowed_p95_ms": args.max_policy_p95_ms,
    }
    check(ok, "policy_inference_benchmark", f"format={fmt} device={device} p95_ms={p95} max={args.max_policy_p95_ms}", report)


def run_performance_profile(args, report):
    if not args.performance_profile:
        check(False, "jetson_performance_profile", "not supplied", report)
        return
    path = Path(args.performance_profile).resolve()
    data = load_json(path)
    requested = data.get("requested", {}) if isinstance(data.get("requested"), dict) else {}
    tools = data.get("tools", {}) if isinstance(data.get("tools"), dict) else {}
    jetson = data.get("jetson", {}) if isinstance(data.get("jetson"), dict) else {}
    governors = data.get("cpu_governors", {}) if isinstance(data.get("cpu_governors"), dict) else {}
    apply_requested = data.get("apply_requested")
    is_jetson = jetson.get("is_jetson")
    nvpmodel = requested.get("nvpmodel")
    jetson_clocks = requested.get("jetson_clocks")
    nvpmodel_present = tools.get("nvpmodel", {}).get("present") is True
    jetson_clocks_present = tools.get("jetson_clocks", {}).get("present") is True
    governor_ok = not requested.get("set_cpu_governor") or governors.get("all_match_requested") is True
    ok = (
        apply_requested is True
        and is_jetson is True
        and bool(nvpmodel)
        and jetson_clocks is True
        and nvpmodel_present
        and jetson_clocks_present
        and governor_ok
    )
    report["artifacts"]["performance_profile"] = {
        "path": str(path),
        "apply_requested": apply_requested,
        "is_jetson": is_jetson,
        "nvpmodel": nvpmodel,
        "jetson_clocks": jetson_clocks,
        "nvpmodel_present": nvpmodel_present,
        "jetson_clocks_present": jetson_clocks_present,
        "cpu_governor_ok": governor_ok,
    }
    check(
        ok,
        "jetson_performance_profile",
        f"apply={apply_requested} is_jetson={is_jetson} nvpmodel={nvpmodel} "
        f"jetson_clocks={jetson_clocks} governor_ok={governor_ok}",
        report,
    )


def run_runtime_summary(args, report):
    if not args.runtime_dir:
        check(False, "runtime_monitor", "not supplied", report)
        return
    runtime_dir = Path(args.runtime_dir).resolve()
    data = runtime_summary.build_report(runtime_dir)
    failures = []
    errors = data["summary"].get("errors", [])
    warnings = data["summary"].get("warnings", [])
    if errors:
        failures.append(f"summary errors={len(errors)}")
    for topic, item in sorted(data.get("topics", {}).items()):
        if item.get("warnings"):
            failures.append(f"{topic} warnings={len(item['warnings'])}")
        rate = item.get("average_rate_hz")
        if rate is None:
            failures.append(f"{topic} missing average_rate_hz")
        elif rate < args.min_topic_hz:
            failures.append(f"{topic} average_rate_hz {rate} < {args.min_topic_hz}")
    teg = data.get("tegrastats", {})
    temp = teg.get("max_temp_c")
    swap = teg.get("max_swap_used_mb")
    if temp is not None and temp > args.max_temp_c:
        failures.append(f"max_temp_c {temp} > {args.max_temp_c}")
    if swap is not None and swap > args.max_swap_mb:
        failures.append(f"max_swap_used_mb {swap} > {args.max_swap_mb}")
    report["artifacts"]["runtime_monitor"] = {
        "path": str(runtime_dir),
        "errors": errors,
        "warnings": warnings,
        "topics": data.get("topics", {}),
        "tegrastats": teg,
        "threshold_failures": failures,
    }
    check(not failures, "runtime_monitor", "; ".join(failures) if failures else "runtime thresholds passed", report)


def build_report(args):
    report = {"overall": "fail", "checks": [], "failures": [], "artifacts": {}}
    run_package_verifier(args, report)
    run_policy_replay(args, report)
    run_sensor_summary(args, report)
    run_environment_report(args, report)
    run_serial_latency(args, report)
    run_policy_benchmark(args, report)
    run_performance_profile(args, report)
    run_runtime_summary(args, report)
    report["overall"] = "pass" if not report["failures"] else "fail"
    return report


def print_text(report):
    print(f"first_drive_gate: {report['overall']}")
    for item in report["checks"]:
        print(f"[{item['status'].upper()}] {item['name']}: {item['detail']}")
    if report["failures"]:
        print("failures:")
        for failure in report["failures"]:
            print(f"  - {failure}")


def main():
    args = parse_args()
    try:
        report = build_report(args)
    except Exception as exc:
        report = {"overall": "fail", "checks": [], "failures": [str(exc)], "artifacts": {}}
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {output}")
    print_text(report)
    return 0 if report["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
