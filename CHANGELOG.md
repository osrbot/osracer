# Changelog

This changelog records user-visible features, compatibility updates, fixes, and
operational limitations included in OSRacer releases.

## [Unreleased]

## [1.2.0] - 2026-08-13

### Added

- Complete ROS 2 Humble workspace for vehicle bringup, description, SLAM,
  navigation, autonomous racing, simulation, calibration, diagnostics, and
  field demonstrations.
- Reference launch and configuration workflows for SLAM Toolbox, Cartographer,
  GMapping, Nav2, Ackermann navigation, and multiple racing controllers.
- Pinned source dependencies for the chassis interface and OSRBOT-maintained
  navigation components.
- Bilingual project overview, installation, configuration, troubleshooting,
  and support information.

### Changed

- Uses the pinned OSRacer Base release as the chassis-driver implementation and
  source of ROS vehicle geometry and operating limits.
- Aligns wheelbase, track width, wheel radius, speed limits, and steering limits
  across vehicle description, racing, and simulation configurations.
- Keeps firmware installation independent of the ROS workspace through the
  native [OSR Updater](https://github.com/osrbot/osr_updater) application.
- Assigns chassis serial-device setup to OSRacer Base while product bringup
  retains only accessory-device rules.

### Fixed

- Restores odometry-directed navigation recovery with costmap clearing for
  blocked or oscillating vehicles.
- Allows MPC racing to use two-column racelines and rows without an explicit
  speed by applying the configured default race speed.
- Verifies installed reference launches and starts the chassis launch against a
  missing serial device without terminating the runtime.

### Compatibility

- Ubuntu 22.04 and ROS 2 Humble.
- OSRacer Base is imported at the exact revision declared in `osracer.repos`.
- `osracer_dependency` is provided as the pinned Git submodule declared by this
  release.
- Firmware must implement the serial interface required by the imported Base
  revision.

[1.2.0]: https://github.com/osrbot/osracer/releases/tag/v1.2.0
