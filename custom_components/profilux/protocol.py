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
import socket
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
CODE_SOCKET_ALL_STATE = 10126  # single read: bitmask of the first 16 socket states
CODE_SOCKET_CURRENT_ARRAY = 10128  # digital powerbar: per-socket current, 16-bit LE mA fields
CODE_SOCKET_NAME = 18064       # + block(i, 64, 1); text
CODE_SOCKET_FUNCTION = 756     # + block(i, 24, 1); config bitfield (0 = unused)

# Level ("Niveau") control loops
CODE_LEVEL_STATE = 10070       # + block(i, 3, 1); packed: alarm/fill/drain/water
CODE_LEVEL_INPUT_STATE = 10074  # + block(i, 4, 1); delayed/previous/undelayed
CODE_LEVEL_SOURCES = 928       # + block(i, 3, 3); source1 = &0xf, source2 = (>>4)&0xf
CODE_LEVEL_NAME = 18128        # + block(i, 64, 1); text
CODE_GET_LEVEL_COUNT = 10503

# Digital inputs (float switches feed the level loops)
CODE_DIGITAL_INPUTS_STATE = 10091  # bitmask of all digital input states
CODE_GET_DIGITAL_INPUT_COUNT = 10505

CODE_GET_SENSOR_COUNT = 10500
CODE_GET_SWITCH_COUNT = 10501
CODE_IS_ALARM = 10090

MAX_SENSORS = 16
MAX_SOCKETS = 24
MAX_LEVELS = 3
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

# Decimal places by sensor type. ProfiLux values are fixed-point integers whose
# scale is defined by the *type*, not a per-sensor display register (that
# register proved unreliable across firmwares). raw / 10**decimals -> value.
DECIMALS_BY_TYPE: dict[int, int] = {
    1: 1,   # Temperature   251 -> 25.1 °C
    2: 2,   # pH            812 -> 8.12
    3: 0,   # Redox         109 -> 109 mV
    4: 1,   # Conductivity (µS/cm)
    5: 1,   # Conductivity  376 -> 37.6 mS/cm
    6: 1,
    7: 0,   # Humidity
    8: 1,   # Air Temperature
    9: 1,   # Oxygen
    10: 2,  # Voltage
}
DEFAULT_DECIMALS = 1

# Classify a probe by its user-given name when the type register is unreliable
# (as on ProfiLux 4). Each entry: (keywords, (label, unit, device_class, decimals)).
# Order matters — more specific keywords first.
NAME_KEYWORDS: list[tuple[tuple[str, ...], tuple[str, str | None, str | None, int]]] = [
    (("temperatur", "temp", "°c"), ("Temperature", "°C", "temperature", 1)),
    (("redox", "orp"), ("Redox", "mV", None, 0)),
    (("leitwert", "leit", "conduct", "cond", "salin", "µs", "ms/cm"),
     ("Conductivity", "mS/cm", None, 1)),
    (("feucht", "humid"), ("Humidity", "%", "humidity", 0)),
    (("sauerstoff", "oxygen", " o2"), ("Oxygen", "mg/L", None, 1)),
    (("ph",), ("pH", "pH", None, 2)),
]


def classify_sensor(type_id: int | None, name: str | None) -> tuple[str, str | None, str | None, int]:
    """Return (label, unit, device_class, decimals) for a probe.

    Prefers a valid GHL type id; otherwise infers from the probe's name (the
    type register is unreliable on some firmwares); otherwise a plain fallback.
    """
    if type_id in SENSOR_TYPES:
        label, unit, device_class = SENSOR_TYPES[type_id]
        return label, unit, device_class, DECIMALS_BY_TYPE.get(type_id, DEFAULT_DECIMALS)
    low = (name or "").lower()
    for keywords, meta in NAME_KEYWORDS:
        if any(k in low for k in keywords):
            return meta
    return (name or "Sensor", None, None, DEFAULT_DECIMALS)


# ProfiLux product ids -> model name (unknown ids fall back to "ProfiLux (id N)").
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
    23: "ProfiLux 4",
}


class ProfiluxError(Exception):
    """Raised when the controller cannot be reached or answers nonsense."""


