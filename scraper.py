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
    args = parser.parse_args()

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
        print(f"  [{s['index']}] {name:<24} {s['value']}{unit}")

    print("\nPower sockets:")
    if not data["sockets"]:
        print("  (none reported)")
    for p in data["sockets"]:
        name = p["name"] or f"Socket {p['index'] + 1}"
        state = "??" if p["is_on"] is None else ("ON" if p["is_on"] else "off")
        print(f"  [{p['index']}] {name:<24} {state}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
