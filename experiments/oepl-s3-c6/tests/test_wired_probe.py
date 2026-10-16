"""No-touch wired probe: USB symlink and TCP fixtures only, no serial open."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_ap_sysinfo_read_only_exposes_radio_fields_not_secrets(self):
        import json

        class Reply:
            status = 200

            def read(self, _):
                return json.dumps({
                    "env": "ESP32_S3_C6_BIG_AP",
                    "ap_version": 33,
                    "ap_type": 198,
                    "hasC6": 1,
                    "flashpin_defaults": {"txd": 13, "rxd": 14, "reset": 47},
                    "alias": "private-room",
                    "ntpserver": "private.example.invalid",
                }).encode()

        class Connection:
            called = None

            def request(self, method, path, headers):
                self.called = (method, path, headers)

            def getresponse(self):
                return Reply()

            def close(self):
                pass

        conn = Connection()
        with patch.object(
            wired_probe.http.client, "HTTPConnection", return_value=conn
        ):
            result = wired_probe.http_sysinfo("192.168.2.192", 80)
        self.assertEqual(result["status"], "HTTP_INFO_OBSERVED")
        self.assertEqual(result["fields"]["ap_version"], 33)
        self.assertEqual(result["fields"]["flashpin_defaults"]["txd"], 13)
        self.assertEqual(conn.called[0:2], ("GET", "/sysinfo"))
        self.assertNotIn("private-room", str(result))
        self.assertNotIn("private.example.invalid", str(result))
        self.assertNotIn("UART_PASS", str(result))

    def test_ap_info_refuses_public_targets_and_bad_json(self):
        self.assertEqual(
            wired_probe.http_sysinfo("8.8.8.8", 80)["status"], "INVALID_TARGET"
        )
        self.assertEqual(
            wired_probe.http_sysinfo(None, 80)["status"], "NOT_REQUESTED"
        )

        class BadReply:
            status = 200

            def read(self, _):
                return b"broken json"

        class BadConn:
            def request(self, *_args, **_kwargs):
                pass

            def getresponse(self):
                return BadReply()

            def close(self):
                pass

        with patch.object(
            wired_probe.http.client, "HTTPConnection", return_value=BadConn()
        ):
            result = wired_probe.http_sysinfo("192.168.2.192", 80)
            self.assertEqual(result["status"], "HTTP_INVALID_JSON")
            self.assertEqual(result["code"], 200)
            self.assertEqual(result["bytes"], len(b"broken json"))
            self.assertNotIn("broken json", str(result))

    def test_ap_http_non_200_carries_only_status_code(self):
        class Reply:
            status = 404

        class Connection:
            def request(self, *_args, **_kwargs):
                pass

            def getresponse(self):
                return Reply()

            def close(self):
                pass

        with patch.object(
            wired_probe.http.client, "HTTPConnection", return_value=Connection()
        ):
            self.assertEqual(
                wired_probe.http_sysinfo("192.168.2.192", 80),
                {"status": "HTTP_NOT_AVAILABLE", "code": 404},
            )

    def test_ap_http_timeout_and_disconnect_are_distinguished(self):
        class Connection:
            def __init__(self, error):
                self.error = error

            def request(self, *_args, **_kwargs):
                raise self.error

            def close(self):
                pass

        for error, expected in (
            (TimeoutError("secret timeout"), "HTTP_TIMEOUT"),
            (wired_probe.http.client.RemoteDisconnected("secret"), "HTTP_CONNECTION_CLOSED"),
            (ConnectionRefusedError("secret"), "HTTP_TRANSPORT_ERROR"),
        ):
            with self.subTest(expected=expected):
                with patch.object(
                    wired_probe.http.client, "HTTPConnection", return_value=Connection(error)
                ):
                    outcome = wired_probe.http_sysinfo("192.168.2.192", 80)
                self.assertEqual(outcome, {"status": expected})
                self.assertNotIn("secret", str(outcome))

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
