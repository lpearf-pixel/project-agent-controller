# ESP32-S3 + ESP32-C6 OEPL bring-up — PAC-controlled pilot

**Tracking:** [PAC Issue #7](https://github.com/lpearf-pixel/project-agent-controller/issues/7).
This sidecar lives on an experimental branch, not on `main` or `stable`.
The hardware is the owner's ESP32-S3 N16R8 + ESP32-C6 DEV-KIT-N16-M,
using locally installed OpenEPaperLink firmware. The actual local checkout
is `/home/parallels/OpenEPaperLink-git`, PlatformIO source
`ESP32_AP-Flasher/`; **do not clone a second firmware tree unnecessarily**.

## Physical vs automatic tasks

Only the owner touches pins, cables, board power, or supplies deliberate
permission for flashing/resetting physical hardware. Everything in this
phase is **read-only inspection or firmware compilation**. A successful
Python test or PlatformIO build is NOT proof that S3 and C6 communicate.

Physical owner-only UART wiring, with both USB supplies disconnected:

| S3 | C6 | Meaning |
|---|---|---|
| GPIO13 (TX) | GPIO2 (RX) | S3 sends to radio |
| GPIO14 (RX) | GPIO3 (TX) | Radio responds to S3 |
| GND | GND | Shared signal reference |

Power each board separately by USB; never connect their 5V or 3V3 rails
while doing so. Keep GPIO47 (S3 reset control) and GPIO9 (C6 BOOT) **unconnected**
until a firmware-specific reset-path review is complete. Check the physical
pin-label numbers, not generic TX/RX silkscreen labels.

The official OEPL PlatformIO environment is `ESP32_S3_C6_BIG_AP`, requiring
S3 build flags TX=13, RX=14, RESET=47. C6 IDF configuration is expected
to use `CONFIG_OEPL_HARDWARE_UART_RX=2` and `TX=3`.
References:
- https://github.com/OpenEPaperLink/OpenEPaperLink/blob/master/ESP32_AP-Flasher/platformio.ini
- https://github.com/hillstub/OpenEPaperLink-Minimal-Spaghetti-AP

**Source-based C6 profile resolution:** In the exact OEPL source revision
`85f92f3839da2c5a7adb4be95c31f9a3fc1cd2ed`, the `DEFAULT`
ESP-IDF Kconfig profile defines C6 protocol UART TX=GPIO3/RX=GPIO2
in `main/second_uart.h`. Numeric `CONFIG_OEPL_HARDWARE_UART_*`
entries are only produced for profiles such as `CUSTOM` or `4inch`;
their absence from `sdkconfig` is **not** a UART failure.
The probe now resolves the unique active profile, then reads either the
selected compile-time header branch or explicit numeric settings.
Unknown, duplicated, unsupported, or wiring-incompatible profiles block
the source gate. An offline source gate cannot prove that the **flashed
firmware** has identical bits or that the physical UART is communicating.

The prior symptom `ping failed` occurred after S3 Wi-Fi connection and
does **not** by itself prove an UART mismatch, physical wire fault or RF
failure. Prior `tagDB.json InvalidInput` does not justify clearing the
database or factory resetting the AP. Keep these error domains separate.

## First local activation (one time)

The controller source is in
`/media/psf/chatgpt/project-agent-controller-codex-pilot`.
The OEPL sidecar is in a separate Git worktree so that the existing
PAC experiment branch remains untouched:

```bash
cd /media/psf/chatgpt/project-agent-controller-codex-pilot
git fetch origin feat/oepl-s3-c6-pilot-20261016
git worktree add /media/psf/chatgpt/oepl-s3-c6-controller \
  feat/oepl-s3-c6-pilot-20261016
cd /media/psf/chatgpt/oepl-s3-c6-controller
python3 experiments/oepl-s3-c6/probe.py \
  --source /home/parallels/OpenEPaperLink-git
```

If the branch already exists in a different checkout, do not forcibly
recreate it. The above is one-time local installation; GitHub actions
cannot open Parallels USB devices.

The following command opts into a 30-minute, **read-only user-level**
systemd timer. It records sanitized source SHAs and GPIO-flag check
results in Ubuntu-local `~/.local/share/pac-codex-loop/oepl-s3-c6`.
Only **changed** summaries are posted to [Issue #7](https://github.com/lpearf-pixel/project-agent-controller/issues/7)
via authenticated `gh issue comment`. It does not include Wi-Fi
credentials, raw serial logs, or token contents.

```bash
bash experiments/oepl-s3-c6/install-user-timer.sh
```

User services normally require an active user manager/session. This
installer does not call sudo or enable system-wide lingering. Check
`systemctl --user list-timers` if reports stop after logout.

One-time **build-only** checks are optional and never flash a chip:

```bash
bash experiments/oepl-s3-c6/run_on_ubuntu.sh --build-only
```

It uses existing `pio` / `idf.py` toolchains if available. Missing
toolchains, mismatched pin configuration and failed builds are reported
as blockers instead of silently installing tools or reflashing boards.
Logs remain on Ubuntu; only PASS/FAIL/TOOL_MISSING status is posted.
No GPIO is toggled, even if a USB device is attached.

To disable periodic monitoring:

```bash
systemctl --user disable --now pac-oepl-s3-c6.timer
```

## Offline log triage (no serial access, no secrets)

The tested `log_triage.py` parser separately counts S3 UART task startup,
radio ping failures, explicit ACK lines, Wi-Fi connectivity and database
load failures. It does **not** infer a broken wire from `ping failed`
or claim a live UART link from a copied log.

```bash
python3 experiments/oepl-s3-c6/log_triage.py --log /path/to/existing-s3.log
```

The output is a bounded symptom summary with no raw SSID/password lines.
The source log remains on Ubuntu; any future hosted status report should
share **only the summary**. No flashing or GPIO access is performed.

## Owner reports the three wires are connected — read-only Ubuntu check

Use an **ext4 Git checkout**, not the old Parallels shared path. A one-time
local invocation from Ubuntu reads configured firmware pin definitions,
lists USB serial symlinks and optionally probes the previously seen AP
address on TCP port 80. It **does not open serial TTYs**, change DTR/RTS,
reset either board, flash chips, modify GPIO, or transmit a radio command.

```bash
# From Ubuntu's own user account, one-time clone if not already present:
gh repo clone lpearf-pixel/project-agent-controller \
  "$HOME/oepl-s3-c6-stage" -- \
  --branch feat/oepl-s3-c6-pilot-20261016
cd "$HOME/oepl-s3-c6-stage"
python3 experiments/oepl-s3-c6/wired_probe.py \
  --source /home/parallels/OpenEPaperLink-git --ap-ip 192.168.2.192
bash experiments/oepl-s3-c6/run_on_ubuntu.sh --inspect
```

For an existing clean checkout, use `git pull --ff-only` rather than
recloning or deleting anything. `192.168.2.192` is **historical**; a
failed TCP probe may simply mean the AP changed address. The second command
publishes only sanitized config status, GPIO pin values, and the count of
USB serial entries to Issue #7 if GitHub CLI is authenticated; it never
publishes a private serial transcript.

The `wired_probe.py` command also sends a read-only **GET
`/sysinfo`** to the private LAN AP. OpenEPaperLink exposes its
`env`, `ap_version`, `ap_type` and `flashpin_defaults` there.
The output allowlists only those diagnostic fields and never exports
the AP alias, Wi-Fi details or tag database. A non-zero `ap_version`
is useful AP-reported radio metadata, **not** a fresh wire-continuity
test or proof that the installed C6 firmware matches this checkout.
No HTTP POST or `/radio_selftest` execution is performed.

### HTTP diagnostics after a TCP-only success

If TCP port 80 accepts connections but `/sysinfo` was previously
`HTTP_UNAVAILABLE`, update this branch and rerun `wired_probe.py`.
The new response distinguishes transport failure, request/header/body
timeout, premature disconnect, protocol errors, invalid JSON, and non-200
HTTP status. It only reports error *types*, not raw response bodies, URLs,
Wi-Fi details, credentials or tag database contents. HTTP sends
`Connection: close` to avoid hanging on a keep-alive stream.

For a second independent, non-destructive check in Ubuntu, compare only
HTTP response codes while **bypassing shell HTTP proxies**:

```bash
for endpoint in / /sysinfo; do
  curl --noproxy '*' --silent --show-error --max-time 6 \
    --output /dev/null \
    --write-out "endpoint=$endpoint http=%{http_code} seconds=%{time_total}\n" \
    "http://192.168.2.192$endpoint" || true
done
```

`curl` output is status-only; no raw headers/body are printed. **Do not
POST** to `/radio_selftest` or `/update_c6`: those operations can
alter hardware/radio state. If both endpoints have `http=000`, do
not infer a UART wire fault; first verify service identity and HTTP
handling. The reported historical LAN IP may change.

To avoid copying the next AP HTTP probe response back into chat,
a one-time inspection can post **only sanitized HTTP status**, phase,
HTTP code, and numeric AP radio type/version directly to Issue #7:

```bash
OEPL_AP_IP=192.168.2.192 \
  bash experiments/oepl-s3-c6/run_on_ubuntu.sh --inspect
```

This additional network GET is opt-in via `OEPL_AP_IP`; omitted by
default. As before it never opens TTY ports or initiates radio self-test.

`READY_FOR_HARDWARE_CHECK` means firmware configuration can be reviewed,
**not** that USB, UART, C6 radio, RF or actual wire continuity passed.
`TCP_REACHABLE` means only the historical S3 network endpoint accepts
connections. Real UART success needs a *fresh* serial log showing an
explicit C6 response on the correct serial device; opening CDC/USB JTAG
serial can alter DTR/RTS on some boards, so the default check deliberately
does not open ports. Any active serial capture is a separate operator-visible
step, never a side effect of the preflight.

## Deterministic milestones

- S0 (current): source SHA inventory, UART pin configuration audit, USB
  serial IDs, build toolchain visibility; offline tests in GitHub.
- S1: compile S3 `ESP32_S3_C6_BIG_AP` and C6 IDF project in a
  non-flashing build, record separate results and release versions.
- S2: implement a testable, read-only log classifier for S3 `ping failed`
  versus C6 boot / UART / missing RF heartbeat and unrelated tagDB issues.
- S3: require **owner wiring** and read-only, fresh serial evidence.
  No physical pass without actual UART handshake logs.
- S4: package firmware compatibility findings and bounded software
  improvements through PAC Draft PR and CI. No auto-merge, force-push,
  device write/erase, GPIO-drive or tag programming.

The PAC code-generation loop and the separate GitHub publisher currently
run under the **same Ubuntu account** and are *not credential-isolated*.
Therefore this sidecar does not silently enable unattended Codex execution,
background Git pushes, or credential-bearing shell actions. Independent
GitHub-side code changes and offline CI can continue, but **running Codex
on the physical Ubuntu firmware checkout requires first closing this
credential-isolation gap**. That is a software safety gate, not an ongoing
request for user approval.

The only operator interaction needed for hardware is pins/power; any
unknown runtime source or disconnected USB stays UNKNOWN, never PASS.
