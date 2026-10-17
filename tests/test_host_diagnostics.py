"""Offline contract tests for the host and Mac read-only diagnostics."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "host-readonly-probe.sh"
MAC = ROOT / "mac-readonly-network-probe.sh"
REPORT = ROOT / "diagnosis-report.md"


class HostDiagnosticContractTests(unittest.TestCase):
    def test_host_probe_records_tcp_and_first_byte_timings(self):
        text = HOST.read_text(encoding="utf-8")
        self.assertIn("time_connect", text)
        self.assertIn("time_starttransfer", text)
        self.assertIn("tcp_connect_failed", text)
        self.assertIn("http_first_byte_timeout", text)

    def test_host_probe_uses_stable_by_id_inventory_and_hashes_identity(self):
        text = HOST.read_text(encoding="utf-8")
        self.assertIn("/dev/serial/by-id", text)
        self.assertIn("sha256sum", text)
        self.assertIn("ID_SERIAL_SHORT", text)
        self.assertNotIn('printf "%s" "$serial"', text)

    def test_host_probe_reports_network_and_proxy_presence_without_values(self):
        text = HOST.read_text(encoding="utf-8")
        for token in (
            "ip route", "ip -br addr", "ip neigh", "http_proxy", "https_proxy", "no_proxy"
        ):
            self.assertIn(token, text)
        self.assertIn("firewall", text)
        self.assertNotIn("env |", text)

    def test_mac_probe_is_read_only_and_target_scoped(self):
        text = MAC.read_text(encoding="utf-8")
        self.assertIn('uname -s', text)
        self.assertIn('platform_mismatch', text)
        for token in (
            "192.168.2.192", "route -n get", "ifconfig", "scutil --proxy", "curl --noproxy"
        ):
            self.assertIn(token, text)
        for forbidden in ("/dev/tty", "stty", "sudo", "route add", "ifconfig .* up"):
            self.assertNotIn(forbidden, text)

    def test_report_carries_explicit_unknowns_and_no_uart_claim(self):
        report = REPORT.read_text(encoding="utf-8")
        for token in (
            "tcp_connect_failed",
            "http_first_byte_timeout",
            "NAT",
            "USB",
            "ID_SERIAL_SHORT",
            "UART",
            "not proven",
        ):
            self.assertIn(token, report)
        self.assertNotIn("UART_PASS", report)


if __name__ == "__main__":
    unittest.main()
