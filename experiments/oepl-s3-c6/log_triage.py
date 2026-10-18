#!/usr/bin/env python3
"""Summarize bounded S3 serial logs without disclosing private raw log lines.

This is diagnostic evidence, NOT a GPIO or radio presence test. Do not
treat a historic log as live hardware validation.
"""
import argparse
import json
import re
import sys
from pathlib import Path

LIMIT = 512_000
PING_FAIL = re.compile(r"\bping failed\b", re.IGNORECASE)
ACK_OK = re.compile(r"\bACK>\s*ok\b", re.IGNORECASE)
RX_STARTED = re.compile(r"\brxSerialTask starting\b", re.IGNORECASE)
WIFI_OK = re.compile(r"\b(Obtained IP address|Connected to access point)\b", re.IGNORECASE)
BAD_DB = re.compile(r"\b(deserializeJson\(\) failed|unable to load tagDB)\b",
                    re.IGNORECASE)


def analyze(raw):
    counts = {
        "s3_uart_task_started": 0,
        "ap_wifi_connected": 0,
        "radio_ping_failed": 0,
        "radio_ack_seen": 0,
        "tagdb_error": 0,
    }
    for line in raw.splitlines()[:20_000]:
        counts["s3_uart_task_started"] += bool(RX_STARTED.search(line))
        counts["ap_wifi_connected"] += bool(WIFI_OK.search(line))
        counts["radio_ping_failed"] += bool(PING_FAIL.search(line))
        counts["radio_ack_seen"] += bool(ACK_OK.search(line))
        counts["tagdb_error"] += bool(BAD_DB.search(line))
    # Network and database evidence are explicitly orthogonal to radio UART.
    if counts["radio_ack_seen"] > 0:
        radio = "ACK_OBSERVED_IN_SUPPLIED_LOG"
    elif counts["radio_ping_failed"] >= 3:
        radio = "REPEATED_PING_FAIL_NO_ACK_IN_SUPPLIED_LOG"
    elif counts["radio_ping_failed"] > 0:
        radio = "SOME_PING_FAILURES"
    else:
        radio = "UNKNOWN"
    return {
        "radio_uart": radio,
        "counts": counts,
        "safe_interpretation": (
            "This report cannot prove live wiring, firmware compatibility, "
            "or radio RF operation; a failed ping does not identify a failed pin."
        ),
        "sources": "supplied log only (not verified as live)",
    }


def main():
    p = argparse.ArgumentParser(description="Read-only OEPL S3 log symptom triage")
    p.add_argument("--log", type=Path, required=True)
    args = p.parse_args()
    try:
        if args.log.stat().st_size > LIMIT:
            raise ValueError("log file exceeds safety limit")
        raw = args.log.read_text(encoding="utf-8", errors="replace")
        print(json.dumps(analyze(raw), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print("ERROR: cannot inspect log:", type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
