#!/usr/bin/env python3
"""PyInstaller entry point for the standalone firmware client."""

import signal

from osracer_firmware_client.cli import main


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    raise SystemExit(main())
