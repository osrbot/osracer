#!/usr/bin/env python3

import argparse
import csv
import math
import sys
from pathlib import Path


OBS_FIELDS = [
    "px",
    "py",
    "pz",
    "roll",
    "pitch",
    "yaw",
    "vx",
    "vy",
    "vz",
    "wx",
    "wy",
    "wz",
    "last_speed",
    "last_steering",
]

ALIASES = {
    "px": ("px", "x", "position_x", "odom_x"),
    "py": ("py", "y", "position_y", "odom_y"),
    "pz": ("pz", "z", "position_z", "odom_z"),
    "roll": ("roll", "r"),
    "pitch": ("pitch", "p"),
    "yaw": ("yaw", "heading"),
    "vx": ("vx", "linear_x", "lin_x"),
    "vy": ("vy", "linear_y", "lin_y"),
    "vz": ("vz", "linear_z", "lin_z"),
    "wx": ("wx", "angular_x", "ang_x", "gyro_x"),
    "wy": ("wy", "angular_y", "ang_y", "gyro_y"),
    "wz": ("wz", "angular_z", "ang_z", "gyro_z", "yaw_rate"),
    "last_speed": ("last_speed", "last_action_speed", "prev_speed", "cmd_speed"),
    "last_steering": (
        "last_steering",
        "last_action_steering",
        "prev_steering",
        "cmd_steering",
        "steering",
    ),
}

DEFAULT_ZERO_FIELDS = {"last_speed", "last_steering"}

OUTPUT_FIELDS = [
    "action_speed_raw",
    "action_steering_raw",
    "speed_cmd",
    "steering_cmd",
    "clamped",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Replay recorded OSRacer observations through a TorchScript policy "
            "without publishing commands to the car."
        )
    )
    parser.add_argument("--policy", required=True, help="Path to exported TorchScript policy.pt")
    parser.add_argument("--input", required=True, help="Input CSV containing observation columns")
    parser.add_argument("--output", required=True, help="Output CSV with policy actions appended")
    parser.add_argument("--max-speed-mps", type=float, default=0.3, help="Clamp forward speed")
    parser.add_argument("--max-steering-rad", type=float, default=0.488, help="Clamp steering angle")
    parser.add_argument("--device", default="cpu", help="Torch device, normally cpu on Jetson preflight")
    parser.add_argument(
        "--strict-last-action",
        action="store_true",
        help="Require last_speed and last_steering columns instead of defaulting them to 0",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip rows with missing/non-finite values instead of failing fast",
    )
    return parser.parse_args()


def resolve_columns(fieldnames, strict_last_action):
    if not fieldnames:
        raise ValueError("input CSV has no header")

    available = set(fieldnames)
    resolved = {}
    missing = []
    defaults = set() if strict_last_action else DEFAULT_ZERO_FIELDS

    for field in OBS_FIELDS:
        for candidate in ALIASES[field]:
            if candidate in available:
                resolved[field] = candidate
                break
        else:
            if field in defaults:
                resolved[field] = None
            else:
                missing.append(field)

    if missing:
        expected = ", ".join(OBS_FIELDS)
        raise ValueError(f"missing observation columns: {', '.join(missing)}; expected order: {expected}")

    return resolved


def parse_float(value, field, row_number):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {row_number}: invalid {field}={value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"row {row_number}: non-finite {field}={value!r}")
    return parsed


def build_observation(row, resolved_columns, row_number):
    values = []
    for field in OBS_FIELDS:
        source = resolved_columns[field]
        if source is None:
            values.append(0.0)
        else:
            values.append(parse_float(row.get(source), source, row_number))
    return values


def clamp(value, lower, upper):
    return max(lower, min(float(value), upper))


def load_torch_policy(policy_path, device):
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "torch is not installed for this Python. Install a JetPack-compatible torch wheel "
            "or run with the same Python environment used for policy export."
        ) from exc

    if not policy_path.exists():
        raise FileNotFoundError(f"policy does not exist: {policy_path}")

    policy = torch.jit.load(str(policy_path), map_location=device)
    policy.eval()
    return torch, policy


def run_replay(args):
    input_path = Path(args.input)
    output_path = Path(args.output)
    policy_path = Path(args.policy)

    if args.max_speed_mps < 0.0:
        raise ValueError("--max-speed-mps must be >= 0")
    if args.max_steering_rad < 0.0:
        raise ValueError("--max-steering-rad must be >= 0")
    if not input_path.exists():
        raise FileNotFoundError(f"input CSV does not exist: {input_path}")

    torch, policy = load_torch_policy(policy_path, args.device)

    processed = 0
    skipped = 0
    clamped_count = 0

    with input_path.open(newline="") as input_file:
        reader = csv.DictReader(input_file)
        resolved_columns = resolve_columns(reader.fieldnames, args.strict_last_action)
        fieldnames = list(reader.fieldnames or [])
        for field in OUTPUT_FIELDS:
            if field not in fieldnames:
                fieldnames.append(field)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()

            for row_number, row in enumerate(reader, start=2):
                try:
                    obs_values = build_observation(row, resolved_columns, row_number)
                    obs = torch.tensor([obs_values], dtype=torch.float32, device=args.device)
                    with torch.inference_mode():
                        action = policy(obs).squeeze(0).detach().cpu().tolist()
                    if len(action) < 2:
                        raise ValueError(f"row {row_number}: policy returned {len(action)} action values")
                    speed_raw = parse_float(action[0], "action_speed_raw", row_number)
                    steering_raw = parse_float(action[1], "action_steering_raw", row_number)
                except Exception:
                    if not args.skip_invalid:
                        raise
                    skipped += 1
                    continue

                speed_cmd = clamp(speed_raw, 0.0, args.max_speed_mps)
                steering_cmd = clamp(steering_raw, -args.max_steering_rad, args.max_steering_rad)
                clamped = speed_cmd != speed_raw or steering_cmd != steering_raw
                clamped_count += int(clamped)

                row.update(
                    {
                        "action_speed_raw": f"{speed_raw:.9g}",
                        "action_steering_raw": f"{steering_raw:.9g}",
                        "speed_cmd": f"{speed_cmd:.9g}",
                        "steering_cmd": f"{steering_cmd:.9g}",
                        "clamped": str(clamped).lower(),
                    }
                )
                writer.writerow(row)
                processed += 1

    print(
        f"processed={processed} skipped={skipped} clamped={clamped_count} "
        f"output={output_path}",
        file=sys.stderr,
    )


def main():
    args = parse_args()
    try:
        run_replay(args)
    except Exception as exc:
        print(f"policy_replay_csv: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
