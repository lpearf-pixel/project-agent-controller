#!/usr/bin/env python3
"""Non-invasive, one-shot S3/C6 wiring *preflight*, not a UART handshake.

Never opens a serial TTY: opening USB CDC/JTAG devices can affect DTR/RTS
and reset some boards. Never flashes, writes GPIO, or changes tty settings.
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
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


def http_sysinfo(ip: str | None, port: int, timeout: float = 3.0) -> dict:
    """GET only /sysinfo on local AP; sanitize to a tiny known field allowlist.

    Neither AP network availability nor cached apInfo values prove a live UART
    handshake. No POST, reset, firmware change, tag command, serial open.
    """
    if not ip:
        return {"status": "NOT_REQUESTED"}
    try:
        address = ipaddress.IPv4Address(ip)
        if not address.is_private or address.is_unspecified:
            return {"status": "INVALID_TARGET"}
        if not 1 <= port <= 65535:
            return {"status": "INVALID_TARGET"}
    except ipaddress.AddressValueError:
        return {"status": "INVALID_TARGET"}
    conn = http.client.HTTPConnection(str(address), port, timeout=timeout)
    phase = "REQUEST"
    try:
        conn.request(
            "GET", "/sysinfo",
            headers={"Accept": "application/json", "Connection": "close"},
        )
        phase = "HEADERS"
        response = conn.getresponse()
        if response.status != 200:
            return {"status": "HTTP_NOT_AVAILABLE", "code": response.status}
        phase = "BODY"
        raw = response.read(16_385)
        if len(raw) > 16_384:
            return {"status": "RESPONSE_TOO_LARGE", "code": 200}
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            # Never expose the body, headers, Wi-Fi SSID, cookies, or alias.
            return {"status": "HTTP_INVALID_JSON", "code": 200, "bytes": len(raw)}
        if not isinstance(data, dict):
            return {"status": "HTTP_INVALID_RESPONSE", "code": 200}
        allowed = (
            "env", "ap_version", "ap_type", "ap_typename", "channel", "hasC6",
            "resetreason", "crashed", "buildversion",
        )
        details = {
            key: value for key in allowed
            if (value := data.get(key)) is not None
            and isinstance(value, (str, int, bool))
            and len(str(value)) <= 100
        }
        pins = data.get("flashpin_defaults")
        if isinstance(pins, dict):
            details["flashpin_defaults"] = {
                key: pins[key] for key in ("txd", "rxd", "reset")
                if key in pins and type(pins[key]) is int
            }
        return {
            "status": "HTTP_INFO_OBSERVED",
            "fields": details,
            "interpretation": (
                "AP-reported state only; not proof of live UART or flashed C6 identity"
            ),
        }
    except TimeoutError:
        return {"status": "HTTP_TIMEOUT", "phase": phase}
    except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
        return {"status": "HTTP_CONNECTION_CLOSED", "phase": phase}
    except http.client.IncompleteRead:
        return {"status": "HTTP_INCOMPLETE_RESPONSE", "phase": phase}
    except http.client.HTTPException:
        return {"status": "HTTP_PROTOCOL_ERROR", "phase": phase}
    except OSError:
        return {"status": "HTTP_TRANSPORT_ERROR", "phase": phase}
    finally:
        conn.close()


def http_root_status(ip: str | None, port: int, timeout: float = 3.0) -> dict:
    """GET /, inspect response status only; never read body or retain headers."""
    if not ip:
        return {"status": "NOT_REQUESTED"}
    try:
        address = ipaddress.IPv4Address(ip)
        if not address.is_private or address.is_unspecified or not 1 <= port <= 65535:
            return {"status": "INVALID_TARGET"}
    except ipaddress.AddressValueError:
        return {"status": "INVALID_TARGET"}
    conn = http.client.HTTPConnection(str(address), port, timeout=timeout)
    phase = "REQUEST"
    try:
        conn.request("GET", "/", headers={"Connection": "close"})
        phase = "HEADERS"
        reply = conn.getresponse()
        return {"status": "HTTP_STATUS_OBSERVED", "code": reply.status}
    except TimeoutError:
        return {"status": "HTTP_TIMEOUT", "phase": phase}
    except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
        return {"status": "HTTP_CONNECTION_CLOSED", "phase": phase}
    except http.client.HTTPException:
        return {"status": "HTTP_PROTOCOL_ERROR", "phase": phase}
    except OSError:
        return {"status": "HTTP_TRANSPORT_ERROR", "phase": phase}
    finally:
        conn.close()



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
        "c6_profile": inventory.get("c6", {}).get("profile"),
        "c6_pin_source": inventory.get("c6", {}).get("source"),
        "usb_serial_ports": devices,
        "tcp": net,
        "ap_root": http_root_status(ip, port),
        "ap_sysinfo": http_sysinfo(ip, port),
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
