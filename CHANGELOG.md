# Changelog

This changelog records user-visible features, compatibility updates, fixes, and
operational limitations included in published releases.

## [0.1.1] - 2026-08-11

### Standalone firmware client

- Provides one self-contained Linux ARM64 executable with CLI and local browser
  interfaces in Chinese and English.
- Includes supported official firmware resources and selects a compatible
  resource automatically after device inspection.
- Supports official App updates, custom ESP32-S3 application updates, and an
  isolated full-erase recovery operation.
- Creates a private vehicle-parameter backup before supported App updates and
  displays the backup path and SHA256 result.
- Preserves the NVS partition during App-only updates and verifies the device
  identity and configuration after reconnection.
- Requires a raw NVS backup and two explicit confirmations before full-erase
  recovery can begin.
- Reports application-image and backup-file SHA256 values with distinct labels.

### Compatibility

- Designed for supported Jetson Linux ARM64 systems.
- Does not require ROS, ESP-IDF, Python packages, or network access at runtime.
- Includes the official `OSRACER_V1.1` firmware resource for compatible OSRacer
  devices.

### Distribution

- The executable is accompanied by a SHA256 checksum file.
- Packaged third-party notices are available through
  `osracer-firmware-client licenses`.
