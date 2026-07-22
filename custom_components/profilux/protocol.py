"""GHL ProfiLux SWMBus protocol with pluggable transport.

Every value on a ProfiLux controller is addressed by a numeric *code*. There are
two local ways to read those codes, and this module supports both behind a
common :class:`Transport` interface:

* ``http``  – the documented ProfiLux 3/4 interface:
  ``GET /communication.php?dir=enq&code=<code>`` returns
  ``command=<code>&data=<value>`` with the value already decoded to a decimal
  integer (or plain text for names). This is the primary path for a ProfiLux 4.

* ``websocket`` – the ProfiLux mini tunnels the raw binary SWMBus frames over
  ``ws://<host>/ws``. Kept as a fallback because some controllers only answer
  there.

The high-level :class:`Controller` (device info / sensors / sockets) is written
once against the transport interface, so it behaves identically either way.

Everything here is synchronous and always called from an executor thread by the
coordinator. Code map cross-checked against ``cjburchell/profilux-go``; the
WebSocket framing matches the known-good ``PascalGohl/ha-profilux-mini``.
"""
from __future__ import annotations

import base64
import logging
import urllib.error
import urllib.request
from typing import Any

_LOGGER = logging.getLogger(__name__)

INTERFACE_HTTP = "http"
INTERFACE_WEBSOCKET = "websocket"
INTERFACES = [INTERFACE_HTTP, INTERFACE_WEBSOCKET]

# --- Code map (subset we need) -------------------------------------------
CODE_SOFTWAREVERSION = 0
CODE_PRODUCTID = 2
CODE_SERIALNUMBER = 6

CODE_SENSOR_TYPE = 25          # + block(i, 8, 24)
CODE_SENSOR_DISPLAYMODE = 27   # + block(i, 8, 24); low nibble = decimal places
CODE_SENSOR_VALUE = 10000      # + block(i, 8, 8); raw integer, needs scaling
CODE_SENSOR_NAME = 18000       # + block(i, 32, 1); text

CODE_SOCKET_STATE = 10100      # + block(i, 24, 1); 0 = off, else on
CODE_SOCKET_NAME = 18064       # + block(i, 64, 1); text

CODE_GET_SENSOR_COUNT = 10500
CODE_GET_SWITCH_COUNT = 10501
CODE_IS_ALARM = 10090

MAX_SENSORS = 16
MAX_SOCKETS = 24
MEGA_BLOCK_SIZE = 1000

# type id -> (label, unit, HA device_class or None), from the GHL sensor map.
SENSOR_TYPES: dict[int, tuple[str, str | None, str | None]] = {
    1: ("Temperature", "°C", "temperature"),
    2: ("pH", "pH", None),
    3: ("Redox", "mV", None),
    4: ("Conductivity", "µS/cm", None),
    5: ("Conductivity", "mS/cm", None),
    6: ("Sensor", None, None),
    7: ("Humidity", "%", "humidity"),
    8: ("Air Temperature", "°C", "temperature"),
    9: ("Oxygen", "mg/L", None),
    10: ("Voltage", "V", "voltage"),
}

# ProfiLux product ids -> model name (older models; newer ones fall back).
PRODUCT_IDS: dict[int, str] = {
    2: "ProfiLux II",
    3: "ProfiLux Plus II",
    4: "ProfiLux Plus II Ex",
    5: "ProfiLux II Terra",
    6: "ProfiLux II Ex",
    7: "ProfiLux II Light",
    8: "ProfiLux II Outdoor",
    11: "ProfiLux III",
    12: "ProfiLux III Ex",
}


class ProfiluxError(Exception):
    """Raised when the controller cannot be reached or answers nonsense."""


