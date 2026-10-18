"""S3 UART log classifier fixtures: never prints raw SSIDs or other sensitive data."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import log_triage


class TriageTests(unittest.TestCase):
    def test_ping_failure_does_not_imply_wiring_fault(self):
        raw = (
            "[WiFi-event 4] Connected to access point\n"
            "[WiFi-event 7] Obtained IP address: 192.168.2.192\n"
            "rxSerialTask starting\n"
            "deserializeJson() failed: InvalidInput\n"
            "ping failed\nping failed\nping failed\n"
        )
        result = log_triage.analyze(raw)
        self.assertEqual(result["radio_uart"], "REPEATED_PING_FAIL_NO_ACK_IN_SUPPLIED_LOG")
        self.assertEqual(result["counts"]["radio_ping_failed"], 3)
        self.assertEqual(result["counts"]["tagdb_error"], 1)
        self.assertEqual(result["counts"]["ap_wifi_connected"], 2)
        self.assertNotIn("192.168.", str(result))

    def test_explicit_ack_is_only_observed_in_supplied_log(self):
        result = log_triage.analyze("ping failed\nACK> ok, 111ms\n")
        self.assertEqual(result["radio_uart"], "ACK_OBSERVED_IN_SUPPLIED_LOG")
        self.assertIn("not verified as live", result["sources"])

    def test_missing_radio_evidence_is_unknown(self):
        result = log_triage.analyze("S3 booted\nConnected to access point\n")
        self.assertEqual(result["radio_uart"], "UNKNOWN")

    def test_never_copy_password_or_private_log_data(self):
        raw = "SSID=myhome password=secret123\nping failed\n"
        self.assertNotIn("secret123", str(log_triage.analyze(raw)))
        with tempfile.TemporaryDirectory() as path:
            file = Path(path) / "log.txt"
            file.write_text(raw)
            self.assertEqual(file.read_text(), raw)


if __name__ == "__main__":
    unittest.main()
