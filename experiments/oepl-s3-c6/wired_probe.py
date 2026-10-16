#!/usr/bin/env python3
"""Non-invasive, one-shot S3/C6 wiring *preflight*, not a UART handshake.

Never opens a serial TTY: opening USB CDC/JTAG devices can affect DTR/RTS
and reset some boards. Never flashes, writes GPIO, or changes tty settings.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from probe import DEFAULT_ROOT, report


def tcp_probe(ip: str | None, port: int, timeout: float = 2.0) -> dict:
    if not ip:
        return {"status": "NOT_REQUESTED"}
    try:
        socket.inet_aton(ip)
        if ip.count(".") != 3 or not 1 <= port <= 65535:
            raise ValueError("not an IPv4 TCP endpoint")
    except (OSError, ValueError):
        return {"status": "INVALID_TARGET"}
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return {"status": "TCP_REACHABLE"}
    except OSError:
        return {"status": "TCP_UNREACHABLE"}


def tty_inventory(root: Path = Path("/dev/serial/by-id")) -> list[dict]:
    if not root.is_dir():
        return []
    devices = []
    for link in sorted(root.iterdir(), key=lambda item: item.name)[:32]:
        if not link.is_symlink():
            continue
        # A listing does not prove the tty belongs to a particular board.
        # Never open the target or alter TTY control signals.
        resolved = link.resolve()
        devices.append({
            "by_id": link.name,
            "tty": resolved.name,
            "reachable": resolved.exists(),
        })
    return devices


def snapshot(source: Path, ip: str | None, port: int = 80,
             serial_dir: Path = Path("/dev/serial/by-id")) -> dict:
    inventory = report(source)
    devices = tty_inventory(serial_dir)
    net = tcp_probe(ip, port)
    return {
        "kind": "OEPL_READ_ONLY_WIRED_PREFLIGHT",
        "source_sha": inventory.get("source_sha"),
        "configuration": inventory.get("status"),
        "s3_flags": inventory.get("s3", {}).get("flags", {}),
        "c6_flags": inventory.get("c6", {}).get("flags", {}),
        "usb_serial_ports": devices,
        "tcp": net,
        "uart_handshake": "NOT_TESTED",
        "c6_radio": "NOT_TESTED",
        "conclusion": (
            "Only software/USB enumeration/TCP reachability checked; "
            "the reported wiring and C6 UART are not yet electrically proven."
        ),
        "safety": "No TTY opened; no flash, reset, GPIO drive or tag command.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="No-touch OEPL S3/C6 wiring preflight")
    parser.add_argument("--source", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--ap-ip", default=None, help="Previously observed AP IPv4 address")
    parser.add_argument("--ap-port", type=int, default=80)
    args = parser.parse_args()
    state = snapshot(args.source.expanduser().resolve(), args.ap_ip, args.ap_port)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if state["configuration"] == "SOURCE_MISSING":
        return 2
    if state["configuration"] == "CONFIG_MISMATCH":
        return 1
    # A successful *preflight* is not a passed UART handshake.
    return 0


if __name__ == "__main__":
    sys.exit(main())
