# ProfiLux — Home Assistant integration

A Home Assistant custom integration for **GHL ProfiLux** aquarium controllers.
It polls the controller on the local network and creates native Home Assistant
entities for **every sensor** and the **on/off state of every power socket**.

Works with the **ProfiLux 3 / 4** over the documented HTTP interface, and with
the **ProfiLux mini** over its WebSocket interface — the local interface is
selectable when you add the integration.

## What it does

- **Auto-discovery** of probes and sockets via the controller's own resource
  counts (`GetSensorCount` / `GetSwitchCount`) — no hard-coded lists.
- **All sensor types** (temperature, pH, redox, conductivity, humidity,
  oxygen, voltage, …) with correct units and per-probe decimal scaling read
  from the controller.
- **Power-socket status** — one read-only `binary_sensor` per socket.
- A controller **alarm** `binary_sensor` and a proper HA **device** (model +
  firmware).

### Socket control (opt-in)

By default the integration is **read-only** — sockets appear as `binary_sensor`
entities (status only), so it can never accidentally switch live equipment.

To control sockets, enable it explicitly: **Settings → Devices & Services →
ProfiLux → Configure → "Enable socket control"**. That adds, per socket:

- an on/off **switch**, and
- an **Auto / On / Off** `select`.

On/off writes the socket's **Function** to "always on" / "always off" on the
controller — the same persistent override the GHL app offers (it survives
reboots, unlike a Maintenance program which reverts after a timeout). **Auto**
restores the socket's original Function, handing control back to the controller
(offered once the socket has been seen un-overridden since startup, so its
automatic Function is known).

> ⚠️ A switch **overrides the controller's automatic control** of whatever is
> plugged in. Only enable this if you understand that forcing, say, a heater or
> return pump on/off bypasses its normal regulation.

## How it talks to the controller

The integration supports three local interfaces, selectable in the config flow:

| Interface | Transport | Use for |
|-----------|-----------|---------|
| `ghl_api` | the **documented GHL API** — plain-text `GET`/`SET` over its WebSocket (`ws://<host>/ghl-api/`) | ProfiLux 4 firmware **7.31+** (recommended) |
| `websocket` | raw SWMBus frames over `ws://<host>/ws` | ProfiLux 4 (fw 7.x), ProfiLux mini |
| `http` | `GET http://<host>/communication.php?dir=enq&code=<code>` → `command=<code>&data=<value>` | older ProfiLux 3 / 4 firmware that still serves `communication.php` |

### GHL API (recommended)

