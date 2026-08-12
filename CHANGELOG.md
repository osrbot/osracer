# Changelog

This changelog records user-visible features, compatibility updates, fixes, and
operational limitations included in published releases.

## [Unreleased]

## [0.1.2] - 2026-08-12

### Firmware client

- Updates the supported official OSRacer firmware resource and its exact
  application, recovery, identity, size, and SHA256 metadata.
- Retains vehicle-parameter backup, NVS-preserving App updates, post-reboot
  identity checks, and isolated full-erase recovery behavior.

### Navigation and race

- Uses the pinned OSRacer Ackermann recovery behavior with odometry-directed
  escape and costmap clearing, and verifies that both maintained navigation
  configurations can configure and activate the Behavior Server.
- Allows the MPC controller to use two-column racelines or empty speed cells by
  applying the configured default race speed.

### ROS runtime integration

- Uses the pinned `osracer_base` Red vehicle profile as the single source for
  wheelbase, chassis speed limit, and maximum steering angle in bringup, race,
  and simulation packages.
- Keeps product launch files responsible only for runtime wiring such as the
  serial port, frames, topics, and feature switches.
- Assigns the chassis udev rule exclusively to `osracer_base`; the optional
  bringup installer now manages only camera and LED accessory rules.
- Aligns the description URDF wheelbase, track width, and wheel mesh radius with
  the approved `0.285 / 0.215 / 0.0425 m` vehicle projection used by Race and
  Sim.

### Maintenance

- Keeps only the standalone firmware client under `tools/`; policy inference,
  Jetson measurement, TensorRT, and Sim2Real utilities move to `osracer_lab`.
- Retains the supported firmware-client operations and embedded resources while
  removing the superseded internal updater implementation.
- Expands CI to build and test the complete maintained ROS workspace, validate
  installed reference launches, and start the chassis launch against a missing
  serial device without terminating the runtime.

### Documentation

- Documents the agreed parameter authority: firmware speed/odometry parameters
  follow `osrcore`, ROS geometry follows Base/Race, and stale Lab geometry is an
  alignment defect rather than an alternate parameter set requiring remeasurement.
- Links the maintained hardware inventory and real-car measurement worksheet in
  `osracer_lab` from the advanced-development section.
- Documents the complete one-way Core to Base to OSRacer to Lab dependency
  chain and treats repeated Race, Sim, and Lab geometry only as checked
  projections of the approved vehicle specification.

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
