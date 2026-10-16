"""Offline integration of OEPL build-only/inspect shell gates, with fake tools.

Never accesses a USB device, compiles real firmware, or posts to GitHub.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "run_on_ubuntu.sh"


class BuildGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.source = self.home / "firmware"
        s3 = self.source / "ESP32_AP-Flasher"
        s3.mkdir(parents=True)
        (s3 / "platformio.ini").write_text(
            "[env:ESP32_S3_C6_BIG_AP]\nbuild_flags =\n"
            "-D FLASHER_AP_TXD=13\n-D FLASHER_AP_RXD=14\n"
            "-D FLASHER_AP_RESET=47\n",
            encoding="utf-8",
        )
        c6 = self.source / "ARM_Tag_FW" / "OpenEPaperLink_esp32_C6_AP"
        c6.mkdir(parents=True)
        (c6 / "sdkconfig").write_text(
            "CONFIG_OEPL_HARDWARE_UART_RX=2\n"
            "CONFIG_OEPL_HARDWARE_UART_TX=3\n",
            encoding="utf-8",
        )
        self.fake = self.home / "fake-bin"
        self.fake.mkdir()
        self.make_tool(
            "gh",
            "#!/bin/sh\n"
            "if [ \"$1\" = auth ]; then exit 0; fi\n"
            "if [ \"$1\" = issue ]; then exit 0; fi\n"
            "exit 1\n",
        )
        self.make_tool("pio", "#!/bin/sh\nexit 0\n")
        self.make_tool("idf.py", "#!/bin/sh\nexit 0\n")
        self.env = {
            **os.environ,
            "PATH": str(self.fake) + os.pathsep + os.environ.get("PATH", ""),
            "OEPL_SOURCE": str(self.source),
            "PAC_CODEX_LOOP_DATA": str(self.home / "runtime"),
        }
        self.state_dir = self.home / "runtime" / "oepl-s3-c6"

    def make_tool(self, name, source):
        file = self.fake / name
        file.write_text(source, encoding="utf-8")
        file.chmod(0o755)

    def invoke(self, mode):
        return subprocess.run(
            ["bash", str(RUN), mode],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=35,
            check=False,
        )

    def test_both_builds_pass_but_no_uart_claim(self):
        result = self.invoke("--build-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        details = json.loads(
            (self.state_dir / "build-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(details, {"s3_build": "PASS", "c6_build": "PASS"})
        inventory = json.loads(
            (self.state_dir / "inventory.json").read_text(encoding="utf-8")
        )
        self.assertEqual(inventory["status"], "READY_FOR_HARDWARE_CHECK")
        self.assertNotIn("UART_PASS", result.stdout)

    def test_one_failed_build_fails_gate_and_records_result(self):
        self.make_tool("pio", "#!/bin/sh\nexit 9\n")
        result = self.invoke("--build-only")
        self.assertEqual(result.returncode, 1, result.stderr)
        details = json.loads(
            (self.state_dir / "build-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(details, {"s3_build": "FAIL", "c6_build": "PASS"})
        self.assertIn("Build gate incomplete", result.stderr)

    def test_inspect_does_not_reuse_previous_build_result(self):
        self.assertEqual(self.invoke("--build-only").returncode, 0)
        inspect = self.invoke("--inspect")
        self.assertEqual(inspect.returncode, 0)
        summary = (self.state_dir / "github-summary.md").read_text(encoding="utf-8")
        self.assertIn("Build outcomes: not requested", summary)
        self.assertNotIn("'s3_build': 'PASS'", summary)

    def test_pin_mismatch_prevents_any_compilation(self):
        ini = self.source / "ESP32_AP-Flasher" / "platformio.ini"
        ini.write_text(
            ini.read_text(encoding="utf-8").replace(
                "FLASHER_AP_TXD=13", "FLASHER_AP_TXD=17"
            ),
            encoding="utf-8",
        )
        result = self.invoke("--build-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Firmware GPIO flags mismatch", result.stderr)
        self.assertFalse((self.state_dir / "build-summary.json").exists())


if __name__ == "__main__":
    unittest.main()
