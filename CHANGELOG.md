# Changelog

All notable changes to this integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.9.0] - 2026-07-23

### Fixed
- **Level floats: wet is now the good state, dry is the fault.** The float
  sensors are modelled as *problem* sensors — a dry float reads as a fault (red)
  and a wet, submerged float reads as OK (green/neutral), instead of a wet float
  showing as an alert.
- **Level floats no longer report a fabricated wet/dry state.** The individual
  min/max float state is not exposed by this controller firmware over the local
  protocol — the float inputs are level-sensor inputs, a namespace separate from
  the digital inputs, and the digital-input mask the integration read for them is
  a constant zero (which made every float read "wet"). They now read **unknown**
  rather than a wrong value, and carry a `live_state` attribute noting the state
  isn't reported by the controller. The confirmed min/max **sensor number**
  (decoded from each loop's source configuration and cross-checked against a
  controller backup) is still surfaced as an attribute.

### Changed
- **Reworked dashboard layout.** The auto-generating strategy (and the bundled
  example) now use a two-column layout: the **controller alarm** spans the full
  width at the top; **sensors** sit on the left with **power & current** on the
  right; then full-width rows for **switching channels**, **level control loops**
  and **dosing pumps**.
- **Socket and level cards behave like area cards.** A socket card shows its
  name, a state-coloured icon and the current power draw on the face; tapping it
  opens the toggle and the per-outlet power. A level-loop card shows its name and
  a state-coloured icon; tapping it lists the sensors assigned to the loop.
- The level **alarm** sensor now carries the loop's assigned float sensors as an
  attribute, so the loop's more-info dialog shows them.

## [1.8.0] - 2026-07-22

### Fixed
- **Level float sensors were inverted** — a float sitting in water now reads
  **wet** (the input bit is cleared when submerged on this controller).

### Changed
- **Per-outlet power in the socket dialog.** Each socket switch now carries its
  `current_a` and `power_w` as attributes, so tapping a socket tile opens a
  dialog with the on/off toggle *and* that outlet's power draw. The main page
  keeps the overall power/current totals and the 24 h graph; the per-socket
  current row is gone from the main page (it lives in each socket's dialog now).
- **Controller alarm pinned to the top** of the auto-generating dashboard.
- The dashboard strategy pairs level floats/alarms to their loop by name
  (robust to differing entity-id prefixes).

## [1.7.1] - 2026-07-22

### Fixed
- **Strategy dashboard now finds socket switches** even when the switch and the
  status binary_sensor carry different entity-id prefixes (e.g. one created
  before the device gained an area name). Sockets are matched by name, so
  control-enabled sockets render as tap-to-toggle tiles instead of falling back
  to the read-only status sensor.

## [1.7.0] - 2026-07-22

### Added
- **Per-socket Auto / On / Off control.** With socket control enabled, each
  socket also gets a `select` with **Auto / On / Off**: On/Off force the socket
  (as the switch does), and **Auto** hands control back to the controller by
  restoring the socket's remembered automatic Function. "Auto" is offered only
  once the socket has been seen un-overridden since startup, so its automatic
  Function is known.

## [1.6.0] - 2026-07-22

### Added
- **Socket control (opt-in).** Enable it under the integration's *Configure*
  options to add an on/off **switch** per socket. On/off writes the socket's
  **Function** to "always on" / "always off" — a persistent override (survives a
  reboot, unlike a Maintenance program which reverts after a timeout). Each
  socket's automatic Function is remembered so control can be handed back to the
  controller. Off by default; a switch overrides the controller's automatic
  control of whatever is plugged in.

### Changed
- The protocol gained a write path (`set_int`, `Controller.set_socket_function`),
  confirmed by read-back since this firmware doesn't acknowledge writes. Writes
  no longer block waiting for an ack that never arrives.
- The socket snapshot now includes each socket's Function and derived mode
  (auto / on / off).

## [1.5.2] - 2026-07-22

### Changed
- **Prettier, fuller dashboards.** The auto-generating strategy is restructured
  into clear sections — sensor gauges, power & current (totals + 24 h trend +
  per-socket draw), switching channels, dosing pumps (with % full), and level
  loops paired with their min/max float switches — with icons and colour. The
  bundled example dashboard mirrors the same layout, and the README documents
  how to install the strategy dashboard step by step.
- The strategy module now guards its own custom-element registration, so it is
  safe to load both as a frontend extra and as a Lovelace resource (a reliable
  fallback when the mobile app caches the frontend). The README's troubleshooting
  covers the "Timeout waiting for strategy element" case.

## [1.5.1] - 2026-07-22

### Changed
- Documentation and the bundled example dashboard now use generic placeholder
  names and entity IDs instead of one installation's specific device names.

## [1.5.0] - 2026-07-22

### Added
- **Level-loop float sensors.** Each level control loop now exposes its assigned
  float switches as **min / max** binary sensors (wet/dry), decoded from the
  loop's source configuration. The loop's status sensor gains `active` and
  `sensors` attributes, so a one- vs two-sensor loop is reflected directly.
- **Dynamic discovery & live names.** Sensors, sockets, dosing pumps and level
  sensors are now discovered on every poll, so a pump activated or a socket
  added on the controller appears without reloading the integration; likewise a
  **rename** on the controller updates the entity's friendly name on the next
  poll (its entity_id stays stable).

### Changed
- The diagnostic dump reads the confirmed level source/props scheme
  (`801 + loop*1000 + sub*4`) and decodes each sub-control's sensor number.

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
  array (code `10128`) only carries sockets 0–15, so higher channels drew a
  current the GHL app showed but Home Assistant reported as "unknown". That
  current lives in the next powerbar bank at the `+1000` mega-block offset; the
  integration now reads every bank (socket `i` → bank `i // 16`, field `i % 16`),
  so those channels report their real current and switch on/off correctly.

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

[1.9.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.9.0
[1.8.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.8.0
[1.7.1]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.7.1
[1.7.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.7.0
[1.6.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.6.0
[1.5.2]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.5.2
[1.5.1]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.5.1
[1.5.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.5.0
[1.4.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.4.0
[1.3.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.3.0
[1.2.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.2.0
[1.1.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.1.0
[1.0.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v1.0.0
