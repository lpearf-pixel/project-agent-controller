#!/usr/bin/env python3
"""Read-only M3 AP observation; never opens USB or sends device commands."""
from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from wired_probe import http_sysinfo, tcp_probe

DEFAULT_IP = "192.168.2.192"
CRASH_REASONS = {"panic", "watchdog", "task watchdog", "other watchdog", "brownout"}
FIELD_ALLOWLIST = (
    "env", "ap_version", "ap_type", "ap_typename", "channel", "hasC6",
    "resetreason", "crashed", "buildversion",
)


def sanitize_sysinfo_fields(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    result = {}
    for key in FIELD_ALLOWLIST:
        value = data.get(key)
        if value is None or not isinstance(value, (str, int, bool)):
            continue
        if len(str(value)) <= 100:
            result[key] = value
    return result


def sample(ip: str, port: int, timeout: float) -> dict:
    """Capture TCP reachability and /sysinfo separately, without raw bodies."""
    tcp = tcp_probe(ip, port, timeout)
    info = http_sysinfo(ip, port, timeout)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "tcp": tcp,
        "api_status": info.get("status"),
        "api_phase": info.get("phase"),
        "api_code": info.get("code"),
        "fields": sanitize_sysinfo_fields(info.get("fields", {})),
    }


def _is_crash(fields: dict) -> bool:
    reason = str(fields.get("resetreason", "")).strip().lower()
    return bool(fields.get("crashed")) or reason in CRASH_REASONS


def summarize_samples(samples: list[dict]) -> dict:
    api_ok = [s for s in samples if s.get("api_status") == "HTTP_INFO_OBSERVED"]
    api_failures = [s for s in samples if s.get("api_status") not in (None, "HTTP_INFO_OBSERVED")]
    fields = [s.get("fields", {}) for s in api_ok if isinstance(s.get("fields", {}), dict)]
    versions = sorted({str(f["ap_version"]) for f in fields if "ap_version" in f})
    types = sorted({str(f.get("ap_typename", f.get("ap_type"))) for f in fields
                    if f.get("ap_typename") is not None or f.get("ap_type") is not None})
    crash_flags = [_is_crash(f) for f in fields]
    panic_transitions = sum(
        current and not previous
        for previous, current in zip([False, *crash_flags], crash_flags, strict=False)
    )
    radio_none = sum(
        f.get("ap_version") in (None, 0, "0") and
        f.get("ap_type") in (None, 0, "0") and
        f.get("hasC6") not in (1, True, "1")
        for f in fields
    )
    return {
        "sample_count": len(samples),
        "api_ok_count": len(api_ok),
        "api_failure_count": len(api_failures),
        "api_statuses": sorted({str(s.get("api_status")) for s in samples}),
        "tcp_statuses": sorted({str(s.get("tcp", {}).get("status")) for s in samples}),
        "radio_versions": versions,
        "radio_types": types,
        "c6_firmware_version": versions[-1] if versions else None,
        "reset_reasons": sorted({str(f["resetreason"]) for f in fields if f.get("resetreason")}),
        "panic_observation_count": sum(crash_flags),
        "new_panic_count": panic_transitions,
        "radio_none_count": radio_none,
        # HTTP /sysinfo does not expose WebSocket apstate or uptime.
        "radio_disconnect_count": None,
        "uptime_seconds": None,
        "uart_status": "NOT_TESTED",
        "interpretation": (
            "API failures are not counted as radio disconnects; /sysinfo lacks "
            "live apstate and uptime, and UART was not opened."
        ),
    }


def run(args: argparse.Namespace) -> dict:
    samples = []
    deadline = time.monotonic() + args.duration
    first = True
    while first or time.monotonic() < deadline:
        first = False
        samples.append(sample(args.ap_ip, args.port, args.timeout))
        if args.once or time.monotonic() >= deadline:
            break
        time.sleep(min(args.interval, max(0.0, deadline - time.monotonic())))
    summary = summarize_samples(samples)
    summary.update({"ap_ip": args.ap_ip, "port": args.port, "duration_requested": args.duration,
                    "interval": args.interval, "samples": samples})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="No-touch OEPL M3 stability observer")
    parser.add_argument("--ap-ip", default=DEFAULT_IP)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.duration < 0 or args.interval <= 0 or args.timeout <= 0:
        parser.error("duration must be non-negative; interval and timeout must be positive")
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for item in result["samples"]:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    summary_path = Path(str(args.output) + ".summary.json")
    summary_path.write_text(json.dumps({k: v for k, v in result.items() if k != "samples"},
                                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
