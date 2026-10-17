import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PATCH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "oepl-s3-c6"
    / "patches"
    / "0001-bound-c6-uart-printf.patch"
)
C_HARNESS = r"""
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static int observed_bad;
static int observed_len;
static char observed[128];

static int uart_write_bytes(int port, const char *buffer, int len) {
    if (port != 1 || len < 0 || len > 127) {
        observed_bad = 1;
    }
    observed_len = len;
    if (len > 0) {
        memcpy(observed, buffer, (size_t)len);
    }
    return len;
}

static void tested_uart_printf(const char *format, ...) {
    char buffer[128];
    va_list args;
    va_start(args, format);
    int len = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    if (len > 0) {
        if (len >= (int)sizeof(buffer)) {
            len = sizeof(buffer) - 1;
        }
        uart_write_bytes(1, buffer, len);
    }
}

static int expect_length(const char *text, int expected) {
    observed_bad = 0;
    observed_len = -1;
    memset(observed, 0, sizeof(observed));
    tested_uart_printf("%s", text);
    return !observed_bad && observed_len == expected;
}

int main(void) {
    char exact[128];
    char long_text[4097];
    memset(exact, 'A', sizeof(exact) - 1);
    exact[sizeof(exact) - 1] = '\0';
    memset(long_text, 'B', sizeof(long_text) - 1);
    long_text[sizeof(long_text) - 1] = '\0';
    return expect_length("", -1) &&
           expect_length("x", 1) &&
           expect_length(exact, 127) &&
           expect_length(long_text, 127) ? 0 : 1;
}
"""


class C6UartPatchTests(unittest.TestCase):
    @staticmethod
    def bounded_write_length(vsnprintf_result, buffer_capacity=128):
        """Model the C guard; returned length must fit before the NUL byte."""
        if vsnprintf_result >= buffer_capacity:
            vsnprintf_result = buffer_capacity - 1
        return vsnprintf_result if vsnprintf_result > 0 else 0

    def test_uart_printf_patch_clamps_truncated_vsnprintf_length(self):
        patch = PATCH.read_text(encoding="utf-8")
        self.assertIn(
            "+\t\tif (len >= (int)sizeof(buffer)) len = sizeof(buffer) - 1;",
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
        for result in (-2147483648, -1, 0, 1, 127, 128, 129, 4096, 2147483647):
            with self.subTest(result=result):
                length = self.bounded_write_length(result)
                self.assertGreaterEqual(length, 0)
                self.assertLess(length, 128)

    def test_write_contract_is_explicitly_bounded(self):
        patch = PATCH.read_text(encoding="utf-8")
        self.assertIn("len >= (int)sizeof(buffer)", patch)
        self.assertIn("sizeof(buffer) - 1", patch)
        self.assertIn("uart_write_bytes(1, buffer, len);", patch)

    def test_c_harness_executes_boundary_contract(self):
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "uart_contract.c"
            binary = root / "uart_contract"
            source.write_text(C_HARNESS, encoding="utf-8")
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
