"""GHL API client — the documented text protocol (firmware 7.31+, doc v1.0).

The GHL API is a plain-text ``GET``/``SET`` protocol reachable locally over a
raw TCP port (10002) or a WebSocket (``ws://<host>/ghl-api/``). This module
speaks it over TCP (connect-per-command, as the official examples do) and builds
the *same snapshot dict* the SWMBus :class:`~.protocol.Controller` produces, so
every Home Assistant platform works unchanged whichever interface is selected.

Why offer it alongside the raw SWMBus path:

* It exposes each **level sensor** individually (``GET LEVELSENSOR[i] ACTSTATE``)
  with its real name — the per-sensor wet/dry state that the raw registers don't
  cleanly expose — plus **KH Director**, **ION Director** and **flow** values.
* It is a documented, stable protocol: no nibble reverse-engineering.

What it deliberately cannot do: **switch outputs**. ``SWITCHCHANNEL`` is
read-only by design ("a network outage must never leave a tank unheated"), so
socket on/off control stays a raw-SWMBus-only feature. In API mode sockets are
read-only status.

The API is **off by default** on the controller (and after every firmware
update); it must be enabled — read access is enough — in GHL Control Center or
GHL Connect, or every command answers ``NACK (-105)``.
"""
from __future__ import annotations

import socket
from typing import Any

# Imported lazily-safe: protocol.py imports api.py only inside functions, so this
# top-level import back into protocol is fine (api is imported after protocol's
# module body has run).
from .protocol import ProfiluxError, classify_sensor

DEFAULT_API_PORT = 10002

# Resource index ranges the firmware accepts (doc §3). We scan and keep whatever
# answers; a missing index replies NACK (-101).
API_MAX_SENSORS = 32
API_MAX_SWITCHCHANNELS = 64
API_MAX_DOSERS = 32
API_MAX_LEVELSENSORS = 16
API_MAX_IONVALUES = 5
API_MAX_FLOWSENSORS = 4
API_MAX_ILLUMINATION = 32
API_MAX_LIGHTSCENES = 8

# Sensible min/max/step for an editable setpoint, keyed by the sensor's unit.
# Generous ranges; the controller clamps to its own valid band anyway.
_SETPOINT_RANGES: dict[str | None, tuple[float, float, float]] = {
    "°C": (10.0, 40.0, 0.1),
    "pH": (5.0, 9.0, 0.01),
    "°dH": (4.0, 20.0, 0.1),
    "mS/cm": (30.0, 60.0, 0.1),
    "µS/cm": (0.0, 2000.0, 1.0),
    "mg/L": (0.0, 20.0, 0.1),
    None: (0.0, 1000.0, 1.0),
}

NACK_NO_ACCESS = -105        # API off, or read-only and a SET was sent
NACK_NO_INDEX = -101         # this device doesn't have that index / hardware

API_DISABLED_MESSAGE = (
    "the GHL API is disabled (or read-only) on the controller — enable it "
    "(read access is enough) under System → GHL API in GHL Control Center or "
    "GHL Connect; it is off by default and after every firmware update"
)

# LEVELSENSOR ACTSTATE: does a non-zero state mean the sensor is submerged (wet)?
# The doc doesn't pin the polarity down, so it lives here as a single, obvious
# flip point — confirmed against a live controller.
LEVELSENSOR_ACTIVE_IS_WET = True


def _parse_reply(reply: str) -> tuple[str | None, int | None]:
    """Parse one API reply line.

    ``ACK <value>`` → ``(value, None)``; a bare ``ACK`` → ``("", None)``;
    ``NACK (code)`` → ``(None, code)``. Raises on the no-access code.
    """
    reply = (reply or "").strip()
    if reply.startswith("ACK"):
        if "<" in reply and ">" in reply:
            return reply[reply.index("<") + 1 : reply.rindex(">")], None
        return "", None
    if reply.startswith("NACK"):
        code: int | None = None
        try:
            code = int(reply[reply.index("(") + 1 : reply.index(")")])
        except (ValueError, IndexError):
            code = None
        if code == NACK_NO_ACCESS:
            raise ProfiluxError(API_DISABLED_MESSAGE)
        return None, code
    return None, None


