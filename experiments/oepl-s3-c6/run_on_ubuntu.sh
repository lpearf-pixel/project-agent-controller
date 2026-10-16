#!/usr/bin/env bash
# Deterministic, non-flashing OEPL hardware/software bring-up.
# Does not reset GPIO, connect power rails, touch production firmware or call Codex.
set -euo pipefail

PAC_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SOURCE="${OEPL_SOURCE:-/home/parallels/OpenEPaperLink-git}"
STATE_DIR="${PAC_CODEX_LOOP_DATA:-$HOME/.local/share/pac-codex-loop}/oepl-s3-c6"
ISSUE="lpearf-pixel/project-agent-controller#7"
MODE="${1:---inspect}"
build_incomplete=0

if [[ "$MODE" != "--inspect" && "$MODE" != "--build-only" ]]; then
  echo "Usage: bash experiments/oepl-s3-c6/run_on_ubuntu.sh [--inspect|--build-only]" >&2
  exit 2
fi
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
report="$STATE_DIR/inventory.json"
python3 "$PAC_ROOT/experiments/oepl-s3-c6/probe.py" \
  --source "$SOURCE" --output "$report"

status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])' "$report")"
if [[ "$status" == "SOURCE_MISSING" ]]; then
  echo "No source checkout; no flashing or changes attempted." >&2
  exit 2
fi
if [[ "$status" == "CONFIG_MISMATCH" ]]; then
  echo "Firmware GPIO flags mismatch. Stop and inspect; never guess pin wiring." >&2
  exit 1
fi

# Configuration/USB listing is entirely read-only. Builds, if requested,
# may create local build/cache artifacts but NEVER invoke device upload/flash.
if [[ "$MODE" == "--build-only" ]]; then
  s3_build="TOOL_MISSING"
  c6_build="TOOL_MISSING"
  if command -v pio >/dev/null 2>&1; then
    s3_build="FAIL"
    if timeout --signal=TERM --kill-after=10s 1500s \
        pio run --project-dir "$SOURCE/ESP32_AP-Flasher" \
        --environment ESP32_S3_C6_BIG_AP \
        >"$STATE_DIR/s3-build.log" 2>&1; then
      s3_build="PASS"
    fi
  fi
  if command -v idf.py >/dev/null 2>&1; then
    c6_build="FAIL"
    if timeout --signal=TERM --kill-after=10s 1500s \
        idf.py -C "$SOURCE/ARM_Tag_FW/OpenEPaperLink_esp32_C6_AP" build \
        >"$STATE_DIR/c6-build.log" 2>&1; then
      c6_build="PASS"
    fi
  fi
  # ESP-IDF builds can generate/change active sdkconfig. Re-inspect after
  # compilation so the report reflects effective post-build GPIO settings.
  python3 "$PAC_ROOT/experiments/oepl-s3-c6/probe.py" \
    --source "$SOURCE" --output "$report"
  post_status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])' "$report")"
  if [[ "$post_status" != "READY_FOR_HARDWARE_CHECK" ]]; then
    echo "Post-build UART pin configuration was not verified; no hardware claim." >&2
    build_incomplete=1
  fi

  # Only summary and bounded diagnostics are emitted, never raw logs.
  printf '{"s3_build":"%s","c6_build":"%s"}\n' "$s3_build" "$c6_build" \
    >"$STATE_DIR/build-summary.json"
  echo "S3 build: $s3_build; C6 build: $c6_build"
  if [[ "$s3_build" != "PASS" || "$c6_build" != "PASS" ]]; then
    echo "Build gate incomplete; no firmware flashed." >&2
    build_incomplete=1
  fi
fi

# Optional local AP GET; caller chooses IP and no serial/GPIO is touched.
wired="$STATE_DIR/wired-preflight.json"
rm -f "$wired"
if [[ -n "${OEPL_AP_IP:-}" ]]; then
  python3 "$PAC_ROOT/experiments/oepl-s3-c6/wired_probe.py" \
    --source "$SOURCE" --ap-ip "$OEPL_AP_IP" >"$wired"
fi

# Share ONLY sanitized metadata, never USB serial transcripts or Wi-Fi passwords.
# A one-time change-sensitive issue report lets the PAC reviewer progress
# asynchronously without requesting terminal screenshots.
if command -v gh >/dev/null 2>&1 && gh auth status -h github.com >/dev/null 2>&1; then
  summary="$STATE_DIR/github-summary.md"
  python3 - "$report" "$STATE_DIR/build-summary.json" "$summary" "$MODE" "$wired" <<'PY'
import json
import pathlib
import sys

inventory = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
build_path = pathlib.Path(sys.argv[2])
# An inspection is not a build: never imply a stale prior build was performed.
build = (json.loads(build_path.read_text(encoding="utf-8"))
         if sys.argv[4] == "--build-only" and build_path.is_file() else None)
lines = [
    "Automated **non-flashing** OEPL S3/C6 probe:",
    "",
    f"- Source commit: `{inventory.get('source_sha') or 'unknown'}`",
    f"- Configuration gate: **{inventory['status']}**",
    f"- S3 PlatformIO flags: `{inventory.get('s3', {}).get('flags', {})}`",
    f"- C6 IDF UART flags: `{inventory.get('c6', {}).get('flags', {})}`",
    f"- C6 active hardware profile: `{inventory.get('c6', {}).get('profile') or 'unknown'}`",
    f"- C6 pin source: `{inventory.get('c6', {}).get('source') or 'unknown'}`",
    f"- USB serial IDs present: {len(inventory.get('usb_serial_ids', []))}",
    f"- Build outcomes: {build or 'not requested'}",
    "- No flashing, resetting, GPIO changes, power wiring or tag commands performed.",
]
wired_path = pathlib.Path(sys.argv[5])
if wired_path.is_file():
    wired_data = json.loads(wired_path.read_text(encoding="utf-8"))
    ap = wired_data.get("ap_sysinfo", {})
    metadata = ap.get("fields", {}) if isinstance(ap.get("fields"), dict) else {}
    lines.append(f"- AP read-only HTTP status: `{ap.get('status', 'UNKNOWN')}`")
    lines.append(f"- AP HTTP phase: `{ap.get('phase', 'none')}`")
    if type(ap.get("code")) is int:
        lines.append(f"- AP HTTP response code: `{ap['code']}`")
    for key in ("ap_version", "ap_type"):
        value = metadata.get(key)
        if type(value) is int:
            lines.append(f"- AP-reported {key}: `{value}`")
    lines.append("- UART/RF handshake is NOT_TESTED; API metadata is not wire proof.")
pathlib.Path(sys.argv[3]).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  fingerprint="$(sha256sum "$summary" | awk '{print $1}')"
  previous=""
  [[ -f "$STATE_DIR/last-report-sha256" ]] && previous="$(cat "$STATE_DIR/last-report-sha256")"
  if [[ "$fingerprint" != "$previous" ]]; then
    if gh issue comment 7 --repo lpearf-pixel/project-agent-controller \
        --body-file "$summary" >/dev/null; then
      printf '%s\n' "$fingerprint" > "$STATE_DIR/last-report-sha256"
      echo "Reported sanitized OEPL state to $ISSUE"
    else
      echo "WARN: issue reporting failed; local evidence retained." >&2
    fi
  fi
fi
echo "Evidence: $STATE_DIR"
echo "Hardware gate: only owner physically wires S3/C6. No software can verify disconnected pins."
if [[ "$build_incomplete" -ne 0 ]]; then
  exit 1
fi
