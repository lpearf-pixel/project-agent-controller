#!/usr/bin/env bash
set -u

# Read-only macOS network probe for the historical OEPL AP address.
# It does not change routes, interfaces, firewall, or USB/serial state.

TARGET_IP="192.168.2.192"
HTTP_URL="http://${TARGET_IP}/"
CONNECT_TIMEOUT="4"
TOTAL_TIMEOUT="8"
PLATFORM="$(uname -s 2>/dev/null || printf unknown)"

if [ "$PLATFORM" != "Darwin" ]; then
    printf '{"probe":"oepl-mac-readonly-network","status":"platform_mismatch","platform":"%s","expected":"Darwin","target_ip":"%s","safety":{"changed_routes":false,"changed_interfaces":false,"changed_firewall":false,"opened_tty":false}}\n' \
        "$(printf '%s' "$PLATFORM" | sed 's/\\/\\\\/g; s/"/\\"/g')" \
        "$(printf '%s' "$TARGET_IP" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    exit 2
fi

json_escape() {
    sed 's/\\/\\\\/g; s/"/\\"/g; s/[^[:print:]]//g'
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

proxy_enabled() {
    local key="$1"
    scutil --proxy 2>/dev/null | awk -v key="$key" \
        '$1 == key && $3 == 1 { found=1 } END { if (found) print "true"; else print "false" }'
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

route_lines="$(route -n get "$TARGET_IP" 2>/dev/null || true)"
interface_lines="$(ifconfig 2>/dev/null || true)"
arp_lines="$(arp -n "$TARGET_IP" 2>/dev/null || true)"
http_proxy_enabled="$(proxy_enabled HTTPEnable)"
https_proxy_enabled="$(proxy_enabled HTTPSEnable)"

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

printf '{"probe":"oepl-mac-readonly-network","target_ip":"%s","network":{"route":' "$(printf '%s' "$TARGET_IP" | json_escape)"
printf '%s' "$route_lines" | json_lines
printf ',"interfaces":'
printf '%s' "$interface_lines" | json_lines
printf ',"arp":'
printf '%s' "$arp_lines" | json_lines
printf ',"proxy_presence":{"http_proxy":%s,"https_proxy":%s,"all_proxy":%s,"no_proxy":%s,"system_http":%s,"system_https":%s}}' \
    "$(env_present http_proxy)" "$(env_present https_proxy)" "$(env_present all_proxy)" \
    "$(env_present no_proxy)" "$http_proxy_enabled" "$https_proxy_enabled"
printf ',"http":{"url":"%s","classification":"%s","code":"%s","curl_exit":%s,"time_connect":%s,"time_starttransfer":%s,"time_total":%s}' \
    "$(printf '%s' "$HTTP_URL" | json_escape)" "$http_class" "$http_code" "$http_rc" \
    "$http_connect" "$http_starttransfer" "$http_total"
printf ',"safety":{"changed_routes":false,"changed_interfaces":false,"changed_firewall":false,"opened_tty":false}}\n'