def _block_offset(index: int, block_count: int, block_size: int) -> int:
    """GHL block addressing (mirrors ``getOffset`` in profilux-go)."""
    return ((index % block_count) * block_size) + ((index // block_count) * MEGA_BLOCK_SIZE)


def _sensor_offset(index: int) -> int:
    return _block_offset(index, 8, 24)


def _decode_16bit_fields(raw: int | None) -> list[int]:
    """Split a packed integer into its 16-bit little-endian fields (low first).

    High-order zero fields are dropped by the integer, so trailing empty fields
    simply won't appear.
    """
    if not raw:
        return []
    count = (raw.bit_length() + 15) // 16
    return [(raw >> (16 * i)) & 0xFFFF for i in range(count)]


def _decode_socket_currents(raw: int | None) -> dict[int, float]:
    """Decode the powerbar current register into {socket_index: amps}.

    The value packs one 16-bit little-endian field per socket, in milliamps.
    High-order zero fields are dropped by the integer, so trailing sockets that
    draw no current simply won't appear.
    """
    fields = _decode_16bit_fields(raw)
    return {i: round(ma / 1000.0, 2) for i, ma in enumerate(fields)}


# =========================================================================
# Transports
# =========================================================================


class Transport:
    """Reads controller codes as ints or text.

    ``get_many_*`` read a batch of codes and return only those that answered
    (missing codes are simply absent). The default implementation loops the
    single-code reads; transports that can pipeline override it.
    """

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def get_int(self, code: int, signed: bool = True) -> int | None:
        raise NotImplementedError

    def get_text(self, code: int) -> str | None:
        raise NotImplementedError

    def get_many_int(self, codes: list[int], signed: bool = True) -> dict[int, int]:
        out: dict[int, int] = {}
        for code in codes:
            value = self.get_int(code, signed=signed)
            if value is not None:
                out[code] = value
        return out

    def get_many_text(self, codes: list[int]) -> dict[int, str]:
        out: dict[int, str] = {}
        for code in codes:
            text = self.get_text(code)
            if text is not None:
                out[code] = text
        return out


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
    # Header block-check (BCA) guards the addressing bytes.
    if _checksum(b, 3) != b[3]:
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
    # Frame block-check (BCC): the byte right after ETX must equal the checksum
    # over SOH..ETX. This rejects the corrupted / merged frames the controller
    # occasionally emits under rapid polling (which produced nonsense sensor
    # types and slot counts); a rejected frame is simply retried.
    etx = next((j for j in range(d, len(b)) if b[j] == ETX), None)
    if etx is None or etx + 1 >= len(b) or _checksum(b, etx + 1) != b[etx + 1]:
        return None
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
        # Latin-1 so German umlauts (ü/ö/ä/ß) in probe/socket names survive.
        if byte >= 32 and byte != 127:
            chars.append(chr(byte))
    return "".join(chars).strip()


def _is_timeout(err: Exception) -> bool:
    """True if the exception is a socket/WebSocket read timeout."""
    if isinstance(err, socket.timeout):
        return True
    return "timed out" in str(err).lower() or type(err).__name__ == "WebSocketTimeoutException"


class WebSocketTransport(Transport):
    """ProfiLux mini SWMBus-over-WebSocket interface (``ws://<host>/ws``)."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        timeout: int = 10,
        read_timeout: float = 3.0,
    ) -> None:
        self._url = f"ws://{host}/ws"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}
        self._timeout = timeout
        # Per-read timeout: an empty/non-existent slot simply gets no reply, so
        # keep this short — a miss should cost a beat, not the whole poll.
        self._read_timeout = read_timeout
        self._ws: Any = None

    def __enter__(self) -> "WebSocketTransport":
        try:
            import websocket  # noqa: PLC0415 - optional dependency, only for this transport

            self._ws = websocket.create_connection(
                self._url, header=self._auth, timeout=self._timeout
            )
            self._ws.settimeout(self._read_timeout)
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
        for _ in range(6):
            try:
                reply = self._ws.recv()
            except Exception as err:  # noqa: BLE001
                # A timeout means the controller had nothing to say for this
                # code (e.g. an empty sensor/socket slot). Treat it as "no data"
                # rather than a fatal error so one gap can't abort the poll.
                if _is_timeout(err):
                    return None
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

    # -- Batched reads ----------------------------------------------------
    # The controller drops the odd reply under back-to-back requests and never
    # answers for empty slots. Firing one enquiry at a time (with a per-code
    # timeout) is therefore both slow and lossy. Instead, send a whole batch,
    # drain whatever comes back mapping by code, then retry only the codes that
    # stayed silent for a couple of rounds.

    def _read_many_raw(
        self, codes: list[int], rounds: int = 3, drain_timeout: float = 0.7
    ) -> dict[int, list[int]]:
        results: dict[int, list[int]] = {}
        unique = list(dict.fromkeys(codes))
        for _ in range(rounds):
            pending = [c for c in unique if c not in results]
            if not pending:
                break
            for code in pending:
                try:
                    self._ws.send_binary(_make_enquiry(code))
                except Exception as err:  # noqa: BLE001
                    raise ProfiluxError(f"send failed for code {code}: {err}") from err
            self._drain(set(pending), results, drain_timeout)
        return results

    def _drain(
        self, expected: set[int], results: dict[int, list[int]], drain_timeout: float
    ) -> None:
        """Read replies until the controller goes quiet for ``drain_timeout``."""
        self._ws.settimeout(drain_timeout)
        try:
            while True:
                try:
                    reply = self._ws.recv()
                except Exception as err:  # noqa: BLE001
                    if _is_timeout(err):
                        return  # no more replies this round
                    raise ProfiluxError(f"recv failed: {err}") from err
                if isinstance(reply, str):
                    reply = reply.encode("latin-1", "ignore")
                parsed = _parse_response(reply)
                if parsed and parsed[0] in expected and parsed[0] not in results:
                    results[parsed[0]] = parsed[1]
        finally:
            self._ws.settimeout(self._read_timeout)

    def get_many_int(self, codes: list[int], signed: bool = True) -> dict[int, int]:
        raw = self._read_many_raw(codes)
        return {code: _nibbles_to_int(nib, signed=signed) for code, nib in raw.items()}

    def get_many_text(self, codes: list[int]) -> dict[int, str]:
        raw = self._read_many_raw(codes)
        return {code: text for code, nib in raw.items() if (text := _nibbles_to_text(nib))}


def make_transport(interface: str, host: str, username: str, password: str) -> Transport:
    if interface == INTERFACE_WEBSOCKET:
        return WebSocketTransport(host, username, password)
    return HttpTransport(host, username, password)


# =========================================================================
# High-level controller (transport-agnostic)
# =========================================================================


class Controller:
    """Reads device info, all sensors, and all sockets via a transport.

    The controller occasionally drops a reply under back-to-back requests, so
    every read is retried a few times, and slot scans never abort early on a
    miss — they cover the full reported count and simply skip anything that
    stays silent.
    """

    def __init__(self, transport: Transport, retries: int = 3, read_names: bool = True) -> None:
        self._t = transport
        self._retries = max(1, retries)
        self._read_names = read_names

    def _get_int(self, code: int, signed: bool = True) -> int | None:
        for _ in range(self._retries):
            value = self._t.get_int(code, signed=signed)
            if value is not None:
                return value
        return None

    def _get_text(self, code: int) -> str | None:
        if not self._read_names:
            return None
        for _ in range(self._retries):
            text = self._t.get_text(code)
            if text is not None:
                return text
        return None

    def _count(self, code: int, cap: int) -> int:
        count = self._get_int(code, signed=False)
        if count is None or count <= 0:
            return 0
        return min(count, cap)

    def device_info(self) -> dict[str, Any]:
        version_raw = self._get_int(CODE_SOFTWAREVERSION, signed=False)
        product_id = self._get_int(CODE_PRODUCTID, signed=False)
        serial = self._get_int(CODE_SERIALNUMBER, signed=False)
        sw_version = None if version_raw is None else f"{version_raw / 100:.2f}"
        if product_id is None:
            model = "ProfiLux"
        else:
            model = PRODUCT_IDS.get(product_id, f"ProfiLux (id {product_id})")
        return {"model": model, "sw_version": sw_version, "serial": serial}

    def sensors(self, count: int) -> list[dict[str, Any]]:
        # The count register is unreliable on some firmwares, so scan a fixed
        # range and treat a slot as populated when its *value* register answers.
        idxs = list(range(MAX_SENSORS))
        value_code = {i: CODE_SENSOR_VALUE + _block_offset(i, 8, 8) for i in idxs}
        values = self._t.get_many_int(list(value_code.values()))
        present = [i for i in idxs if value_code[i] in values]

        type_code = {i: CODE_SENSOR_TYPE + _sensor_offset(i) for i in present}
        types = self._t.get_many_int(list(type_code.values()), signed=False)
        name_code = {i: CODE_SENSOR_NAME + _block_offset(i, 32, 1) for i in present}
        names = self._t.get_many_text(list(name_code.values())) if self._read_names else {}

        result: list[dict[str, Any]] = []
        for i in present:
            name = names.get(name_code[i])
            type_id = types.get(type_code[i])
            label, unit, device_class, decimals = classify_sensor(type_id, name)
            value = round(values[value_code[i]] / (10 ** decimals), decimals)
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

    def sockets(self, count: int) -> list[dict[str, Any]]:
        # A socket is real if its state register answers (physical sockets) or
        # it has a name (named virtual/expansion outputs). The count register is
        # not trustworthy, so scan the full addressable range.
        idxs = list(range(MAX_SOCKETS))
        state_code = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) for i in idxs}
        states = self._t.get_many_int(list(state_code.values()), signed=False)
        name_code = {i: CODE_SOCKET_NAME + _block_offset(i, 64, 1) for i in idxs}
        names = self._t.get_many_text(list(name_code.values())) if self._read_names else {}
        currents = _decode_socket_currents(self._get_int(CODE_SOCKET_CURRENT_ARRAY, signed=False))

        # Physical sockets answer the state register; digital-powerbar channels
        # (17/18…) don't, but their draw shows up in the current array. So a
        # socket is real if it has a state, a name, or a non-zero current.
        present = [
            i for i in idxs
            if state_code[i] in states or names.get(name_code[i]) or currents.get(i)
        ]
        result: list[dict[str, Any]] = []
        for i in present:
            state = states.get(state_code[i])
            amps = currents.get(i)
            if state is not None:
                is_on: bool | None = state != 0
            elif amps is not None:
                is_on = amps > 0  # powerbar channel: on when drawing current
            else:
                is_on = None
            result.append(
                {
                    "index": i,
                    "name": names.get(name_code[i]),
                    "is_on": is_on,
                    "current": amps,
                }
            )
        return result

    def alarm(self) -> bool | None:
        raw = self._get_int(CODE_IS_ALARM, signed=False)
        return None if raw is None else raw != 0

    def levels(self) -> list[dict[str, Any]]:
        idxs = list(range(MAX_LEVELS))
        state_code = {i: CODE_LEVEL_STATE + _block_offset(i, 3, 1) for i in idxs}
        states = self._t.get_many_int(list(state_code.values()), signed=False)
        name_code = {i: CODE_LEVEL_NAME + _block_offset(i, 64, 1) for i in idxs}
        names = self._t.get_many_text(list(name_code.values())) if self._read_names else {}

        present = [i for i in idxs if state_code[i] in states or names.get(name_code[i])]
        result: list[dict[str, Any]] = []
        for i in present:
            raw = states.get(state_code[i])
            alarm = fill = drain = None
            if raw is not None:
                # bit layout: A F D W W W W R  (alarm/fill/drain/water-mode/reserved)
                v = raw >> 1
                v >>= 4  # skip water-mode nibble
                drain = bool(v & 0x1)
                v >>= 1
                fill = bool(v & 0x1)
                v >>= 1
                alarm = bool(v & 0x1)
            result.append(
                {
                    "index": i,
                    "name": names.get(name_code[i]),
                    "alarm": alarm,
                    "fill": fill,
                    "drain": drain,
                }
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "device": self.device_info(),
            "alarm": self.alarm(),
            "sensors": self.sensors(0),
            "sockets": self.sockets(0),
            "levels": self.levels(),
        }


def fetch_all(
    host: str,
    username: str,
    password: str,
    interface: str = INTERFACE_HTTP,
    read_names: bool = True,
) -> dict[str, Any]:
    """Read device info, every populated sensor, and every socket.

    Raises :class:`ProfiluxError` on connection/auth failure.
    """
    with make_transport(interface, host, username, password) as transport:
        return Controller(transport, read_names=read_names).snapshot()


def test_connection(host: str, username: str, password: str, interface: str = INTERFACE_HTTP) -> None:
    """Lightweight reachability/auth check for the config flow."""
    with make_transport(interface, host, username, password) as transport:
        if Controller(transport)._get_int(CODE_GET_SENSOR_COUNT, signed=False) is None:
            raise ProfiluxError("connected but received no valid response")


def diagnostic(
    host: str, username: str, password: str, interface: str = INTERFACE_HTTP
) -> dict[str, Any]:
    """Raw dump of every sensor/socket slot — for reverse-engineering a device.

    Reads the type/value/name of every sensor slot and the per-socket state,
    the bulk ``SP_ALL_STATE`` bitmask, socket function and name, so the real
    layout (which type id sits where, how to read all sockets) is visible.
    """
    with make_transport(interface, host, username, password) as transport:
        ctrl = Controller(transport)
        s_type_c = {i: CODE_SENSOR_TYPE + _sensor_offset(i) for i in range(MAX_SENSORS)}
        s_val_c = {i: CODE_SENSOR_VALUE + _block_offset(i, 8, 8) for i in range(MAX_SENSORS)}
        s_name_c = {i: CODE_SENSOR_NAME + _block_offset(i, 32, 1) for i in range(MAX_SENSORS)}
        k_state_c = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) for i in range(MAX_SOCKETS)}
        k_func_c = {i: CODE_SOCKET_FUNCTION + _block_offset(i, 24, 1) for i in range(MAX_SOCKETS)}
        k_name_c = {i: CODE_SOCKET_NAME + _block_offset(i, 64, 1) for i in range(MAX_SOCKETS)}

        # Scan the real socket range (indices >= 24 wrap into other code blocks)
        # and add level + "unknown code" probes so channels beyond the 16-bit
        # state register can be located.
        wide = range(MAX_SOCKETS)
        k_state_c = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) for i in wide}
        k_name_c = {i: CODE_SOCKET_NAME + _block_offset(i, 64, 1) for i in wide}
        l_state_c = {i: CODE_LEVEL_STATE + _block_offset(i, 3, 1) for i in range(4)}
        l_input_c = {i: CODE_LEVEL_INPUT_STATE + _block_offset(i, 4, 1) for i in range(4)}
        l_source_c = {i: CODE_LEVEL_SOURCES + _block_offset(i, 3, 3) for i in range(4)}
        l_name_c = {i: CODE_LEVEL_NAME + _block_offset(i, 64, 1) for i in range(4)}
        probe_codes = list(range(10124, 10146))  # around SP_ALL_STATE/CURRENT

        # Wide current sweep: the powerbar current array (10128) only carries the
        # first 16 sockets, so channels 16+ (e.g. an Orphek light, a virtual
        # channel) draw current the app shows but that array doesn't hold. Scan a
        # broad code range plus the +1000 mega-block banks and decode each value
        # as 16-bit little-endian mA fields, so a single run pinpoints whichever
        # register carries the higher channels' current.
        sweep_codes = (
            list(range(10124, 10160))
            + list(range(11124, 11160))
            + list(range(12124, 12160))
        )
        # Also probe socket state/name for the higher channels via the +1000
        # mega-block, in case a second socket bank lives there.
        hi_state_c = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) + MEGA_BLOCK_SIZE for i in range(16, 24)}

        # Level-loop config. Each loop's source block is *three* words (not one),
        # so read all three per loop — the two assigned float sensors ("Sensor 1"
        # / "Sensor 2" in the app) live in this block, and whether a loop uses one
        # or two of them depends on its operating mode ("Betriebsmodus"). Also
        # sweep a nearby config band per loop to locate the operating-mode
        # register (an inactive loop should read 0 where active ones don't).
        NLV = 4
        l_srcfull_c = {
            (i, w): CODE_LEVEL_SOURCES + _block_offset(i, 3, 3) + w
            for i in range(NLV)
            for w in range(3)
        }
        cfg_band = range(900, 961)
        l_cfg_c = {
            (base, i): base + _block_offset(i, 3, 1)
            for base in cfg_band
            for i in range(NLV)
        }

        s_types = transport.get_many_int(list(s_type_c.values()), signed=False)
        s_vals = transport.get_many_int(list(s_val_c.values()))
        s_names = transport.get_many_text(list(s_name_c.values()))
        k_states = transport.get_many_int(list(k_state_c.values()), signed=False)
        k_names = transport.get_many_text(list(k_name_c.values()))
        l_states = transport.get_many_int(list(l_state_c.values()), signed=False)
        l_inputs = transport.get_many_int(list(l_input_c.values()), signed=False)
        l_sources = transport.get_many_int(list(l_source_c.values()), signed=False)
        l_names = transport.get_many_text(list(l_name_c.values()))
        digital_inputs = ctrl._get_int(CODE_DIGITAL_INPUTS_STATE, signed=False)
        digital_input_count = ctrl._get_int(CODE_GET_DIGITAL_INPUT_COUNT, signed=False)
        probes = transport.get_many_int(probe_codes, signed=False)
        sweep_raw = transport.get_many_int(sweep_codes, signed=False)
        hi_states = transport.get_many_int(list(hi_state_c.values()), signed=False)
        l_srcfull = transport.get_many_int(list(l_srcfull_c.values()), signed=False)
        l_cfg = transport.get_many_int(list(l_cfg_c.values()), signed=False)
        all_state = ctrl._get_int(CODE_SOCKET_ALL_STATE, signed=False)
        socket_currents = _decode_socket_currents(
            ctrl._get_int(CODE_SOCKET_CURRENT_ARRAY, signed=False)
        )
        counts = {
            "sensors": ctrl._get_int(CODE_GET_SENSOR_COUNT, signed=False),
            "sockets": ctrl._get_int(CODE_GET_SWITCH_COUNT, signed=False),
            "levels": ctrl._get_int(CODE_GET_LEVEL_COUNT, signed=False),
        }

    sensors = [
        {
            "index": i,
            "type": s_types.get(s_type_c[i]),
            "value_raw": s_vals.get(s_val_c[i]),
            "name": s_names.get(s_name_c[i]),
        }
        for i in range(MAX_SENSORS)
    ]
    sockets = [
        {
            "index": i,
            "state": k_states.get(k_state_c[i]),
            "all_bit": None if all_state is None else (all_state >> i) & 1,
            "current": socket_currents.get(i),
            "name": k_names.get(k_name_c[i]),
        }
        for i in wide
    ]
    levels = [
        {
            "index": i,
            "state": l_states.get(l_state_c[i]),
            "input": l_inputs.get(l_input_c[i]),
            "sources": l_sources.get(l_source_c[i]),
            "name": l_names.get(l_name_c[i]),
        }
        for i in range(4)
    ]
    # Any swept code whose decoded 16-bit fields look like plausible currents
    # (at least one field in the 20 mA – 5 A range) is a candidate register for
    # the higher channels' current.
    current_sweep = {}
    for code, val in sweep_raw.items():
        fields = _decode_16bit_fields(val)
        if any(20 <= f <= 5000 for f in fields):
            current_sweep[code] = fields
    hi_bank = {i: hi_states.get(hi_state_c[i]) for i in range(16, 24) if hi_states.get(hi_state_c[i]) is not None}

    # Full 3-word source block per level loop.
    level_sources_full = {
        i: [l_srcfull.get(l_srcfull_c[(i, w)]) for w in range(3)] for i in range(NLV)
    }
    # Config-band candidates: bases whose per-loop values look like small
    # ids/modes (0-255) and are not identical across every loop — the operating
    # mode should read 0 on an inactive loop and non-zero on active ones.
    level_cfg_band = {}
    for base in cfg_band:
        vals = [l_cfg.get(l_cfg_c[(base, i)]) for i in range(NLV)]
        present = [v for v in vals if v is not None]
        if present and all(v <= 255 for v in present) and len(set(present)) > 1:
            level_cfg_band[base] = vals

    return {
        "counts": counts,
        "all_state_raw": all_state,
        "socket_currents": socket_currents,
        "digital_inputs_raw": digital_inputs,
        "digital_input_count": digital_input_count,
        "probe_codes": {c: v for c, v in probes.items()},
        "current_sweep": current_sweep,
        "hi_bank_state": hi_bank,
        "level_sources_full": level_sources_full,
        "level_cfg_band": level_cfg_band,
        "sensors": sensors,
        "sockets": sockets,
        "levels": levels,
    }
