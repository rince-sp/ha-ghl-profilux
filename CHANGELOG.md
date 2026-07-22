# Changelog

All notable changes to this integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.0] - 2026-07-22

### Added
- **Dosing pumps ("Dosierpumpen").** A fill-level sensor per dosing pump showing
  the **remaining reservoir volume** in mL, with the configured **capacity** and
  a **percent** full as attributes. Only pumps actually in use are exposed — the
  schedule mode ("Modus" on the Dosierplan tab) must not be "Aus" — and the mode
  is surfaced as an attribute. The auto-generating dashboard gains a
  **Dosierpumpen** section.

## [1.3.0] - 2026-07-22

### Fixed
- **Current for switching channels beyond the first 16.** The powerbar current
  array (code `10128`) only carries sockets 0–15, so higher channels — an Orphek
  light on channel 17, a pump on channel 18 — drew current the GHL app showed
  but Home Assistant reported as "unknown". Their current lives in the next
  powerbar bank at the `+1000` mega-block offset (code `11128`); the integration
  now reads every bank (socket `i` → bank `i // 16`, field `i % 16`), so those
  channels report their real current and switch on/off correctly.

### Added
- **Targeted current/level probes** in the diagnostic dump (`scraper.py
  --debug`): the neighbouring/mega-block current banks (decoded as 16-bit
  little-endian mA fields), a possible higher socket state bank, and each level
  loop's full three-word source block.

### Changed
- Refactored the per-socket current decoder onto a shared 16-bit little-endian
  field splitter (`_decode_16bit_fields`); no change to the decoded values.
- The diagnostic dump uses small, targeted probes instead of a broad code sweep,
  which the controller answers reliably (a wide sweep made it drop frames and
  blanked the essential reads).

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

Initial release. Reads a GHL ProfiLux controller over the local network and
exposes its sensors, power sockets, level control loops and alarm to Home
Assistant.

### Added
- **Two local transports**, selectable in the config flow: the raw SWMBus
  frames tunnelled over **WebSocket** (`ws://<host>/ws`, the path ProfiLux 4
  firmware answers on) and the documented **HTTP** `communication.php`
  interface. Both drive the same protocol layer, so entities are identical
  either way.
- **Sensors** — auto-discovered, scaled and classified (temperature, pH, redox,
  conductivity, humidity, oxygen, voltage, …), by GHL type id when valid and
  otherwise by probe name (the type register is unreliable on some firmwares).
- **Power sockets** — on/off state per socket, exposed as `power` binary
  sensors, with the measured **current** carried as a `current_a` attribute.
- **Per-socket current** sensors, plus a device-wide **total current** input to
  the power estimate.
- **Level control loops** ("Niveau") — a per-loop alarm/fill/drain binary
  sensor.
- **Controller alarm** binary sensor.
- Reliable reads: frame checksum (BCA/BCC) validation and batched, retrying
  reads that tolerate the controller's occasional dropped or corrupt frame.
- HACS brand **icon** and **logo**.
- A ready-made **Lovelace dashboard** (`dashboards/aquarium.yaml`) — sensor
  gauges, socket outlet tiles, and a level/alarm row.
- Standalone `scraper.py` for verifying a controller from the LAN, with a
  `--debug` register dump.

[1.4.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.4.0
[1.3.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.3.0
[1.2.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.2.0
[1.1.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.1.0
[1.0.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.0.0