GHL published an [official local API](https://www.aquariumcomputer.com/de/software/ghl-api/)
for ProfiLux firmware 7.31+ — a simple, documented text protocol. Prefer it:
it reads cleanly and exposes things the raw registers don't, including **each
level sensor's individual wet/dry state**, the **KH Director**, up to five
**ION Director** values, and **flow sensors** — all under the names you gave them.

- **Enable it first.** The API is **off by default** (and after every firmware
  update). Turn it on under **System → GHL API** in GHL Control Center or GHL
  Connect. **Read access is enough** for this integration.
- **Talks over the WebSocket.** The integration uses the API's WebSocket
  (`ws://<host>/ghl-api/`, up to 25 clients), not the single-client TCP port —
  so it keeps working even while the GHL app is connected.
- **Read-only for outputs by design.** The API cannot switch outlets ("a network
  outage must never leave a tank unheated"), so **socket on/off control is not
  available in API mode** — use the `websocket` / `http` interface for that.
- No username/password (the API has none — your network is the perimeter; keep
  the device on a trusted LAN and don't forward the API through your router).
- Verify/enable it from the LAN with the bundled scraper — over the WebSocket the
  integration uses (`--api-ws`), or the plain TCP port for a quick manual check:
  ```bash
  python scraper.py 192.168.1.50 --api-ws --api-cmd "GET SENSOR[0] ACTVALUE"
  # -> ACK <24.412>   (NACK (-105) means the API is still switched off)
  ```

### Raw SWMBus (`websocket` / `http`)

Every value on a ProfiLux is also addressable by a numeric *code* over the raw
SWMBus protocol. Pick the one your controller answers on — the bundled
`scraper.py` auto-detects it. Recent ProfiLux 4 firmware (e.g. 7.49) drops
`communication.php` (returns 404) and only speaks WebSocket, so use `websocket`
there. These interfaces additionally support **socket on/off control**.

The GHL code map and block-offset addressing were cross-checked against
[`cjburchell/profilux-go`](https://github.com/cjburchell/profilux-go); the
WebSocket framing matches
[`PascalGohl/ha-profilux-mini`](https://github.com/PascalGohl/ha-profilux-mini).
See `custom_components/profilux/protocol.py` for the commented implementation.

### ProfiLux 4 firmware notes

ProfiLux 4 lays out some registers differently from the ProfiLux 3 code map, so
the integration only relies on the registers that read reliably there:

- **Sensors** are detected by whether their *value* register answers, and
  classified by GHL type id when it's valid, otherwise by the probe's name
  (e.g. `pH`, `Redox`, `Leitwert`). Values are scaled by fixed decimals per kind.
- **Frame checksums (BCA/BCC) are validated**, so the occasional corrupted or
  merged reply the controller emits under rapid polling is rejected and retried.
- **Sockets** are read from the per-socket state register (physical sockets)
  plus any named virtual/expansion outputs. Higher switching channels beyond the
  first bank derive their on/off state and current from the powerbar's next
  current bank.

## Requirements

- Home Assistant 2023.1 or newer.
- A ProfiLux reachable on the LAN with its web interface enabled (port 80).
- Controller username/password if your controller requires them (leave blank if
  local access is unrestricted).

## Test it first (recommended)

From any machine on the same network as the controller:

```bash
# auto-detects whether your controller answers on HTTP or WebSocket:
python scraper.py 192.168.1.50 --username admin --password YOURPASS
```

It prints the working interface, the device info, all sensor readings, and each
socket's on/off state. Add `--json` for the raw structure. The WebSocket
interface additionally needs `pip install "websocket-client>=1.6.0"`.

Whatever interface the scraper reports as working is the one to select in the
Home Assistant config flow.

## Install

### Manual

1. Copy `custom_components/profilux/` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → ProfiLux**, then enter
   the host, credentials and interface (`http` for a ProfiLux 3/4).

### Via HACS (custom repository)

1. HACS → Integrations → ⋯ → **Custom repositories**.
2. Add this repository URL with category **Integration**.
3. Install **ProfiLux**, restart HA, then add the integration as above.

> **Migrating from `PascalGohl/ha-profilux-mini`?** That component (domain
> `profilux_mini`, temperature + pH only) is superseded by this one. Remove the
> old integration and its `custom_components/profilux_mini/` folder to avoid two
> integrations polling the same controller.

## Dashboard

### Auto-generating strategy (recommended)

The integration ships a Lovelace **strategy** and registers it as a frontend
resource automatically on setup, so a dashboard can build **itself** from your
ProfiLux entities — no entity IDs to type, and it restructures itself as
entities are added, renamed or removed.

It lays out, in order: **sensor gauges**, **power & current** (total power +
current, a 24 h trend, per-socket draw), **switching channels** as outlet tiles,
**dosing-pump** fill levels, and a **level & alarm** section (each loop's status
with its min/max float switches).

**Install it (one time):**

1. Make sure the integration is installed and Home Assistant has been restarted
   at least once since — that's when the strategy resource is registered.
2. Go to **Settings → Dashboards → Add dashboard → New dashboard from scratch**.
   Give it a title (e.g. *Aquarium*) and an icon (e.g. `mdi:fishbowl`), open it.
3. Top-right ⋯ → **Edit dashboard**; if asked, **Take control**. Then ⋯ again →
   **Raw configuration editor**.
4. Delete everything in the editor and paste exactly:
   ```yaml
   strategy:
     type: custom:profilux
   ```
5. **Save**. The dashboard renders itself from your current entities.

Optional — give the view a custom title:
```yaml
strategy:
  type: custom:profilux
  title: Reef Tank
```

> **`custom:profilux` not found, or "Timeout waiting for strategy element
> `ll-strategy-dashboard-profilux`"?** The frontend just hasn't loaded the
> strategy module yet — it's cached, especially in the **mobile app**. In order:
>
> 1. **Hard-refresh** the browser (Ctrl/Cmd-Shift-R). In the companion app:
>    **App configuration → Debugging → Reset frontend cache**, then reopen.
> 2. Confirm the integration is loaded (Settings → Devices & Services → ProfiLux)
>    and **restart Home Assistant** once — the module is registered on setup.
> 3. Still stuck? Register the module as a **dashboard resource** explicitly:
>    **Settings → Dashboards → ⋯ (top-right) → Resources → Add resource**, URL
>    `/profilux_frontend/profilux-strategy.js`, type **JavaScript Module**, save,
>    then hard-refresh. (The module is safe to load twice — it guards its own
>    registration.)

### Static YAML dashboard

Prefer a hand-editable copy? A ready-made one ships at
[`dashboards/aquarium.yaml`](dashboards/aquarium.yaml) — sensor **gauges**,
socket **outlet tiles**, a **power & current** section, **dosing-pump** fill
levels, and a **level & alarm** row.

> Home Assistant does not let an integration auto-create a user dashboard, so
> this can't install *itself* — but it's versioned here alongside the code so it
> stays in sync. Apply it either way:
>
> - **Quick:** Settings → Dashboards → *Add dashboard* → *New dashboard* → take
>   control → ⋯ → **Raw configuration editor** → paste the file contents.
> - **YAML mode:** reference the file from `configuration.yaml`:
>   ```yaml
>   lovelace:
>     dashboards:
>       profilux-aquarium:
>         mode: yaml
>         title: Aquarium
>         icon: mdi:fishbowl
>         filename: ha-ghl-profilux/dashboards/aquarium.yaml
>   ```
>
> The `entity:` IDs in the file are generic placeholders (`socket_1`,
> `dosing_pump_1`, `level_1`, …) — swap in your own entity IDs, or just use the
> auto-generating strategy above, which needs none.

## Versioning

Releases follow [Semantic Versioning](https://semver.org); see
[`CHANGELOG.md`](CHANGELOG.md). The version in `manifest.json` is the source of
truth. **For HACS to show a version number (e.g. `1.0.0`) instead of a commit
hash, publish a GitHub Release** whose tag matches the manifest version
(`v1.0.0`); HACS reads its versions from GitHub Releases.

## Polling

The integration opens one short conversation every 60 seconds and reads all
sensors and sockets in that pass.
