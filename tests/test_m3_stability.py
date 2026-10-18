"""Offline tests for M3 read-only stability aggregation."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "oepl-s3-c6"))
import m3_stability


class M3StabilityTests(unittest.TestCase):
    def test_sanitizes_known_sysinfo_fields_only(self):
        result = m3_stability.sanitize_sysinfo_fields({
            "ap_version": 33,
            "ap_type": 198,
            "ap_typename": "ESP32-C6",
            "hasC6": 1,
            "resetreason": "Power-on",
            "crashed": False,
            "alias": "private-alias",
            "ntpserver": "private.example",
        })
        self.assertEqual(result["ap_version"], 33)
        self.assertEqual(result["resetreason"], "Power-on")
        self.assertNotIn("alias", result)
        self.assertNotIn("ntpserver", result)

    def test_summary_separates_api_failures_from_radio_disconnects(self):
        samples = [
            {"api_status": "HTTP_INFO_OBSERVED", "fields": {
                "ap_version": 33, "ap_type": 198, "hasC6": 1,
                "resetreason": "Power-on", "crashed": False,
            }},
            {"api_status": "HTTP_TIMEOUT", "fields": {}},
            {"api_status": "HTTP_INFO_OBSERVED", "fields": {
                "ap_version": 33, "ap_type": 198, "hasC6": 1,
                "resetreason": "Power-on", "crashed": False,
            }},
        ]
        summary = m3_stability.summarize_samples(samples)
        self.assertEqual(summary["api_failure_count"], 1)
        self.assertIsNone(summary["radio_disconnect_count"])
        self.assertIsNone(summary["uptime_seconds"])
        self.assertEqual(summary["new_panic_count"], 0)

    def test_summary_detects_radio_none_without_calling_it_uart_failure(self):
        samples = [{
            "api_status": "HTTP_INFO_OBSERVED",
            "fields": {"ap_version": 0, "ap_type": 0, "hasC6": 0,
                        "resetreason": "Power-on", "crashed": False},
        }]
        summary = m3_stability.summarize_samples(samples)
        self.assertEqual(summary["radio_none_count"], 1)
        self.assertEqual(summary["uart_status"], "NOT_TESTED")


if __name__ == "__main__":
    unittest.main()
