"""No-touch wired probe: USB symlink and TCP fixtures only, no serial open."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wired_probe


class TTYTests(unittest.TestCase):
    def test_only_enumerates_symlinks_without_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "ttyACM0"
            target.write_text("fixture")
            (root / "usb-Espressif_One-if00").symlink_to(target)
            (root / "not-a-device").write_text("fixture")
            with patch.object(Path, "open", side_effect=AssertionError("TTY open")):
                devices = wired_probe.tty_inventory(root)
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0]["tty"], "ttyACM0")
            self.assertTrue(devices[0]["reachable"])

    def test_missing_devices_is_not_uart_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(wired_probe, "report", return_value={
                "status": "NEEDS_READ_ONLY_INSPECTION",
                "s3": {"flags": {"FLASHER_AP_TXD": "13"}},
                "c6": {"flags": {}},
            }):
                state = wired_probe.snapshot(
                    root, None, serial_dir=root / "nonexistent-serial")
            self.assertEqual(state["usb_serial_ports"], [])
            self.assertEqual(state["uart_handshake"], "NOT_TESTED")
            self.assertEqual(state["c6_radio"], "NOT_TESTED")
            self.assertEqual(state["tcp"]["status"], "NOT_REQUESTED")

    def test_tcp_socket_never_implies_uart_success(self):
        class Socket:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        with patch.object(
            wired_probe.socket, "create_connection", return_value=Socket()
        ) as mocked:
            state = wired_probe.tcp_probe("192.168.2.192", 80)
        self.assertEqual(state["status"], "TCP_REACHABLE")
        mocked.assert_called_once_with(("192.168.2.192", 80), timeout=2.0)
        self.assertEqual(wired_probe.tcp_probe("bad-host", 80)["status"],
                         "INVALID_TARGET")


if __name__ == "__main__":
    unittest.main()
