# Changelog

All notable changes to this integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-07-22

### Added
- **Power monitoring.** A **total current** (A) and estimated **total power** (W,
  current × mains voltage) sensor across the powerbar; the strategy dashboard
  gains a power section with a 24 h history graph.
- **Level status** sensor per control loop (OK / Filling / Draining / Alarm).
- Debug dump (`scraper.py --debug`) now includes digital-input states and each
  level loop's source assignments, to map the individual min/max float sensors.

## [1.1.0] - 2026-07-22

### Added
- **Auto-generating dashboard strategy.** The integration registers a Lovelace
  strategy as a frontend resource on setup, so a dashboard built with
  `strategy: {type: custom:profilux}` generates itself from the current ProfiLux
  entities (sensor gauges, socket outlet tiles, per-socket current row,
  level/alarm row) — no hard-coded entity IDs.

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
- A ready-made **Lovelace dashboard** (`dashboards/aquarium.yaml`) — sensor
  gauges, socket outlet tiles, and a level/alarm row.
- Standalone `scraper.py` for verifying a controller from the LAN, with a
  `--debug` register dump.

[1.2.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.2.0
[1.1.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.1.0
[1.0.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.0.0
