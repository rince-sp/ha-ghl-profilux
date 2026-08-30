# Changelog

All notable changes to this integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.2.2] - 2026-08-30

### Fixed
- **Dashboard: the "Level sensor fault" sensor no longer appears among the
  switching channels.** The strategy classified sockets by matching text in the
  entity id, but entity ids are derived from the (localised, user-editable)
  friendly name — so the controller-wide level-fault sensor slipped past the
  socket filter and rendered as the first Schaltkanäle tile, while going missing
  from the level section. Sockets are now identified by `device_class: power`
  and the level entities by `device_class: problem`, which is name-independent.
  As a side effect, a location-named level sensor (e.g. "Technikbecken") is now
  grouped with the level section instead of leaking into the sockets.

### Added
- **Switching channels are ordered by hardware channel number** (1, 2, 3 …)
  instead of alphabetically by name. Each socket now exposes a `channel`
  attribute (its 1-based controller channel) that the dashboard sorts on.

## [2.2.1] - 2026-08-30

### Fixed
- **pH still read a decimal place too high on some controllers** (e.g. 84.1
  instead of 8.41), even in GHL API mode. The pH×10 detection ran *after* the
  unit-count probe, so a pH sensor whose firmware answered `ACTVALUE[1]`/`[2]`
  was mis-detected as temperature/conductivity and never rescaled. The pH value
  check now runs **first** — a raw reading of ≈50–100 can't be an aquarium
  temperature or seawater conductivity, so it is unambiguously a scaled pH — and
  no longer depends on the unit probe.
- **The per-sensor type dropdowns did not appear under *Configure*.** They were
  gated to GHL API mode and also blocked by a stale translation file. The
  dropdowns now show on **every** interface (a manual type override also helps on
  SWMBus, where some firmwares report an unreliable type register), and the
  options texts — including the new *GHL API control* toggle — render correctly
  again (the shipped `translations/en.json` had fallen behind `strings.json`,
  which is the file Home Assistant actually displays).

### Changed
- The pH×10 correction and the per-sensor type override now apply on the
  **SWMBus** (HTTP / WebSocket) interfaces too, not only the GHL API — a pH probe
  named by location is corrected and can be pinned there as well.

## [2.2.0] - 2026-08-30

### Added
- **Per-sensor type override (GHL API mode).** The API doesn't report a sensor's
  type, so under *Configure* each sensor now has a dropdown — **auto / pH /
  temperature / redox / conductivity / oxygen / humidity**. "auto" keeps the
  automatic detection; any other choice pins the type (unit, decimals and pH
  scaling) explicitly, so an oddly-named probe is always right.
- **Unit-count probe.** For a sensor the name doesn't identify, the integration
  now probes how many units it offers and identifies **temperature** (2 units:
  °C/°F) and **seawater conductivity** (3 units) deterministically — so a probe
  named by location (e.g. "Wärmetauscher") is classified correctly without a name
  match. The result is cached like names.

## [2.1.2] - 2026-08-30

### Fixed
- **GHL API: pH probes named by location** (e.g. "Kalkreaktor", "Technikbecken")
  were not recognised as pH — the API doesn't expose the sensor type — so they
  showed a decimal place too high (66.3, 84.1 instead of 6.63, 8.41). An
  otherwise-unidentified sensor whose value sits in the pH×10 band is now treated
  as pH and corrected. (v2.1.1 only caught sensors literally named "pH".)
- **Dashboard: unavailable/unknown sensors no longer render as broken gauges.**
  The auto-generating strategy skips sensor entities without a live state (e.g.
  orphans left over from switching interface), so they don't clutter the layout.

## [2.1.1] - 2026-08-28

### Fixed
- **GHL API: pH read a decimal place too high** (e.g. 83.7 instead of 8.37). The
  API returns pH shifted by a power of ten on some firmware; since pH is
  physically 0–14, an out-of-range pH is now divided back into range. Applies to
  both the pH sensor and its setpoint; other sensors are untouched. (The raw
  SWMBus interface was already correct.)

## [2.1.0] - 2026-08-27

### Added
- **GHL API control (opt-in).** A new *Configure* option, **Enable GHL API
  control**, exposes entities that write to the controller over the API. It needs
  the API set to **full access**. Off by default. It adds:
  - **Setpoints** — editable target values (`number`) for temperature, pH, the KH
    Director and ION Director values (`SET … DESVALUE`).
  - **Aquarium actions** — **Feed pause**, **Water change** and **Maintenance** as
    switches (`SPECIALFUNCTION`), plus **KH/ION measurement** and a 5-minute
    **Thunderstorm** as buttons.
  - **Lighting** — the illumination **master brightness** as a dimmable `light`,
    per-channel brightness as `%` sensors, and an eight-slot **Light scene**
    selector. (Also works for Mitras luminaires that expose illumination.)
- The auto-generating dashboard gains **Sollwerte** (setpoints), **Beleuchtung**
  (lighting) and **Aktionen** (actions) sections.

### Changed
- **Smarter API polling.** Names (`DESCRIPTION`) are cached and only re-read every
  ~20 polls (and immediately for any newly-appeared resource) instead of every
  poll — roughly halving API traffic. Live values are still read every poll, so
  new hardware and changing readings appear right away.

## [2.0.0] - 2026-07-31

### Added
- **GHL API interface (new, recommended).** Alongside the existing raw SWMBus
  transports (WebSocket / HTTP), the integration can now talk the **documented
  GHL API** — the plain-text `GET`/`SET` protocol GHL published for ProfiLux
  firmware 7.31+. Pick it in the config flow ("Interface: GHL API"). It talks the
  API over its **WebSocket** (`ws://<host>/ghl-api/`, up to 25 clients) — far more
  robust for continuous polling than the single-client TCP port — handling the
  documented quirks (skip the greeting lines, ignore the duplicate reply). It
  must be enabled on the controller first (**System → GHL API** in GHL Control
  Center / GHL Connect; it is off by default and after every firmware update).
- **True per-sensor level states.** In API mode each level sensor is read
  individually (`GET LEVELSENSOR[i] ACTSTATE`) with its own name and exposed as a
  binary sensor — **wet = OK (green), dry = fault (red)** — instead of the
  loop-level guess the raw registers only allow.
- **KH Director, ION Director and flow sensors.** API mode surfaces the KH
  Director value, up to five ION Director values (e.g. calcium, magnesium) and
  flow sensors as additional sensors.
- Standalone `scraper.py` gains `--api-cmd "GET SENSOR[0] ACTVALUE"` to send a
  single GHL API line — over TCP by default, or over the WebSocket with
  `--api-ws` (the transport the integration uses) — handy for enabling/verifying
  the API.

### Notes
- **Socket on/off control stays SWMBus-only.** The GHL API is read-only for
  outputs by design ("a network outage must never leave a tank unheated"), so in
  API mode sockets are read-only status. To switch sockets, use the WebSocket /
  HTTP interface, which keeps the on/off switch and Auto/On/Off select.
- API mode reads no model/serial/firmware (the API exposes none), so the device
  is identified by its host.

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

[2.2.2]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.2.2
[2.2.1]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.2.1
[2.2.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.2.0
[2.1.2]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.1.2
[2.1.1]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.1.1
[2.1.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.1.0
[2.0.0]: https://github.com/rince-sp/ha-ghl-profilux/releases/tag/v2.0.0
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
