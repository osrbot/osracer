# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


ROOT = Path(SPEC).resolve().parent  # noqa: F821 - injected by PyInstaller
esptool_datas, esptool_binaries, esptool_hidden = collect_all("esptool")

datas = [
    (str(ROOT / "osracer_firmware_client" / "resources"), "osracer_firmware_client/resources"),
    (str(ROOT / "osracer_firmware_client" / "static"), "osracer_firmware_client/static"),
    (
        str(ROOT / "osracer_firmware_client" / "THIRD_PARTY_NOTICES.txt"),
        "osracer_firmware_client",
    ),
]
build_info = os.environ.get("OSRACER_FIRMWARE_CLIENT_BUILD_INFO")
if not build_info:
    raise SystemExit("OSRACER_FIRMWARE_CLIENT_BUILD_INFO is required")
datas.append((build_info, "osracer_firmware_client"))
datas += esptool_datas
datas += copy_metadata("esptool", recursive=True)
datas += copy_metadata("pyserial", recursive=True)

analysis = Analysis(  # noqa: F821 - injected by PyInstaller
    [str(ROOT / "entry.py")],
    pathex=[str(ROOT)],
    binaries=esptool_binaries,
    datas=datas,
    hiddenimports=esptool_hidden + collect_submodules("serial"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)  # noqa: F821 - injected by PyInstaller

executable = EXE(  # noqa: F821 - injected by PyInstaller
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="osracer-firmware-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
