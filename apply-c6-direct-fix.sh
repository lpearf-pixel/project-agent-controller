#!/usr/bin/env bash
set -euo pipefail

# Apply only the C6 /32 bypass. Run manually as: sudo bash apply-c6-direct-fix.sh
# No files are deleted; timestamped backups are retained.

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root: sudo bash $0" >&2
  exit 2
fi

command -v jq >/dev/null
command -v sing-box >/dev/null
command -v nft >/dev/null

stamp=$(date +%Y%m%d-%H%M%S)
config=/etc/sing-box/config.json
guard=/etc/nftables-codex.nft
new_config=/tmp/oepl-sing-box-config-${stamp}.json

cp -a "$config" "${config}.oepl-before-${stamp}"
cp -a "$guard" "${guard}.oepl-before-${stamp}"
nft -a list ruleset > "/tmp/oepl-nft-before-${stamp}.txt"
ip rule show > "/tmp/oepl-ip-rule-before-${stamp}.txt"
ip route show table all > "/tmp/oepl-ip-route-before-${stamp}.txt"

jq '
  (.inbounds |= map(
    if .tag == "tun-in"
    then .route_exclude_address =
      (((.route_exclude_address // []) + ["192.168.2.192/32"]) | unique)
    else .
    end
  ))
' "$config" > "$new_config"

sing-box check -c "$new_config"
install -o root -g root -m 0644 "$new_config" "$config"

if ! grep -Fq 'ip daddr 192.168.2.192 tcp dport 80 accept' "$guard"; then
  awk '
    /# Block other physical-interface traffic/ {
      print "    # OEPL C6 AP direct HTTP allow"
      print "    oifname \"enp0s5\" ip daddr 192.168.2.192 tcp dport 80 accept"
      print ""
    }
    { print }
  ' "$guard" > "/tmp/oepl-nftables-codex-${stamp}.nft"
  install -o root -g root -m 0644 "/tmp/oepl-nftables-codex-${stamp}.nft" "$guard"
fi

# Apply the narrow runtime filter rule before the existing physical-interface DROP.
# The source file is loaded in the correct order; this also repairs an older
# runtime rule that may have been appended after the DROP.
drop_handle=$(nft -a list chain inet codex_guard output \
  | awk '/oifname "enp0s5" counter.* drop/ {print $NF; exit}')
if [[ -n "$drop_handle" ]]; then
  nft insert rule inet codex_guard output position "$drop_handle" \
    oifname "enp0s5" ip daddr 192.168.2.192 tcp dport 80 accept
else
  nft add rule inet codex_guard output \
    oifname "enp0s5" ip daddr 192.168.2.192 tcp dport 80 accept
fi

systemctl reload sing-box
echo "C6 direct bypass installed; backups use suffix .oepl-before-${stamp}"