def _block_offset(index: int, block_count: int, block_size: int) -> int:
    """GHL block addressing (mirrors ``getOffset`` in profilux-go)."""
    return ((index % block_count) * block_size) + ((index // block_count) * MEGA_BLOCK_SIZE)


def _sensor_offset(index: int) -> int:
    return _block_offset(index, 8, 24)


# =========================================================================
# Transports
# =========================================================================


class Transport:
    """Reads a single code as an int or as text."""

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def get_int(self, code: int, signed: bool = True) -> int | None:
        raise NotImplementedError

    def get_text(self, code: int) -> str | None:
        raise NotImplementedError


class HttpTransport(Transport):
    """ProfiLux 3/4 ``communication.php`` HTTP interface."""

    def __init__(self, host: str, username: str, password: str, timeout: int = 10) -> None:
        self._base = f"http://{host}/communication.php"
        self._timeout = timeout
        self._headers: dict[str, str] = {}
        if username or password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"

    def _raw(self, code: int) -> str | None:
        url = f"{self._base}?dir=enq&code={code}"
        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("latin-1", "ignore").strip()
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise ProfiluxError("access denied (check username/password)") from err
            raise ProfiluxError(f"HTTP {err.code} for code {code}") from err
        except (urllib.error.URLError, OSError) as err:
            raise ProfiluxError(f"cannot reach {self._base}: {err}") from err

        if body == "Access Denied":
            raise ProfiluxError("access denied (check username/password)")

        # Expected: "command=<code>&data=<value>"
        try:
            command_part, data_part = body.split("&", 1)
            command = int(command_part.split("=", 1)[1])
            data = data_part.split("=", 1)[1]
        except (ValueError, IndexError):
            return None

        if command != code or data.startswith("NACK"):
            return None
        return data

    def get_int(self, code: int, signed: bool = True) -> int | None:
        data = self._raw(code)
        if data is None:
            return None
        try:
            return int(data)
        except ValueError:
            return None

    def get_text(self, code: int) -> str | None:
        data = self._raw(code)
        if data is None:
            return None
        text = data.strip()
        return text or None


# --- WebSocket / raw SWMBus framing (ProfiLux mini) ----------------------
SOH = 0x01
STX = 0x02
ENQ = 0x05
ETX = 0x03
EOT = 0x04
CODE_OFFSET_SAVE = 0x40
CODE_OFFSET_NOSAVE = 0x60
DATA_OFFSET = 0x30
SLAVE_ADDR = 80
MASTER_ADDR = 145


def _checksum(data: list[int], length: int) -> int:
    total = sum(data[:length]) & 0xFF
    return total if total >= 32 else total + 32


def _encode_code(code: int) -> list[int]:
    nibbles: list[int] = []
    while True:
        nibbles.append((code & 0xF) | CODE_OFFSET_SAVE)
        code >>= 4
        if code == 0:
            break
    return nibbles


def _make_enquiry(code: int) -> bytes:
    header = [SOH, SLAVE_ADDR, MASTER_ADDR]
    frame = header + [_checksum(header, 3), STX] + _encode_code(code) + [ENQ, ETX]
    frame += [_checksum(frame, len(frame)), EOT]
    return bytes(frame)


def _parse_response(data: bytes) -> tuple[int, list[int]] | None:
    b = list(data)
    if len(b) < 6 or b[0] != SOH or b[4] != STX:
        return None
    if b[1] < 80 or b[2] < 80:
        return None
    d = 5
    code_offset = b[d] & 0xF0
    if code_offset not in (CODE_OFFSET_SAVE, CODE_OFFSET_NOSAVE):
        return None
    code_nibbles: list[int] = []
    while d < len(b) and (b[d] & 0xF0) == code_offset:
        code_nibbles.append(b[d] & 0x0F)
        d += 1
    data_nibbles: list[int] = []
    while d < len(b) and (b[d] & 0xF0) == DATA_OFFSET:
        data_nibbles.append(b[d] & 0x0F)
        d += 1
    code = sum(n << (4 * i) for i, n in enumerate(code_nibbles))
    return code, data_nibbles


def _nibbles_to_int(nibbles: list[int], signed: bool = True) -> int:
    raw = sum(n << (4 * i) for i, n in enumerate(nibbles))
    if signed and len(nibbles) <= 4 and raw >= 0x8000:
        raw -= 0x10000
    return raw


def _nibbles_to_text(nibbles: list[int]) -> str:
    chars: list[str] = []
    for i in range(0, len(nibbles) - 1, 2):
        byte = nibbles[i] | (nibbles[i + 1] << 4)
        if byte == 0:
            break
        if 32 <= byte < 127:
            chars.append(chr(byte))
    return "".join(chars).strip()


class WebSocketTransport(Transport):
    """ProfiLux mini SWMBus-over-WebSocket interface (``ws://<host>/ws``)."""

    def __init__(self, host: str, username: str, password: str, timeout: int = 10) -> None:
        self._url = f"ws://{host}/ws"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}
        self._timeout = timeout
        self._ws: Any = None

    def __enter__(self) -> "WebSocketTransport":
        try:
            import websocket  # noqa: PLC0415 - optional dependency, only for this transport

            self._ws = websocket.create_connection(
                self._url, header=self._auth, timeout=self._timeout
            )
        except Exception as err:  # noqa: BLE001
            raise ProfiluxError(f"cannot connect to {self._url}: {err}") from err
        return self

    def __exit__(self, *exc: object) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    def _read_code(self, code: int) -> list[int] | None:
        try:
            self._ws.send_binary(_make_enquiry(code))
        except Exception as err:  # noqa: BLE001
            raise ProfiluxError(f"send failed for code {code}: {err}") from err
        for _ in range(4):
            try:
                reply = self._ws.recv()
            except Exception as err:  # noqa: BLE001
                raise ProfiluxError(f"recv failed for code {code}: {err}") from err
            if isinstance(reply, str):
                reply = reply.encode("latin-1", "ignore")
            parsed = _parse_response(reply)
            if parsed and parsed[0] == code:
                return parsed[1]
        return None

    def get_int(self, code: int, signed: bool = True) -> int | None:
        nibbles = self._read_code(code)
        return None if nibbles is None else _nibbles_to_int(nibbles, signed=signed)

    def get_text(self, code: int) -> str | None:
        nibbles = self._read_code(code)
        if nibbles is None:
            return None
        return _nibbles_to_text(nibbles) or None


def make_transport(interface: str, host: str, username: str, password: str) -> Transport:
    if interface == INTERFACE_WEBSOCKET:
        return WebSocketTransport(host, username, password)
    return HttpTransport(host, username, password)


# =========================================================================
# High-level controller (transport-agnostic)
# =========================================================================


class Controller:
    """Reads device info, all sensors, and all sockets via a transport."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def _count(self, code: int, cap: int) -> int:
        count = self._t.get_int(code, signed=False)
        if count is None or count <= 0:
            return 0
        return min(count, cap)

    def device_info(self) -> dict[str, Any]:
        version_raw = self._t.get_int(CODE_SOFTWAREVERSION, signed=False)
        product_id = self._t.get_int(CODE_PRODUCTID, signed=False)
        serial = self._t.get_int(CODE_SERIALNUMBER, signed=False)
        sw_version = None if version_raw is None else f"{version_raw / 100:.2f}"
        if product_id is None:
            model = "ProfiLux"
        else:
            model = PRODUCT_IDS.get(product_id, f"ProfiLux (id {product_id})")
        return {"model": model, "sw_version": sw_version, "serial": serial}

    def sensors(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i in range(self._count(CODE_GET_SENSOR_COUNT, MAX_SENSORS)):
            offset = _sensor_offset(i)
            type_id = self._t.get_int(CODE_SENSOR_TYPE + offset, signed=False)
            if not type_id:  # 0 == "None" == not populated
                continue

            decimals_raw = self._t.get_int(CODE_SENSOR_DISPLAYMODE + offset, signed=False)
            decimals = (decimals_raw & 0x0F) if decimals_raw is not None else 1

            raw = self._t.get_int(CODE_SENSOR_VALUE + _block_offset(i, 8, 8))
            value = None if raw is None else round(raw / (10 ** decimals), decimals)

            name = self._t.get_text(CODE_SENSOR_NAME + _block_offset(i, 32, 1))
            label, unit, device_class = SENSOR_TYPES.get(type_id, (f"Sensor {i + 1}", None, None))
            result.append(
                {
                    "index": i,
                    "type_id": type_id,
                    "label": label,
                    "name": name,
                    "value": value,
                    "decimals": decimals,
                    "unit": unit,
                    "device_class": device_class,
                }
            )
        return result

    def sockets(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i in range(self._count(CODE_GET_SWITCH_COUNT, MAX_SOCKETS)):
            state = self._t.get_int(CODE_SOCKET_STATE + _block_offset(i, 24, 1), signed=False)
            if state is None:
                continue
            name = self._t.get_text(CODE_SOCKET_NAME + _block_offset(i, 64, 1))
            result.append({"index": i, "name": name, "is_on": state != 0})
        return result

    def alarm(self) -> bool | None:
        raw = self._t.get_int(CODE_IS_ALARM, signed=False)
        return None if raw is None else raw != 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "device": self.device_info(),
            "alarm": self.alarm(),
            "sensors": self.sensors(),
            "sockets": self.sockets(),
        }


def fetch_all(host: str, username: str, password: str, interface: str = INTERFACE_HTTP) -> dict[str, Any]:
    """Read device info, every populated sensor, and every socket.

    Raises :class:`ProfiluxError` on connection/auth failure.
    """
    with make_transport(interface, host, username, password) as transport:
        return Controller(transport).snapshot()


def test_connection(host: str, username: str, password: str, interface: str = INTERFACE_HTTP) -> None:
    """Lightweight reachability/auth check for the config flow."""
    with make_transport(interface, host, username, password) as transport:
        if transport.get_int(CODE_GET_SENSOR_COUNT, signed=False) is None:
            raise ProfiluxError("connected but received no valid response")
