"""Offline tests for S3/C6 preflight: never open a device or run a flash tool."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probe


class OEPLProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "ESP32_AP-Flasher").mkdir()
        self.c6 = self.root / "ARM_Tag_FW" / "OpenEPaperLink_esp32_C6_AP"
        self.c6.mkdir(parents=True)
        self.ini = self.root / "ESP32_AP-Flasher" / "platformio.ini"
        self.ini.write_text(
            "[env]\n-D WHATEVER=1\n"
            "[env:WRONG_AP]\n-D FLASHER_AP_TXD=99\n"
            "[env:ESP32_S3_C6_BIG_AP]\n"
            "build_flags =\n-D FLASHER_AP_TXD=13\n"
            "-D FLASHER_AP_RXD=14\n-D FLASHER_AP_RESET=47\n"
            "[env:OTHER_AP]\n-D FLASHER_AP_TXD=2\n",
            encoding="utf-8",
        )
        self.sdk_defaults = self.c6 / "sdkconfig.defaults"
        self.sdk_active = self.c6 / "sdkconfig"
        self.sdk_defaults.write_text(
            "CONFIG_OEPL_HARDWARE_UART_RX=2\n"
            "CONFIG_OEPL_HARDWARE_UART_TX=3\n", encoding="utf-8",
        )
        self.sdk_active.write_text(
            "CONFIG_OEPL_HARDWARE_UART_RX=2\n"
            "CONFIG_OEPL_HARDWARE_UART_TX=3\n"
            "CONFIG_WIFI_PASSWORD=\"secret-never-print\"\n", encoding="utf-8",
        )

    def test_pin_mapping_detected_in_correct_environment(self):
        self.assertEqual(probe.s3_config(self.root)["status"], "PASS")
        self.assertEqual(probe.c6_config(self.root)["status"], "PASS")

    def test_uart_mismatch_blocks_hw_claim(self):
        self.ini.write_text(
            self.ini.read_text(encoding="utf-8").replace(
                "FLASHER_AP_RXD=14", "FLASHER_AP_RXD=33"
            ), encoding="utf-8",
        )
        self.assertEqual(probe.s3_config(self.root)["status"], "MISMATCH")
        with patch.object(probe, "usb_ports", return_value=[]):
            status = probe.report(self.root)
        self.assertEqual(status["status"], "CONFIG_MISMATCH")

    def test_missing_c6_config_is_unknown_not_success(self):
        self.sdk_active.unlink()
        self.assertEqual(probe.c6_config(self.root)["status"], "UNKNOWN")
        with patch.object(probe, "usb_ports", return_value=[]):
            self.assertEqual(probe.report(self.root)["status"], "NEEDS_READ_ONLY_INSPECTION")

    def test_sdkconfig_defaults_alone_never_passes(self):
        self.sdk_active.unlink()
        self.assertEqual(probe.c6_config(self.root)["status"], "UNKNOWN")

    def test_active_settings_override_defaults_and_fail_mismatch(self):
        self.sdk_active.write_text(
            "CONFIG_OEPL_HARDWARE_UART_RX=9\n"
            "CONFIG_OEPL_HARDWARE_UART_TX=3\n",
            encoding="utf-8",
        )
        self.assertEqual(probe.c6_config(self.root)["status"], "MISMATCH")
        with patch.object(probe, "usb_ports", return_value=[]):
            self.assertEqual(probe.report(self.root)["status"], "CONFIG_MISMATCH")

    def test_duplicate_active_uart_flag_fails_closed(self):
        with self.sdk_active.open("a", encoding="utf-8") as handle:
            handle.write("CONFIG_OEPL_HARDWARE_UART_RX=2\n")
        self.assertEqual(probe.c6_config(self.root)["status"], "MISMATCH")

    def test_source_missing_fail_closed(self):
        self.assertEqual(probe.report(self.root / "missing")["status"], "SOURCE_MISSING")

    def test_never_expose_wifi_password_or_touch_usb(self):
        with patch.object(probe, "usb_ports", return_value=["usb-Device-One"]):
            details = probe.report(self.root)
        self.assertNotIn("secret-never-print", str(details))
        self.assertEqual(details["safe_boundary"]["can_auto_flash"], False)
        self.assertEqual(details["usb_serial_ids"], ["usb-Device-One"])


if __name__ == "__main__":
    unittest.main()
