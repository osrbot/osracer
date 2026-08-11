# OSRacer Standalone Firmware Client

## 1. Scope

`osracer-firmware-client` is a standalone ESP32-S3 firmware utility for Jetson
Linux ARM64. The executable contains its runtime, serial dependency, local web
interface, and the validated official firmware resources. It does not require
ROS, ESP-IDF, Python packages, or a network download.

There is one client. A standard official update inspects the connected device
and automatically selects the matching firmware resource.

The local web interface supports Chinese and English. On first use it follows
the browser language, then stores the selected language only in that browser.
Changing the language does not connect to the device or alter an update. The
confirmation tokens and low-level diagnostic errors remain in English so field
support and audit records use the same exact text.

The client handles firmware and NVS safety only. It does not run `git pull`,
build ROS, or start the vehicle workspace.

## 2. Recommended entry point

Stop any ROS node using `/dev/osrbot_base`, then start the local interface:

```bash
chmod +x osracer-firmware-client
./osracer-firmware-client ui
```

The server listens only on `127.0.0.1`, prints a local URL with a random session
token, loads no CDN resources, and permits only one firmware operation at a
time.

For an SSH or service workflow, use the CLI:

```bash
./osracer-firmware-client inspect
./osracer-firmware-client bundles
./osracer-firmware-client official
./osracer-firmware-client custom /absolute/path/application.bin
./osracer-firmware-client erase --bundle B01
```

The default port is `/dev/osrbot_base`. A different port must be supplied as a
global option:

```bash
./osracer-firmware-client --port /dev/ttyACM0 inspect
```

## 3. Operations

### 3.1 Official Firmware Update

The client requires the device identity to match exactly one embedded official
package. An unknown identity, a profile mismatch, an active OTA session, or a
failed mandatory logical backup stops the operation before App data is sent.

The standard path is:

1. Stop the vehicle and open the serial port exclusively.
2. Inspect firmware, protocol, profile, and voltage state.
3. Back up the vehicle parameters exposed by the running firmware.
4. Display the backup path and SHA256.
5. Require the exact `UPDATE` confirmation.
6. Update only the App OTA partition without erasing NVS.
7. Reconnect and verify the official target.
8. Re-read and compare vehicle parameters when supported by the target.

### 3.2 Custom Application Flash

This mode accepts one ESP32-S3 `application.bin`. It rejects a bootloader,
FullFlash or merged image, a wrong chip, a broken checksum, a missing validation
hash, and an image that exceeds the OTA slot.

The exact confirmation is `FLASH CUSTOM`. Readable vehicle parameters are
backed up first and the App OTA does not erase NVS. If the custom application no
longer exposes the OSR inspection or OTA protocol, the client reports only the
transfer result and does not claim that custom behavior was validated.

### 3.3 Erase and Restore

This isolated advanced operation accepts only an embedded official recovery
package. It does not accept a customer FullFlash image.

The path is deliberately two-stage:

1. Enter `PREPARE B01` or `PREPARE B02`.
2. Attempt a logical parameter backup and require a raw NVS backup.
3. Display the raw NVS path, offset, size, and SHA256.
4. Acknowledge that non-NVS persistent data will be lost.
5. Enter `ERASE AND FLASH B01` or `ERASE AND FLASH B02`.
6. Recheck the same device, security state, and unchanged NVS.
7. Erase the full flash, write the official recovery image, and restore NVS at
   `0x9000`.
8. Read back `0x6000` bytes, compare them byte for byte, reboot, and verify the
   official firmware identity.

Nothing is erased before the second confirmation. Unsupported secure boot,
secure download, flash encryption, device identity, flash size, partition,
resource hash, NVS file, or readback state stops the operation. Full erase
removes `storage`, OTA history, and the alternate App; version 1 does not back
up `storage`.

## 4. Backup and audit locations

The default private state directory is:

```text
${XDG_STATE_HOME:-~/.local/state}/osracer/firmware-client/
├── audit/
├── backups/
├── nvs-raw/
└── uploads/
```

Directories use mode `0700` and backup files use mode `0600`. Writes use a
temporary file, `fsync`, atomic replacement, re-read, size verification, and
SHA256 verification. The audit contains states, field names, paths, and hashes,
but not vehicle parameter values.

If the result says `Do not reflash`, do not start another App update. Preserve
the displayed backup and audit paths and follow the result guidance.

## 5. Build and verification

Build the customer executable on Linux ARM64:

```bash
tools/build_firmware_client.sh
```

Outputs:

```text
dist/firmware-client/osracer-firmware-client
dist/firmware-client/osracer-firmware-client.sha256
```

The build uses pinned dependencies and verifies the command-line interface,
provenance metadata, licenses, and embedded firmware resources. Verify a
downloaded executable with its accompanying SHA256 file before use.

Version 0.1.1 is distributed for Linux ARM64 and includes the supported official
firmware resources. Official update mode selects a resource only after the
connected device passes identity, protocol, profile, voltage, and update-state
checks. Custom App and full-erase recovery remain advanced operations and must
be used only with an authorized image and a retained backup.

## 6. Supported entry point

Use `osracer-firmware-client` for all new deployments. The legacy
`osracer_firmware_update.py` command is not distributed as a supported customer
interface.

## 7. License

Repository-owned source remains available under the root MIT license. The
self-contained executable also packages GPL-2.0-or-later `esptool`, so the
executable is distributed under GPL-2.0-or-later. Exact dependency versions,
source links, and notices are listed in
`osracer_firmware_client/THIRD_PARTY_NOTICES.txt`.
