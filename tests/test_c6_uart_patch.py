import unittest
from pathlib import Path


PATCH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "oepl-s3-c6"
    / "patches"
    / "0001-bound-c6-uart-printf.patch"
)
SOURCE = (
    Path(__file__).resolve().parents[1].parent
    / "OpenEPaperLink-git"
    / "ARM_Tag_FW"
    / "OpenEPaperLink_esp32_C6_AP"
    / "main"
    / "second_uart.c"
)


class C6UartPatchTests(unittest.TestCase):
    @staticmethod
    def bounded_write_length(vsnprintf_result, buffer_capacity=128):
        """Model the C guard; returned length must fit before the NUL byte."""
        if vsnprintf_result >= buffer_capacity:
            vsnprintf_result = buffer_capacity - 1
        return vsnprintf_result if vsnprintf_result > 0 else 0

    def test_uart_printf_patch_clamps_truncated_vsnprintf_length(self):
        patch = PATCH.read_text(encoding="utf-8")
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("char buffer[128];", source)
        self.assertIn(
            "+\tif (len >= (int)sizeof(buffer)) len = sizeof(buffer) - 1;",
            patch,
        )
        self.assertIn("+\tif (len > 0)", patch)
        self.assertIn("\t\tuart_write_bytes(1, buffer, len);", patch)

    def test_vsnprintf_result_boundaries(self):
        expected = {
            -1: 0,    # encoding/formatting failure
            0: 0,     # empty output
            1: 1,
            127: 127, # largest payload in char buffer[128]
            128: 127, # truncated: vsnprintf returns required length
            129: 127,
            4096: 127,
        }
        for result, wanted in expected.items():
            with self.subTest(result=result):
                self.assertEqual(self.bounded_write_length(result), wanted)

    def test_uart_write_length_is_nonnegative_and_in_bounds(self):
        for result in (-1, 0, 1, 127, 128, 129, 4096):
            with self.subTest(result=result):
                length = self.bounded_write_length(result)
                self.assertGreaterEqual(length, 0)
                self.assertLess(length, 128)


if __name__ == "__main__":
    unittest.main()
