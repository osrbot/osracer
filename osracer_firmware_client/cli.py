"""Customer-facing command line interface for the standalone client."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__, core
from .build_info import load_build_info, load_third_party_notices
from .bundles import load_bundles
from .images import validate_application_file
from .operations import ClientSettings, FirmwareClient


class ConsoleEventRenderer:
    def __init__(self, output: Callable[[str], None] = print):
        self.output = output
        self._last_progress = -1

    def __call__(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase", "operation")).replace("_", " ").title()
        status = event.get("status")
        message = event.get("message", "")
        if status == "progress":
            percent = int(float(event.get("progress", 0.0)) * 100)
            if percent != 100 and percent < self._last_progress + 5:
                return
            self._last_progress = percent
            details = event.get("details", {})
            written = details.get("written", 0)
            total = details.get("total", 0)
            self.output(f"  Flashing App {percent:3d}%  {written:,}/{total:,} bytes")
            return
        self.output(f"[{phase}] {message}")
        details = event.get("details", {})
        for label, key in (
            ("Backup file", "path"),
            ("Backup SHA256", "sha256"),
            ("Audit log", "audit_path"),
            ("Raw NVS backup", "raw_nvs_path"),
        ):
            value = details.get(key)
            if value:
                self.output(f"  {label}: {value}")


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _chunk_size(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if not core.MIN_CHUNK_SIZE <= result <= core.MAX_CHUNK_SIZE:
        raise argparse.ArgumentTypeError(
            f"must be between {core.MIN_CHUNK_SIZE} and {core.MAX_CHUNK_SIZE}"
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osracer-firmware-client",
        description="Standalone OSRacer ESP32-S3 firmware client with embedded official packages.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--port", default=core.DEFAULT_PORT)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument(
        "--response-timeout",
        type=_positive_float,
        default=core.DEFAULT_RESPONSE_TIMEOUT,
    )
    parser.add_argument(
        "--reconnect-timeout",
        type=_positive_float,
        default=core.DEFAULT_RECONNECT_TIMEOUT,
    )
    parser.add_argument("--chunk-size", type=_chunk_size, default=core.DEFAULT_CHUNK_SIZE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", help="inspect the connected device without writing firmware")
    commands.add_parser("bundles", help="validate and list embedded official package summaries")
    commands.add_parser("build-info", help="show executable build provenance")
    commands.add_parser("licenses", help="show packaged third-party notices")
    official = commands.add_parser("official", help="install the uniquely matched official package")
    official.add_argument(
        "--reinstall",
        action="store_true",
        help="allow reinstalling an already installed official target",
    )
    custom = commands.add_parser("custom", help="flash one local ESP32-S3 application.bin")
    custom.add_argument("image", help="absolute or local path to application.bin")
    erase = commands.add_parser(
        "erase",
        help="advanced full erase, official recovery, and raw NVS restore",
    )
    erase.add_argument("--bundle", required=True, choices=("B01", "B02"))
    ui = commands.add_parser("ui", help="start the local browser interface")
    ui.add_argument("--listen", default="127.0.0.1")
    ui.add_argument("--http-port", type=int, default=0)
    ui.add_argument("--no-browser", action="store_true")
    return parser


def _settings(args: argparse.Namespace) -> ClientSettings:
    default = ClientSettings()
    return ClientSettings(
        port=args.port,
        chunk_size=args.chunk_size,
        response_timeout=args.response_timeout,
        reconnect_timeout=args.reconnect_timeout,
        state_dir=(
            Path(args.state_dir).expanduser().absolute()
            if args.state_dir
            else default.state_dir
        ),
    )


def _print_json(value: Any, output: Callable[[str], None]) -> None:
    output(json.dumps(value, indent=2, sort_keys=True))


def main(
    argv: list[str] | None = None,
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "bundles":
            _print_json(
                {key: bundle.safe_summary() for key, bundle in load_bundles().items()},
                output_func,
            )
            return 0
        if args.command == "build-info":
            _print_json(load_build_info(), output_func)
            return 0
        if args.command == "licenses":
            output_func(load_third_party_notices().rstrip())
            return 0
        if args.command == "ui":
            if args.listen != "127.0.0.1":
                raise core.PackageValidationError(
                    "the first release only permits the local 127.0.0.1 interface"
                )
            from .web import serve

            return serve(
                settings=_settings(args),
                host=args.listen,
                port=args.http_port,
                open_browser=not args.no_browser,
                output_func=output_func,
            )

        renderer = ConsoleEventRenderer(output_func)
        client = FirmwareClient(settings=_settings(args), event_sink=renderer)
        if args.command == "inspect":
            _print_json(client.inspect().safe_summary(), output_func)
            return 0
        if args.command == "official":
            inspection = client.inspect()
            _print_json(inspection.safe_summary(), output_func)
            confirmation = input_func("Type UPDATE to continue: ").strip()
            result = client.official_update(
                confirmation=confirmation,
                reinstall=args.reinstall,
            )
            _print_json(result.safe_summary(), output_func)
            return 0
        if args.command == "custom":
            image_path = Path(args.image).expanduser().absolute()
            image = validate_application_file(image_path)
            output_func(f"Custom file: {image_path.name}")
            _print_json(image.safe_summary(), output_func)
            inspection = client.inspect()
            _print_json(inspection.safe_summary(), output_func)
            confirmation = input_func("Type FLASH CUSTOM to continue: ").strip()
            result = client.custom_app_update(image_path, confirmation=confirmation)
            _print_json(result.safe_summary(), output_func)
            return 0
        if args.command == "erase":
            output_func("Advanced recovery erases all non-NVS persistent data.")
            first = input_func(f"Type PREPARE {args.bundle} to create the backups: ").strip()
            preparation = client.prepare_erase(args.bundle, confirmation=first)
            _print_json(preparation.safe_summary(), output_func)
            output_func("Raw NVS has been stored and verified. Review the path above.")
            second = input_func(
                f"Type ERASE AND FLASH {args.bundle} to erase and continue: "
            ).strip()
            result = client.execute_erase(
                preparation.preparation_id,
                acknowledge_non_nvs_loss=second == f"ERASE AND FLASH {args.bundle}",
                confirmation=second,
            )
            _print_json(result.safe_summary(), output_func)
            return 0
        parser.error("unsupported command")
    except core.FirmwareUpdateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if getattr(error, "no_app_reflash", False):
            print("Do not reflash the App; inspect the device state first.", file=sys.stderr)
        if getattr(error, "physical_recovery_required", False):
            print(
                "Flash was erased or rewritten; keep the raw NVS backup and use physical recovery.",
                file=sys.stderr,
            )
        if error.audit_path is not None:
            print(f"Audit log: {error.audit_path}", file=sys.stderr)
        return error.exit_code
    return 2


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    raise SystemExit(main())
