#!/usr/bin/env python3
"""Standalone ProfiLux reader — run it from any machine on the same LAN.

Prints device info and every sensor + power-socket the controller reports,
using the exact same protocol the Home Assistant integration uses. Handy for
confirming *which local interface* your controller answers on (and your
credentials) before deploying to Home Assistant.

    # auto-detect the interface (tries HTTP, then WebSocket):
    python scraper.py 192.168.1.221 --username admin --password YOURPASS

    # force one interface:
    python scraper.py 192.168.1.221 --interface http  --password YOURPASS
    python scraper.py 192.168.1.221 --interface websocket --password YOURPASS

The WebSocket interface additionally needs:  pip install "websocket-client>=1.6.0"
"""
from __future__ import annotations

import argparse
import json
import sys

# Allow running straight from the repo without installing anything.
sys.path.insert(0, "custom_components/profilux")

from protocol import (  # noqa: E402
    INTERFACE_HTTP,
    INTERFACE_WEBSOCKET,
    ProfiluxError,
    diagnostic,
    fetch_all,
)


def _try(host: str, user: str, password: str, interface: str, read_names: bool):
    try:
        return fetch_all(host, user, password, interface, read_names=read_names), None
    except ProfiluxError as err:
        return None, str(err)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read all data from a GHL ProfiLux controller.")
    parser.add_argument("host", help="Controller IP address, e.g. 192.168.1.221")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="")
    parser.add_argument(
        "--interface",
        choices=[INTERFACE_HTTP, INTERFACE_WEBSOCKET, "auto"],
        default="auto",
        help="Local interface to use (default: auto-detect)",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a summary")
    parser.add_argument(
        "--no-names",
        action="store_true",
        help="Skip reading sensor/socket names (diagnostic: isolates name reads)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Dump the raw type/value/state/function of every sensor and socket slot",
    )
    args = parser.parse_args()

    interface = INTERFACE_WEBSOCKET if args.interface == "auto" else args.interface

    if args.debug:
        try:
            dump = diagnostic(args.host, args.username, args.password, interface)
        except ProfiluxError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print(f"counts (reported): {dump['counts']}")
        print(f"SP_ALL_STATE  raw: {dump['all_state_raw']!r}")
        print(f"socket currents (A): {dump['socket_currents']}")
        print("\nSENSOR slots (idx: type / raw value / name):")
        for s in dump["sensors"]:
            if s["type"] is None and s["value_raw"] is None and s["name"] is None:
                continue
            print(f"  [{s['index']:>2}] type={s['type']!s:<5} raw={s['value_raw']!s:<8} name={s['name']!r}")
        print("\nSOCKET slots (idx: state / all-bit / name):")
        for k in dump["sockets"]:
            if k["state"] is None and k["name"] is None and not k["all_bit"]:
                continue
            print(f"  [{k['index']:>2}] state={k['state']!s:<5} bit={k['all_bit']!s:<5} name={k['name']!r}")
        di = dump.get("digital_inputs_raw")
        print(f"\nDIGITAL INPUTS raw: {di!r}"
              + (f"  bits={di:016b}" if isinstance(di, int) else "")
              + f"  (count {dump.get('digital_input_count')})")
        print("\nLEVEL slots (idx: state / input / sources / name):")
        for lv in dump["levels"]:
            if lv["state"] is None and lv["input"] is None and lv["name"] is None and lv.get("sources") is None:
                continue
            src = lv.get("sources")
            src_txt = "" if src is None else f" src1={src & 0xF} src2={(src >> 4) & 0xF}"
            print(f"  [{lv['index']:>2}] state={lv['state']!s:<7} input={lv['input']!s:<7} sources={src!s:<6}{src_txt} name={lv['name']!r}")
        print("\nUNKNOWN code probes (10124-10145):")
        for code, val in sorted(dump["probe_codes"].items()):
            print(f"  code {code}: {val}  (bin {val:016b})")
        return 0

    order = (
        [INTERFACE_HTTP, INTERFACE_WEBSOCKET] if args.interface == "auto" else [args.interface]
    )

    data = used = None
    for interface in order:
        data, err = _try(
            args.host, args.username, args.password, interface, read_names=not args.no_names
        )
        if data is not None and (data["sensors"] or data["sockets"]):
            used = interface
            break
        print(f"[{interface}] {'no data' if data is not None else err}", file=sys.stderr)

    if data is None or used is None:
        print("ERROR: could not read the controller on any interface.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"interface": used, **data}, indent=2, ensure_ascii=False))
        return 0

    print(f"Interface: {used}")
    device = data["device"]
    print(f"Device   : {device['model']}  (fw {device['sw_version']}, serial {device['serial']})")
    print(f"Alarm    : {data['alarm']}")
    counts = data.get("counts", {})
    print(f"Reported : {counts.get('sensors')} sensor slots, {counts.get('sockets')} socket slots")

    print("\nSensors:")
    if not data["sensors"]:
        print("  (none reported)")
    for s in data["sensors"]:
        name = s["name"] or s["label"]
        unit = f" {s['unit']}" if s["unit"] else ""
        print(f"  [{s['index']}] {name:<24} {s['value']}{unit:<8}  (type {s['type_id']})")

    print("\nPower sockets:")
    if not data["sockets"]:
        print("  (none reported)")
    for p in data["sockets"]:
        name = p["name"] or f"Socket {p['index'] + 1}"
        state = "??" if p["is_on"] is None else ("ON" if p["is_on"] else "off")
        amps = "" if p.get("current") is None else f"  {p['current']:.2f} A"
        print(f"  [{p['index']}] {name:<24} {state}{amps}")

    print("\nLevel control loops:")
    if not data.get("levels"):
        print("  (none reported)")
    for lv in data.get("levels", []):
        name = lv["name"] or f"Level {lv['index'] + 1}"
        flags = ", ".join(
            f"{k}={v}" for k, v in (("alarm", lv["alarm"]), ("fill", lv["fill"]), ("drain", lv["drain"]))
            if v is not None
        )
        print(f"  [{lv['index']}] {name:<24} {flags or '(no state)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