class _ApiClientBase:
    """Shared ``raw``/``text``/``number`` on top of a transport's ``_command``."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        pass

    def _command(self, command: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def raw(self, command: str) -> tuple[str | None, int | None]:
        return _parse_reply(self._command(command))

    def text(self, command: str) -> str | None:
        value, _ = self.raw(command)
        return None if value is None else value.strip().strip('"').strip()

    def number(self, command: str) -> float | None:
        value, _ = self.raw(command)
        if value is None:
            return None
        try:
            return float(value.strip().strip('"'))
        except ValueError:
            return None

    def set(self, command: str) -> bool:
        """Send a SET command; return True on ``ACK`` (raw yields a value — even
        empty — only for ACK). Raises the "API disabled / read-only" error on
        ``NACK (-105)`` so callers can surface it."""
        value, _ = self.raw(command)
        return value is not None


class GhlApiClient(_ApiClientBase):
    """GHL API over raw TCP — one short connection per command.

    The TCP port serves only one client at a time, so this is best for one-off
    checks; a continuous poller should prefer :class:`GhlApiWsClient`.
    """

    def __init__(self, host: str, port: int = DEFAULT_API_PORT, timeout: int = 10) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    def _command(self, command: str) -> str:
        try:
            with socket.create_connection((self._host, self._port), self._timeout) as sock:
                sock.settimeout(self._timeout)
                sock.sendall((command + "\n").encode("latin-1", "ignore"))
                buf = b""
                # Replies are a single line; read until newline (or the peer
                # closes / a sane cap, so a misbehaving device can't hang us).
                while b"\n" not in buf and len(buf) < 1024:
                    chunk = sock.recv(256)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as err:
            raise ProfiluxError(
                f"cannot reach the GHL API at {self._host}:{self._port}: {err}"
            ) from err
        return buf.decode("latin-1", "ignore").strip()


class GhlApiWsClient(_ApiClientBase):
    """GHL API over its WebSocket (``ws://<host>/ghl-api/``).

    This is the interface the integration uses: it holds one connection (the
    device allows up to 25) and sends commands as text frames, which is far more
    robust for continuous polling than the single-client TCP port.

    Three documented quirks are handled here:

    * On connect the device sends two greeting lines (``greetings client #x`` and
      ``server->count() #y``) — skipped.
    * Replies go to *every* connected client, and each reply currently arrives
      **twice**. Commands are sent one at a time and the buffer is drained before
      each send, so a stale/duplicate reply can't be mistaken for the next one.
    * A command frame carries no trailing newline (the frame is the delimiter).
    """

    def __init__(self, host: str, timeout: int = 10, read_timeout: float = 4.0) -> None:
        self._url = f"ws://{host}/ghl-api/"
        self._timeout = timeout
        self._read_timeout = read_timeout
        self._ws: Any = None

    def _connect(self) -> None:
        try:
            import websocket  # noqa: PLC0415 - optional dep, shared with the SWMBus transport

            self._ws = websocket.create_connection(self._url, timeout=self._timeout)
            self._ws.settimeout(self._read_timeout)
        except Exception as err:  # noqa: BLE001
            raise ProfiluxError(f"cannot connect to the GHL API at {self._url}: {err}") from err
        # Discard the two greeting lines (and anything else buffered).
        self._drain(0.5)

    def _drain(self, timeout: float) -> None:
        if self._ws is None:
            return
        self._ws.settimeout(timeout)
        try:
            while True:
                try:
                    self._ws.recv()
                except Exception:  # noqa: BLE001 - any read stall/close ends the drain
                    return
        finally:
            if self._ws is not None:
                self._ws.settimeout(self._read_timeout)

    def _command(self, command: str) -> str:
        if self._ws is None:
            self._connect()
        try:
            # Clear any leftover frame (e.g. the duplicate of the previous reply)
            # so it can't be read as this command's answer.
            self._drain(0.05)
            self._ws.send(command)
            for _ in range(12):
                frame = self._ws.recv()
                if isinstance(frame, bytes):
                    frame = frame.decode("latin-1", "ignore")
                line = frame.strip()
                if line.startswith(("ACK", "NACK")):
                    return line
                # Otherwise a greeting/other client's line — keep reading.
        except Exception as err:  # noqa: BLE001
            self.close()
            raise ProfiluxError(f"GHL API WebSocket error: {err}") from err
        return ""

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None


class ApiController:
    """Builds the standard snapshot dict from the GHL API.

    Names (``DESCRIPTION``) rarely change but are one read per entity per poll —
    the biggest slice of poll traffic. A caller can pass a ``name_cache`` dict
    (kept across polls) and ``refresh_names=False`` to reuse cached names; it
    still reads the name of any *newly appeared* resource, and a periodic
    ``refresh_names=True`` poll re-reads them all. Live values are always read,
    so new hardware and changed readings appear every poll.
    """

    def __init__(
        self,
        client: GhlApiClient,
        read_names: bool = True,
        name_cache: dict[str, str | None] | None = None,
        refresh_names: bool = True,
    ) -> None:
        self._c = client
        self._read_names = read_names
        self._name_cache = name_cache if name_cache is not None else {}
        self._refresh_names = refresh_names

    # -- helpers ----------------------------------------------------------
    def _name(self, resource: str) -> str | None:
        if not self._read_names:
            return None
        if not self._refresh_names and resource in self._name_cache:
            return self._name_cache[resource]
        name = self._c.text(f"GET {resource} DESCRIPTION") or None
        self._name_cache[resource] = name
        return name

    # -- device -----------------------------------------------------------
    def device_info(self) -> dict[str, Any]:
        # The API has no model/serial/firmware command, so identity falls back to
        # the host (see ProfiluxEntity). Kept generic on purpose.
        return {"model": "ProfiLux", "sw_version": None, "serial": None}

    # -- sensors (+ KH / ION / flow, which the API newly exposes) ---------
    def sensors(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(API_MAX_SENSORS):
            value = self._c.number(f"GET SENSOR[{i}] ACTVALUE")
            if value is None:
                continue
            name = self._name(f"SENSOR[{i}]")
            label, unit, device_class, decimals = classify_sensor(None, name)
            # The GHL API doesn't expose the sensor *type*, so a pH probe named by
            # location (e.g. "Kalkreaktor", "Technikbecken") isn't recognised by
            # name — and the API returns pH a decimal place too high. An
            # otherwise-unidentified sensor whose value sits in the pH×10 band is
            # almost certainly such a pH probe, so treat it as pH.
            if unit is None and _looks_like_scaled_ph(value):
                label, unit, device_class, decimals = "pH", "pH", None, 2
            value = _fix_ph_scale(label, value)
            out.append(
                {
                    "index": i,
                    "type_id": None,
                    "label": label,
                    "name": name,
                    "value": round(value, decimals),
                    "decimals": decimals,
                    "unit": unit,
                    "device_class": device_class,
                }
            )
        # KH Director (single, no index).
        kh = self._c.number("GET KHDIRECTOR ACTVALUE")
        if kh is not None:
            out.append(_extra_sensor("kh", "KH Director", kh, "°dH", 1))
        # ION Director — up to five measured values.
        for i in range(API_MAX_IONVALUES):
            v = self._c.number(f"GET IONDIRECTOR[{i}] ACTVALUE")
            if v is not None:
                out.append(_extra_sensor(f"ion{i}", f"ION Director {i + 1}", v, None, 2))
        # Flow sensors.
        for i in range(API_MAX_FLOWSENSORS):
            v = self._c.number(f"GET FLOWSENSOR[{i}] ACTFLOW")
            if v is None:
                continue
            name = self._name(f"FLOWSENSOR[{i}]")
            out.append(
                {
                    "index": f"flow{i}",
                    "type_id": None,
                    "label": name or f"Flow {i + 1}",
                    "name": name or f"Flow {i + 1}",
                    "value": round(v, 1),
                    "decimals": 1,
                    "unit": "L/h",
                    "device_class": None,
                }
            )
        # Illumination channels — the brightness each is running at right now (%).
        # Read-only (live control is the master brightness / a light entity).
        for i in range(API_MAX_ILLUMINATION):
            v = self._c.number(f"GET ILLUMINATION[{i}] ACTBRIGHTNESS")
            if v is None:
                continue
            name = self._name(f"ILLUMINATION[{i}]")
            out.append(
                {
                    "index": f"light{i}",
                    "type_id": None,
                    "label": name or f"Light {i + 1}",
                    "name": name or f"Light {i + 1}",
                    "value": round(v),
                    "decimals": 0,
                    "unit": "%",
                    "device_class": None,
                }
            )
        return out

    def master_brightness(self) -> float | None:
        """The illumination master brightness (%) — scales all channels."""
        return self._c.number("GET ILLUMINATION MASTERBRIGHTNESS")

    # -- sockets (read-only: the API cannot switch outputs) ---------------
    def sockets(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(API_MAX_SWITCHCHANNELS):
            state = self._c.number(f"GET SWITCHCHANNEL[{i}] ACTSTATE")
            if state is None:
                continue
            current = self._c.number(f"GET SWITCHCHANNEL[{i}] ACTCURRENT")
            out.append(
                {
                    "index": i,
                    "function": None,   # not exposed / not writable via the API
                    "mode": None,
                    "name": self._name(f"SWITCHCHANNEL[{i}]"),
                    "is_on": state != 0,
                    "current": None if current is None else round(current, 2),
                }
            )
        return out

    # -- dosing pumps -----------------------------------------------------
    def dosing_pumps(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(API_MAX_DOSERS):
            fill = self._c.number(f"GET DOSER[{i}] FILLLEVEL")
            capacity = self._c.number(f"GET DOSER[{i}] CAPACITY")
            name = self._name(f"DOSER[{i}]")
            # The API doesn't expose the schedule mode, so skip empty slots: a
            # doser counts as present when it has a capacity or a name.
            if fill is None and capacity is None and not name:
                continue
            if not name and not capacity:
                continue
            percent = (
                round(fill / capacity * 100) if capacity and fill is not None else None
            )
            out.append(
                {
                    "index": i,
                    "name": name,
                    "fill_ml": None if fill is None else round(fill),
                    "capacity_ml": None if capacity is None else round(capacity),
                    "percent": percent,
                    "mode": None,
                }
            )
        return out

    # -- level sensors — the per-sensor state the API exposes directly ----
    def level_sensors(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(API_MAX_LEVELSENSORS):
            state = self._c.number(f"GET LEVELSENSOR[{i}] ACTSTATE")
            if state is None:
                continue
            active = state != 0
            wet = active if LEVELSENSOR_ACTIVE_IS_WET else not active
            out.append(
                {
                    "index": i,
                    "name": self._name(f"LEVELSENSOR[{i}]"),
                    "state": int(state),
                    "wet": wet,
                }
            )
        return out

    # -- setpoints (editable target values) -------------------------------
    def setpoints(self, sensors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Read the target value (``DESVALUE``) for each regulated source, so it
        can be surfaced as an editable number. Only sources that actually carry a
        setpoint answer; the rest are skipped."""
        out: list[dict[str, Any]] = []
        for s in sensors:
            idx = s["index"]
            if isinstance(idx, int):
                resource = f"SENSOR[{idx}]"
            elif idx == "kh":
                resource = "KHDIRECTOR"
            elif isinstance(idx, str) and idx.startswith("ion"):
                resource = f"IONDIRECTOR[{idx[3:]}]"
            else:
                continue  # flow sensors have no setpoint
            value = self._c.number(f"GET {resource} DESVALUE")
            if value is None:
                continue
            value = _fix_ph_scale(s.get("label"), value)
            lo, hi, step = _SETPOINT_RANGES.get(s.get("unit"), (0.0, 1000.0, 0.1))
            out.append(
                {
                    "key": str(idx),
                    "resource": resource,
                    "name": s.get("name") or s.get("label"),
                    "value": round(value, s.get("decimals", 1)),
                    "unit": s.get("unit"),
                    "device_class": s.get("device_class"),
                    "min": lo,
                    "max": hi,
                    "step": step,
                }
            )
        return out

    def snapshot(self, with_control: bool = False) -> dict[str, Any]:
        sensors = self.sensors()
        sockets = self.sockets()
        levels = self.level_sensors()
        # A dry level sensor anywhere → the same controller-wide fault flag the
        # SWMBus path reports, but here we know exactly which sensor is dry.
        level_fault = None if not levels else any(not lv["wet"] for lv in levels)
        return {
            "device": self.device_info(),
            "alarm": None,          # no controller-wide alarm command in the API
            "sensors": sensors,
            "sockets": sockets,
            "levels": [],           # loops aren't exposed; per-sensor is below
            "level_sensors": levels,
            "level_fault": level_fault,
            "dosing_pumps": self.dosing_pumps(),
            # Setpoints are an extra read per source, so only when control is on.
            "setpoints": self.setpoints(sensors) if with_control else [],
            "master_brightness": self.master_brightness(),
        }


def _looks_like_scaled_ph(value: float | None) -> bool:
    """True if an unidentified sensor value looks like a pH returned ×10 by the
    GHL API. The band (≈ pH 5.0–10.0 before the /10) covers realistic aquarium
    and calcium-reactor pH while excluding ION values, conductivity, etc."""
    return value is not None and 50 <= value <= 100


def _fix_ph_scale(label: str | None, value: float | None) -> float | None:
    """Correct a pH the GHL API returns shifted by a decimal place (e.g. 83.7 for
    8.37). pH is physically 0..14, so divide an out-of-range pH back into range.
    Only touches pH, and only when clearly a power-of-ten too large."""
    if value is None or label != "pH":
        return value
    while value > 14:
        value /= 10
    return value


def _extra_sensor(
    index: str, label: str, value: float, unit: str | None, decimals: int
) -> dict[str, Any]:
    return {
        "index": index,
        "type_id": None,
        "label": label,
        "name": label,
        "value": round(value, decimals),
        "decimals": decimals,
        "unit": unit,
        "device_class": None,
    }


def api_command(host: str, command: str) -> bool:
    """Send one GHL API ``SET`` command over the WebSocket; return True on ACK."""
    with GhlApiWsClient(host) as client:
        return client.set(command)


def api_test_connection(host: str) -> None:
    """Reachability/enabled check for the config flow, over the WebSocket the
    integration uses: one GET must answer."""
    with GhlApiWsClient(host) as client:
        # SENSOR[0] ACTVALUE is the doc's canonical probe. raw() raises the
        # helpful "API disabled" message on NACK (-105); a plain missing index
        # (-101) still proves the API is reachable and enabled.
        value, code = client.raw("GET SENSOR[0] ACTVALUE")
        if value is None and code is None:
            raise ProfiluxError("connected but the GHL API gave no valid reply")
