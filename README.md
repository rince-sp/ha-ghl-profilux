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

### Read-only first phase

This phase is intentionally **read-only**. Power sockets appear as
`binary_sensor` entities (status only) so the integration can never accidentally
switch live aquarium equipment. Turning sockets into controllable `switch`
entities is a deliberate later phase.

## How it talks to the controller

Every value on a ProfiLux is addressed by a numeric *code*. There are two local
ways to read those codes, and both are supported:

| Interface | Transport | Use for |
|-----------|-----------|---------|
| `websocket` | raw SWMBus frames over `ws://<host>/ws` | ProfiLux 4 (fw 7.x), ProfiLux mini |
| `http` | `GET http://<host>/communication.php?dir=enq&code=<code>` → `command=<code>&data=<value>` | older ProfiLux 3 / 4 firmware that still serves `communication.php` |

Pick the one your controller answers on — the bundled `scraper.py` auto-detects
it. Recent ProfiLux 4 firmware (e.g. 7.49) drops `communication.php` (returns
404) and only speaks WebSocket, so use `websocket` there.

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
  plus any named virtual/expansion outputs. A named output whose state isn't in
  the standard register (e.g. an Orphek light channel or a virtual switch) is
  listed with an unknown state for now.

## Requirements

- Home Assistant 2023.1 or newer.
- A ProfiLux reachable on the LAN with its web interface enabled (port 80).
- Controller username/password if your controller requires them (leave blank if
  local access is unrestricted).

## Test it first (recommended)

From any machine on the same network as the controller:

```bash
# auto-detects whether your controller answers on HTTP or WebSocket:
python scraper.py 192.168.1.221 --username admin --password YOURPASS
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

## Polling

The integration opens one short conversation every 60 seconds and reads all
sensors and sockets in that pass.
