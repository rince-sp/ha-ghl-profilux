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
# Socket "Function" — the control-source register (confirmed from the backup:
# SWITCHPLUG1_FUNCTION, 779 + block(i, 16, 1)). Setting it to the "always on" /
# "always off" values is the *permanent* manual override (unlike Maintenance,
# which reverts after a timeout); the two magic values still need capturing from
# a controller. Remembering the prior value is how automatic control is restored.
CODE_SOCKET_FUNCTION = 779     # + block(i, 16, 1)
SOCKET_FUNCTION_BLOCK = 16
# "Function" values for the two permanent manual modes (confirmed on a ProfiLux 4
# — universal across sockets, since these modes carry no sensor/timer binding):
SOCKET_FUNCTION_ALWAYS_ON = 59392    # 0xE800 — "Immer an"
SOCKET_FUNCTION_ALWAYS_OFF = 61440   # 0xF000 — "Immer aus"

# Manual socket override via "Maintenance" (Wartung) — the GHL-documented way to
# force sockets on/off. A maintenance *program* p (mega-block +1000*p) carries a
# select mask (which sockets it forces), a state mask (their forced on/off), and
# a timeout in minutes after which the controller reverts to automatic. These
# are the write targets being validated for socket control in a feature branch;
# the runtime "activate program" command still has to be confirmed on-device.
CODE_MAINT_SPSELMASK_1_16 = 218
CODE_MAINT_SPSELMASK_17_32 = 219
CODE_MAINT_SPSTATEMASK_1_16 = 222
CODE_MAINT_SPSTATEMASK_17_32 = 223
CODE_MAINT_TIMEOUT = 244

# Level ("Niveau") control loops
CODE_LEVEL_STATE = 10070       # + block(i, 3, 1); packed: alarm/fill/drain/water
CODE_LEVEL_INPUT_STATE = 10074  # + block(i, 4, 1); delayed/previous/undelayed
CODE_LEVEL_NAME = 18128        # + block(i, 64, 1); text
CODE_GET_LEVEL_COUNT = 10503

# Level-loop configuration (mirrors the GHL backup's LEVELSENSORCONTROL block).
# Each loop has three sub-controls; the two assigned float sensors ("Sensor 1" /
# "Sensor 2" in the app) are the first and third. Per loop g, sub n:
#   props   = 800 + g*1000 + n*4   (bit 0 = active)
#   sources = 801 + g*1000 + n*4   (assigned sensor number = (value >> 4) + 1)
CODE_LEVEL_CTRL_PROPS = 800
CODE_LEVEL_CTRL_SOURCES = 801
LEVEL_SUB_STRIDE = 4
LEVEL_SENSOR_SUBS = (0, 2)     # sub-controls that carry the min / max float sensor

# Dosing pumps ("Dosierpumpen"). Four pumps per mega-block group.
CODE_DOSING_PROPS = 480         # + block(i, 4, 26); low 2 bits = schedule mode
CODE_DOSING_FILLLEVEL = 10311   # + block(i, 4, 6); remaining reservoir volume, mL
CODE_DOSING_CAPACITY = 501      # + block(i, 4, 26); configured reservoir size, mL
CODE_DOSING_NAME = 18184        # + i; text (descriptions block, flat)
MAX_DOSING = 16

# Dosing schedule mode ("Modus" on the Dosierplan tab); 0 = off ("Aus").
DOSING_MODES = {0: "Aus", 1: "Dauerlauf", 2: "Automatische Zeiten", 3: "Individuelle Zeiten"}

# Digital inputs (float switches feed the level loops)
CODE_DIGITAL_INPUTS_STATE = 10091  # bitmask of all digital input states
CODE_GET_DIGITAL_INPUT_COUNT = 10505

CODE_GET_SENSOR_COUNT = 10500
CODE_GET_SWITCH_COUNT = 10501
CODE_IS_ALARM = 10090

MAX_SENSORS = 16
MAX_SOCKETS = 24
MAX_LEVELS = 4
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

    def set_int(self, code: int, value: int, nbytes: int = 2, save: bool = False) -> bool:
        """Write ``value`` (``nbytes`` wide) to ``code``. Returns True on ack."""
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

    def set_int(self, code: int, value: int, nbytes: int = 2, save: bool = False) -> bool:
        url = f"{self._base}?dir=set&code={code}&data={value}"
        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("latin-1", "ignore").strip()
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise ProfiluxError("access denied (check username/password)") from err
            raise ProfiluxError(f"HTTP {err.code} writing code {code}") from err
        except (urllib.error.URLError, OSError) as err:
            raise ProfiluxError(f"cannot reach {self._base}: {err}") from err
        return "NACK" not in body and "Access Denied" not in body


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


