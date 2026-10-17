#!/usr/bin/env bash
set -u

# Read-only Ubuntu host probe for OEPL S3/C6 diagnostics.
# It never opens a tty, changes modem control lines, writes devices, or
# changes network state. USB serial values are used only to derive hashes.

TARGET_IP="192.168.2.192"
HTTP_URL="http://${TARGET_IP}/"
CONNECT_TIMEOUT="4"
TOTAL_TIMEOUT="8"

json_escape() {
    sed 's/\\/\\\\/g; s/"/\\"/g; s/[^[:print:]]//g'
}

json_string() {
    printf '%s' "$1" | json_escape
}

json_lines() {
    local first=true line
    printf '['
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        if [ "$first" = true ]; then first=false; else printf ','; fi
        printf '"%s"' "$(printf '%s' "$line" | json_escape)"
    done
    printf ']'
}

env_present() {
    local name="$1"
    [ -n "${!name-}" ] && printf true || printf false
}

udev_prop() {
    local dev="$1" key="$2"
    udevadm info --query=property --name="$dev" 2>/dev/null \
        | sed -n "s/^${key}=//p" | head -n 1 | json_escape
}

device_json() {
    local name="$1" dev="/dev/${1}"
    local exists=false readable=false perms=""
    local vendor="" model="" vendor_id="" model_id="" driver="" subsystem=""
    if [ -e "$dev" ]; then
        exists=true
        [ -r "$dev" ] && readable=true
        perms="$(ls -l -- "$dev" 2>/dev/null | sed 's/[[:space:]][^[:space:]]*$//' | json_escape)"
        vendor="$(udev_prop "$dev" ID_VENDOR)"
        model="$(udev_prop "$dev" ID_MODEL)"
        vendor_id="$(udev_prop "$dev" ID_VENDOR_ID)"
        model_id="$(udev_prop "$dev" ID_MODEL_ID)"
        driver="$(udev_prop "$dev" ID_USB_DRIVER)"
        subsystem="$(udev_prop "$dev" SUBSYSTEM)"
    fi
    printf '{"tty":"%s","exists":%s,"readable":%s,"permissions":"%s","vendor":"%s","model":"%s","vendor_id":"%s","model_id":"%s","driver":"%s","subsystem":"%s"}' \
        "$name" "$exists" "$readable" "$perms" "$vendor" "$model" "$vendor_id" "$model_id" "$driver" "$subsystem"
}

stable_usb_json() {
    local first=true link base serial stable_hash devnode vendor model vendor_id model_id driver
    printf '['
    for link in /dev/serial/by-id/*; do
        [ -L "$link" ] || continue
        base="$(basename "$link")"
        serial="$(udev_prop "$link" ID_SERIAL_SHORT)"
        # The raw ID_SERIAL_SHORT and by-id name never enter the output.
        stable_hash="$(printf '%s\n' "${serial}|${base}" | sha256sum | awk '{print $1}')"
        devnode="$(readlink -f -- "$link" 2>/dev/null | sed 's#^/dev/##')"
        vendor="$(udev_prop "$link" ID_VENDOR)"
        model="$(udev_prop "$link" ID_MODEL)"
        vendor_id="$(udev_prop "$link" ID_VENDOR_ID)"
        model_id="$(udev_prop "$link" ID_MODEL_ID)"
        driver="$(udev_prop "$link" ID_USB_DRIVER)"
        if [ "$first" = true ]; then first=false; else printf ','; fi
        printf '{"stable_id_sha256":"%s","devnode":"%s","vendor":"%s","model":"%s","vendor_id":"%s","model_id":"%s","driver":"%s"}' \
            "$stable_hash" "$(json_string "$devnode")" "$vendor" "$model" "$vendor_id" "$model_id" "$driver"
    done
    printf ']'
}

classify_http() {
    local rc="$1" code="$2" connect="$3" start="$4"
    case "$rc" in
        0) [ "$code" = "000" ] && printf 'http_transport_error' || printf 'http_response' ;;
        5|6|7) printf 'tcp_connect_failed' ;;
        28)
            if awk -v c="$connect" -v limit="$CONNECT_TIMEOUT" -v s="$start" \
                'BEGIN { exit !(s < 0.001 && c >= limit - 0.1) }'; then
                printf 'tcp_connect_timeout'
            else
                printf 'http_first_byte_timeout'
            fi
            ;;
        52) printf 'http_no_response' ;;
        *) printf 'http_transport_error' ;;
    esac
}

http_line=""
http_rc=0
http_line="$(curl --noproxy '*' --connect-timeout "$CONNECT_TIMEOUT" --max-time "$TOTAL_TIMEOUT" \
    -sS -o /dev/null \
    -w 'HTTP=%{http_code} CONNECT=%{time_connect} STARTTRANSFER=%{time_starttransfer} TOTAL=%{time_total}\n' \
    "$HTTP_URL" 2>/dev/null)" || http_rc=$?
http_code="$(printf '%s' "$http_line" | sed -n 's/^HTTP=\([0-9][0-9][0-9]\).*$/\1/p')"
http_connect="$(printf '%s' "$http_line" | sed -n 's/.*CONNECT=\([^ ]*\).*/\1/p')"
http_starttransfer="$(printf '%s' "$http_line" | sed -n 's/.*STARTTRANSFER=\([^ ]*\).*/\1/p')"
http_total="$(printf '%s' "$http_line" | sed -n 's/.*TOTAL=\([^ ]*\).*/\1/p')"
[ -n "$http_code" ] || http_code="000"
[ -n "$http_connect" ] || http_connect="0.000000"
[ -n "$http_starttransfer" ] || http_starttransfer="0.000000"
[ -n "$http_total" ] || http_total="0.000000"
http_class="$(classify_http "$http_rc" "$http_code" "$http_connect" "$http_starttransfer")"

route_lines="$(ip route 2>/dev/null || true)"
addr_lines="$(ip -br addr 2>/dev/null || true)"
neigh_lines="$(ip neigh show 2>/dev/null || true)"
firewall_state="unavailable"
if command -v ufw >/dev/null 2>&1; then
    firewall_state="$(ufw status 2>/dev/null | head -n 1 | json_escape)"
    [ -n "$firewall_state" ] || firewall_state="unavailable"
fi

printf '{"probe":"oepl-host-readonly","target_ip":"%s","network":{"interfaces":' "$(json_string "$TARGET_IP")"
printf '%s' "$addr_lines" | json_lines
printf ',"routes":'
printf '%s' "$route_lines" | json_lines
printf ',"neighbors":'
printf '%s' "$neigh_lines" | json_lines
printf ',"proxy_presence":{"http_proxy":%s,"https_proxy":%s,"all_proxy":%s,"no_proxy":%s},"firewall":"%s"}' \
    "$(env_present http_proxy)" "$(env_present https_proxy)" "$(env_present all_proxy)" \
    "$(env_present no_proxy)" "$firewall_state"
printf ',"http":{"url":"%s","classification":"%s","code":"%s","curl_exit":%s,"time_connect":%s,"time_starttransfer":%s,"time_total":%s}' \
    "$(json_string "$HTTP_URL")" "$http_class" "$http_code" "$http_rc" \
    "$http_connect" "$http_starttransfer" "$http_total"
printf ',"tty_nodes":['
device_json ttyACM1
printf ','
device_json ttyACM2
printf '],"stable_usb_ids":'
stable_usb_json
printf ',"safety":{"opened_tty":false,"changed_modem_lines":false,"wrote_device":false,"changed_network":false}}\n'
