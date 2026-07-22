# Changelog

All notable changes to this integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-22

Initial release.

### Added
- Support for **ProfiLux 3 / 4** (HTTP `communication.php` interface) and the
  **ProfiLux mini** (WebSocket interface); the interface is selectable in the
  config flow.
- **Sensors** — auto-discovered, scaled and classified (temperature, pH, redox,
  conductivity, humidity, oxygen, voltage, …), by GHL type id when valid and
  otherwise by probe name.
- **Power sockets** — on/off state for physical sockets (state register) and for
  digital-powerbar channels (derived from the decoded per-socket current).
- **Per-socket current** sensors and a `current_a` attribute (digital powerbar).
- **Level control loops** ("Niveau") — per-loop alarm/fill/drain binary sensor.
- **Controller alarm** binary sensor.
- Reliable transport: frame checksum (BCA/BCC) validation and batched,
  retrying reads that tolerate the controller's occasional dropped/corrupt frame.
- HACS brand **icon** and **logo**.
- Standalone `scraper.py` for verifying a controller from the LAN, with a
  `--debug` register dump.

[1.0.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.0.0
