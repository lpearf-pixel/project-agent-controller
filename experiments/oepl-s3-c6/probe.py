#!/usr/bin/env python3
"""Read-only OEPL S3+C6 preflight. Does not access GPIO, USB devices or flash.

Known hardware:
- S3 N16R8, OEPL PlatformIO env ESP32_S3_C6_BIG_AP
- C6 DEV-KIT N16-M, OEPL UART RX GPIO2, TX GPIO3
Physical wiring and power remain operator-controlled.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TARGET = "ESP32_S3_C6_BIG_AP"
S3_FLAGS = {
    "FLASHER_AP_TXD": "13",
    "FLASHER_AP_RXD": "14",
    "FLASHER_AP_RESET": "47",
}
C6_FLAGS = {
    "CONFIG_OEPL_HARDWARE_UART_RX": "2",
    "CONFIG_OEPL_HARDWARE_UART_TX": "3",
}
DEFAULT_ROOT = Path("/home/parallels/OpenEPaperLink-git")


def git_head(root):
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def s3_config(root):
    ini = root / "ESP32_AP-Flasher" / "platformio.ini"
    if not ini.is_file():
        return {"status": "UNKNOWN", "reason": "platformio.ini not found", "flags": {}}
    raw = ini.read_text(encoding="utf-8", errors="replace")
    header = "[env:" + TARGET + "]"
    parts = re.split(r"(?m)^\s*\[env:[^\]]+\]\s*$", raw)
    headers = re.findall(r"(?m)^\s*(\[env:[^\]]+\])\s*$", raw)
    matched = next((parts[i + 1] for i, key in enumerate(headers)
                    if key == header and i + 1 < len(parts)), None)
    if matched is None:
        return {"status": "UNKNOWN", "reason": "expected S3 env absent", "flags": {}}
    values = {}
    duplicate = False
    for name in S3_FLAGS:
        matches = re.findall(
            r"(?m)^\s*-D\s+" + re.escape(name) + r"\s*=\s*([^\s;]+)",
            matched,
        )
        duplicate |= len(matches) > 1
        values[name] = matches[0] if len(matches) == 1 else None
    if duplicate:
        status = "MISMATCH"
    elif any(value is None for value in values.values()):
        status = "UNKNOWN"
    elif all(values[k] == v for k, v in S3_FLAGS.items()):
        status = "PASS"
    else:
        status = "MISMATCH"
    return {"status": status, "flags": values}


def c6_config(root):
    """Resolve *effective source* C6 UART pins from active profile + header.

    Kconfig only writes GPIO pin integers for CUSTOM and 4inch profiles.
    Default/Lilygo/PoE/Elecrow firmware pins are defined by second_uart.h.
    Inspect both sources, never mistake a missing numeric key for a bad pin.
    This is a *source* audit, not installed firmware identity verification.
    """
    directory = root / "ARM_Tag_FW" / "OpenEPaperLink_esp32_C6_AP"
    active = directory / "sdkconfig"
    header = directory / "main" / "second_uart.h"
    base = {"flags": {}, "config_files": [], "profile": None}
    if not directory.is_dir() or not active.is_file():
        return {**base, "status": "UNKNOWN", "reason": "active C6 sdkconfig absent"}
    if active.stat().st_size > 2_000_000:
        return {**base, "status": "UNKNOWN", "reason": "active sdkconfig too large"}

    body = active.read_text(encoding="utf-8", errors="replace")
    selected = re.findall(
        r"(?m)^CONFIG_OEPL_HARDWARE_PROFILE_([A-Za-z0-9_]+)=y[ \t]*$", body
    )
    base["config_files"] = ["sdkconfig"]
    if len(selected) != 1:
        status = "UNKNOWN" if not selected else "MISMATCH"
        return {**base, "status": status, "reason": "hardware profile not unique"}

    profile = selected[0]
    base["profile"] = profile
    supported = {"DEFAULT", "LILYGO", "POE_AP", "ELECROW_C6", "CUSTOM", "4inch"}
    if profile not in supported:
        return {**base, "status": "UNKNOWN", "reason": "unsupported hardware profile"}

    # Explicit ints may be omitted entirely for the DEFAULT profile.
    configured = {}
    for name in C6_FLAGS:
        matches = re.findall(r"(?m)^" + re.escape(name) + r"=([0-9]+)[ \t]*$", body)
        occurrences = re.findall(r"(?m)^" + re.escape(name) + r"=", body)
        if len(occurrences) > 1:
            return {**base, "status": "MISMATCH", "reason": "duplicate UART key"}
        configured[name] = matches[0] if len(matches) == 1 else None

    if profile in {"CUSTOM", "4inch"}:
        values = configured
        source = "sdkconfig"
    else:
        if not header.is_file() or header.stat().st_size > 200_000:
            return {**base, "status": "UNKNOWN", "reason": "C6 UART header absent"}
        h = header.read_text(encoding="utf-8", errors="replace")
        symbol = "CONFIG_OEPL_HARDWARE_PROFILE_" + profile
        block = re.search(
            r"(?ms)^[ \t]*#(?:if|elif)[ \t]+defined\("
            + re.escape(symbol)
            + r"\)(.*?)(?=^[ \t]*#(?:elif|else|endif)\b)",
            h,
        )
        if block is None:
            return {**base, "status": "UNKNOWN", "reason": "profile pins absent from header"}
        values = {}
        for name in C6_FLAGS:
            matches = re.findall(
                r"(?m)^[ \t]*#define[ \t]+" + re.escape(name) + r"[ \t]+([0-9]+)[ \t]*$",
                block.group(1),
            )
            values[name] = matches[0] if len(matches) == 1 else None
        source = "second_uart.h"
        # If a profile somehow has numeric config too, a conflicting value is unsafe.
        if any(configured[k] is not None and configured[k] != values[k] for k in C6_FLAGS):
            return {
                **base, "status": "MISMATCH", "flags": values,
                "reason": "sdkconfig and header disagree", "source": source,
            }

    if any(value is None for value in values.values()):
        status = "UNKNOWN"
    elif all(values[k] == expected for k, expected in C6_FLAGS.items()):
        status = "PASS"
    else:
        status = "MISMATCH"
    return {**base, "status": status, "flags": values, "source": source}


def usb_ports():
    # Only list symlink names: never open, reset or configure USB devices.
    root = Path("/dev/serial/by-id")
    if not root.is_dir():
        return []
    return sorted(x.name for x in root.iterdir() if x.is_symlink())[:20]


def report(root):
    # Keep reports free of Wi-Fi credentials, raw sdkconfig and serial logs.
    if not root.is_dir():
        return {
            "status": "SOURCE_MISSING", "source": str(root),
            "next": "Ubuntu checkout not mounted; cannot inspect firmware",
        }
    s3 = s3_config(root)
    c6 = c6_config(root)
    if "MISMATCH" in (s3["status"], c6["status"]):
        status = "CONFIG_MISMATCH"
    elif s3["status"] == "PASS" and c6["status"] == "PASS":
        status = "READY_FOR_HARDWARE_CHECK"
    else:
        status = "NEEDS_READ_ONLY_INSPECTION"
    return {
        "status": status,
        "source": str(root),
        "source_sha": git_head(root),
        "hardware": {"s3": "ESP32-S3 N16R8", "c6": "ESP32-C6 DEV-KIT N16-M"},
        "s3": s3,
        "c6": c6,
        "usb_serial_ids": usb_ports(),
        "safe_boundary": {
            "can_auto_build": True,
            "can_auto_flash": False,
            "can_auto_change_gpio_or_power": False,
            "requires_manual_wiring": True,
        },
        "next": (
            "Software configuration can be reviewed automatically; UART success "
            "requires physical wiring by the owner"
        ),
    }


def main():
    p = argparse.ArgumentParser(description="Read-only OEPL S3+C6 inventory")
    p.add_argument("--source", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--output", type=Path, help="optional JSON evidence file")
    args = p.parse_args()
    try:
        state = report(args.source.expanduser().resolve())
        payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            dest = args.output.expanduser().resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp = dest.with_suffix(dest.suffix + ".tmp")
            temp.write_text(payload, encoding="utf-8")
            os.replace(temp, dest)
        print(payload, end="")
        return 0 if state["status"] != "SOURCE_MISSING" else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR:", str(exc)[:250], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