def _encode_code(code: int, offset: int = CODE_OFFSET_SAVE) -> list[int]:
    nibbles: list[int] = []
    while True:
        nibbles.append((code & 0xF) | offset)
        code >>= 4
        if code == 0:
            break
    return nibbles


def _encode_data(value: int, nbytes: int) -> list[int]:
    """Little-endian data nibbles for a SET frame (``nbytes`` bytes wide)."""
    return [((value >> (4 * i)) & 0xF) | DATA_OFFSET for i in range(nbytes * 2)]


def _make_enquiry(code: int) -> bytes:
    header = [SOH, SLAVE_ADDR, MASTER_ADDR]
    frame = header + [_checksum(header, 3), STX] + _encode_code(code) + [ENQ, ETX]
    frame += [_checksum(frame, len(frame)), EOT]
    return bytes(frame)


def _make_set(code: int, value: int, nbytes: int, save: bool = False) -> bytes:
    """Build a SET frame writing ``value`` (``nbytes`` wide) to ``code``.

    ``save`` picks the code offset: NOSAVE (runtime only) by default, SAVE to
    persist to the controller's EEPROM. The frame carries the code nibbles then
    the data nibbles (no ENQ), which is what distinguishes a write from a read.
    """
    offset = CODE_OFFSET_SAVE if save else CODE_OFFSET_NOSAVE
    header = [SOH, SLAVE_ADDR, MASTER_ADDR]
    frame = header + [_checksum(header, 3), STX]
    frame += _encode_code(code, offset) + _encode_data(value, nbytes) + [ETX]
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

    def set_int(self, code: int, value: int, nbytes: int = 2, save: bool = False) -> bool:
        try:
            self._ws.send_binary(_make_set(code, value, nbytes, save=save))
        except Exception as err:  # noqa: BLE001
            raise ProfiluxError(f"send failed writing code {code}: {err}") from err
        # The controller echoes the written code back as an acknowledgement.
        for _ in range(6):
            try:
                reply = self._ws.recv()
            except Exception as err:  # noqa: BLE001
                if _is_timeout(err):
                    return False
                raise ProfiluxError(f"recv failed writing code {code}: {err}") from err
            if isinstance(reply, str):
                reply = reply.encode("latin-1", "ignore")
            parsed = _parse_response(reply)
            if parsed and parsed[0] == code:
                return True
        return False

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

    def socket_currents(self) -> dict[int, float]:
        """Per-socket current in amps, across all powerbar banks.

        The current array holds only 16 sockets; higher channels live in the
        next bank at the ``+1000`` mega-block offset (socket ``i`` → bank
        ``i // 16``, field ``i % 16``). So bank 1 (code 11128) carries the draw
        of channels 17+ that the app shows but the first bank never held.
        """
        currents: dict[int, float] = {}
        banks = (MAX_SOCKETS + 15) // 16
        for bank in range(banks):
            raw = self._get_int(CODE_SOCKET_CURRENT_ARRAY + bank * MEGA_BLOCK_SIZE, signed=False)
            # _decode only yields fields actually present in the array, so an
            # absent higher socket stays absent (None current) while a present
            # socket drawing nothing correctly reads 0.0 A.
            for field, amps in _decode_socket_currents(raw).items():
                currents[bank * 16 + field] = amps
        return currents

    def sockets(self, count: int) -> list[dict[str, Any]]:
        # A socket is real if its state register answers (physical sockets) or
        # it has a name (named virtual/expansion outputs). The count register is
        # not trustworthy, so scan the full addressable range.
        idxs = list(range(MAX_SOCKETS))
        state_code = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) for i in idxs}
        states = self._t.get_many_int(list(state_code.values()), signed=False)
        name_code = {i: CODE_SOCKET_NAME + _block_offset(i, 64, 1) for i in idxs}
        names = self._t.get_many_text(list(name_code.values())) if self._read_names else {}
        currents = self.socket_currents()

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

    def dosing_pumps(self) -> list[dict[str, Any]]:
        # Reservoir fill level per dosing pump. Four pumps per mega-block group:
        # props at 480 + block(i, 4, 26), fill at 10311 + block(i, 4, 6),
        # capacity at 501 + block(i, 4, 26), and the name in the flat
        # descriptions block. A pump counts as *in use* when its schedule mode
        # (the "Modus" on the Dosierplan tab = the low 2 bits of PROPS) is not
        # "Aus"; unused pumps are skipped even if a stale reservoir volume
        # lingers. If the mode read is dropped, fall back to name/content so a
        # real pump is never hidden.
        idxs = list(range(MAX_DOSING))
        props_code = {i: CODE_DOSING_PROPS + _block_offset(i, 4, 26) for i in idxs}
        fill_code = {i: CODE_DOSING_FILLLEVEL + _block_offset(i, 4, 6) for i in idxs}
        cap_code = {i: CODE_DOSING_CAPACITY + _block_offset(i, 4, 26) for i in idxs}
        name_code = {i: CODE_DOSING_NAME + i for i in idxs}
        props = self._t.get_many_int(list(props_code.values()), signed=False)
        fills = self._t.get_many_int(list(fill_code.values()), signed=False)
        caps = self._t.get_many_int(list(cap_code.values()), signed=False)
        names = self._t.get_many_text(list(name_code.values())) if self._read_names else {}

        result: list[dict[str, Any]] = []
        for i in idxs:
            name = names.get(name_code[i])
            fill = fills.get(fill_code[i])
            capacity = caps.get(cap_code[i])
            prop = props.get(props_code[i])
            mode = None if prop is None else prop & 0x3
            if mode is not None:
                if mode == 0:  # "Aus" — pump not in use
                    continue
            elif not name and not fill:  # mode unknown: fall back to config signals
                continue
            percent = (
                round(fill / capacity * 100) if capacity and fill is not None else None
            )
            result.append(
                {
                    "index": i,
                    "name": name,
                    "fill_ml": fill,
                    "capacity_ml": capacity,
                    "percent": percent,
                    "mode": DOSING_MODES.get(mode) if mode is not None else None,
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

        # Per-loop config: the two float sensors (min = sub 0, max = sub 2) and
        # whether the loop is active. Sensor number = (SOURCES >> 4) + 1; its live
        # state is the matching bit of the digital-input mask (sensor N -> bit N-1).
        src_code = {
            (i, sub): CODE_LEVEL_CTRL_SOURCES + i * MEGA_BLOCK_SIZE + sub * LEVEL_SUB_STRIDE
            for i in idxs for sub in LEVEL_SENSOR_SUBS
        }
        prop_code = {i: CODE_LEVEL_CTRL_PROPS + i * MEGA_BLOCK_SIZE for i in idxs}
        sources = self._t.get_many_int(list(src_code.values()), signed=False)
        props = self._t.get_many_int(list(prop_code.values()), signed=False)
        di_mask = self._get_int(CODE_DIGITAL_INPUTS_STATE, signed=False)

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

            prop = props.get(prop_code[i])
            active = None if prop is None else bool(prop & 0x1)
            sensors: list[dict[str, Any]] = []
            seen: set[int] = set()
            for role, sub in zip(("min", "max"), LEVEL_SENSOR_SUBS):
                src = sources.get(src_code[(i, sub)])
                if src is None:
                    continue
                number = (src >> 4) + 1  # 1-based float-sensor / digital-input number
                if number in seen:
                    continue  # single-sensor loop: both sub-controls point at one sensor
                seen.add(number)
                triggered = None if di_mask is None else bool((di_mask >> (number - 1)) & 1)
                sensors.append({"role": role, "number": number, "triggered": triggered})

            result.append(
                {
                    "index": i,
                    "name": names.get(name_code[i]),
                    "alarm": alarm,
                    "fill": fill,
                    "drain": drain,
                    "active": active,
                    "sensors": sensors,
                }
            )
        return result

    def write_code(self, code: int, value: int, nbytes: int = 2, save: bool = False) -> bool:
        """Write a raw code (for socket control / experimentation). Returns ack."""
        for _ in range(self._retries):
            if self._t.set_int(code, value, nbytes=nbytes, save=save):
                return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "device": self.device_info(),
            "alarm": self.alarm(),
            "sensors": self.sensors(0),
            "sockets": self.sockets(0),
            "levels": self.levels(),
            "dosing_pumps": self.dosing_pumps(),
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


def read_code(
    host: str,
    username: str,
    password: str,
    code: int,
    interface: str = INTERFACE_WEBSOCKET,
    signed: bool = False,
) -> int | None:
    """Read a single code — handy for capturing e.g. a socket's Function value."""
    with make_transport(interface, host, username, password) as transport:
        return Controller(transport)._get_int(code, signed=signed)


def write_and_verify(
    host: str,
    username: str,
    password: str,
    code: int,
    value: int,
    interface: str = INTERFACE_WEBSOCKET,
    nbytes: int = 2,
    save: bool = False,
    verify_code: int | None = None,
) -> dict[str, Any]:
    """Write one code and read a verify code before/after — for confirming the
    socket-control mechanism on a controller safely and reproducibly.
    """
    with make_transport(interface, host, username, password) as transport:
        ctrl = Controller(transport)
        vcode = code if verify_code is None else verify_code
        before = ctrl._get_int(vcode, signed=False)
        acked = ctrl.write_code(code, value, nbytes=nbytes, save=save)
        after = ctrl._get_int(vcode, signed=False)
        return {"acked": acked, "verify_code": vcode, "before": before, "after": after}


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
        # Scan the real socket range (indices >= 24 wrap into other code blocks)
        # and add level + "unknown code" probes so channels beyond the 16-bit
        # state register can be located.
        wide = range(MAX_SOCKETS)
        k_state_c = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) for i in wide}
        k_func_c = {i: CODE_SOCKET_FUNCTION + _block_offset(i, SOCKET_FUNCTION_BLOCK, 1) for i in wide}
        k_name_c = {i: CODE_SOCKET_NAME + _block_offset(i, 64, 1) for i in wide}
        l_state_c = {i: CODE_LEVEL_STATE + _block_offset(i, 3, 1) for i in range(4)}
        l_input_c = {i: CODE_LEVEL_INPUT_STATE + _block_offset(i, 4, 1) for i in range(4)}
        # Confirmed level-loop source scheme: per loop i, sub n -> 801 + i*1000 + n*4.
        l_source_c = {i: CODE_LEVEL_CTRL_SOURCES + i * MEGA_BLOCK_SIZE for i in range(4)}
        l_name_c = {i: CODE_LEVEL_NAME + _block_offset(i, 64, 1) for i in range(4)}
        probe_codes = list(range(10124, 10146))  # around SP_ALL_STATE/CURRENT

        # Targeted current probe. The powerbar current array (10128) only carries
        # the first 16 sockets, so channels 16+ (e.g. a light channel or a virtual
        # channel) draw current the app shows but that array doesn't hold. A broad
        # sweep overloads the controller (it starts dropping frames), so probe
        # only the likely spots: the neighbouring array (10127) and the +1000 /
        # +2000 mega-block banks, decoded as 16-bit little-endian mA fields.
        sweep_codes = [10127, 10128, 11127, 11128, 12127, 12128, 10136, 10144, 10145]
        # Also probe socket state for the higher channels via the +1000 mega-block,
        # in case a second socket bank lives there.
        hi_state_c = {i: CODE_SOCKET_STATE + _block_offset(i, 24, 1) + MEGA_BLOCK_SIZE for i in range(16, 24)}

        # Level-loop config. Each loop has three sub-controls (props/sources/
        # maxduration), stride 4; the two assigned float sensors live in subs
        # 0 and 2. Dump all three subs' sources + props per loop.
        NLV = 4
        l_srcfull_c = {
            (i, w): CODE_LEVEL_CTRL_SOURCES + i * MEGA_BLOCK_SIZE + w * LEVEL_SUB_STRIDE
            for i in range(NLV)
            for w in range(3)
        }
        l_props_c = {
            (i, w): CODE_LEVEL_CTRL_PROPS + i * MEGA_BLOCK_SIZE + w * LEVEL_SUB_STRIDE
            for i in range(NLV)
            for w in range(3)
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
        # Read the important small probes early, before the connection has done a
        # lot of work — the controller gets lossy under sustained polling.
        l_srcfull = transport.get_many_int(list(l_srcfull_c.values()), signed=False)
        l_propsfull = transport.get_many_int(list(l_props_c.values()), signed=False)
        sweep_raw = transport.get_many_int(sweep_codes, signed=False)
        hi_states = transport.get_many_int(list(hi_state_c.values()), signed=False)
        digital_inputs = ctrl._get_int(CODE_DIGITAL_INPUTS_STATE, signed=False)
        digital_input_count = ctrl._get_int(CODE_GET_DIGITAL_INPUT_COUNT, signed=False)
        probes = transport.get_many_int(probe_codes, signed=False)
        all_state = ctrl._get_int(CODE_SOCKET_ALL_STATE, signed=False)
        socket_currents = ctrl.socket_currents()
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
    # Each probed current code, decoded into its 16-bit little-endian fields, so
    # a field near 0.8-0.9 A on channel 16/17 (or in a mega-block bank) reveals
    # the register that carries the higher channels' current.
    current_sweep = {code: _decode_16bit_fields(val) for code, val in sweep_raw.items() if val}
    hi_bank = {i: hi_states.get(hi_state_c[i]) for i in range(16, 24) if hi_states.get(hi_state_c[i]) is not None}

    # Per level loop: each sub-control's props/sources, plus the decoded sensor
    # number (= (sources >> 4) + 1) so the app's "Sensor N" can be lined up.
    level_sources_full = {
        i: {
            "props": [l_propsfull.get(l_props_c[(i, w)]) for w in range(3)],
            "sources": [l_srcfull.get(l_srcfull_c[(i, w)]) for w in range(3)],
            "sensor_nrs": [
                None if (s := l_srcfull.get(l_srcfull_c[(i, w)])) is None else (s >> 4) + 1
                for w in range(3)
            ],
        }
        for i in range(NLV)
    }

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
        "sensors": sensors,
        "sockets": sockets,
        "levels": levels,
    }
