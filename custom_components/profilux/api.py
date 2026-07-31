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


class GhlApiClient:
    """Talks the GHL API over TCP (one short connection per command)."""

    def __init__(self, host: str, port: int = DEFAULT_API_PORT, timeout: int = 10) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    def __enter__(self) -> "GhlApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def _exchange(self, command: str) -> str:
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

    def raw(self, command: str) -> tuple[str | None, int | None]:
        """Return ``(value, None)`` for ``ACK <value>`` / ``("", None)`` for a
        bare ``ACK``; ``(None, code)`` for ``NACK (code)``. Raises on no-access."""
        reply = self._exchange(command)
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


class ApiController:
    """Builds the standard snapshot dict from the GHL API."""

    def __init__(self, client: GhlApiClient, read_names: bool = True) -> None:
        self._c = client
        self._read_names = read_names

    # -- helpers ----------------------------------------------------------
    def _name(self, resource: str) -> str | None:
        if not self._read_names:
            return None
        return self._c.text(f"GET {resource} DESCRIPTION") or None

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
        return out

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

    def snapshot(self) -> dict[str, Any]:
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
        }


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


def api_test_connection(host: str, port: int = DEFAULT_API_PORT) -> None:
    """Reachability/enabled check for the config flow: one GET must answer."""
    with GhlApiClient(host, port) as client:
        # SENSOR[0] ACTVALUE is the doc's canonical probe. raw() raises the
        # helpful "API disabled" message on NACK (-105); a plain missing index
        # (-101) still proves the API is reachable and enabled.
        value, code = client.raw("GET SENSOR[0] ACTVALUE")
        if value is None and code is None:
            raise ProfiluxError("connected but the GHL API gave no valid reply")
