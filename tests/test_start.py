"""Starting HomeMesh the way a desktop starts it."""

import subprocess
import sys
import unittest

from support import ROOT


class ClosedStreamsTest(unittest.TestCase):
    def test_starts_with_standard_streams_closed(self):
        # LXQt starts its autostart programs, "homemesh login" among them,
        # with standard input closed.
        for closed in ("<&-", "<&- >&-"):
            with self.subTest(closed=closed):
                result = subprocess.run(
                    ["sh", "-c", f'exec "$0" "$1" --help {closed}', sys.executable, str(ROOT / "bin" / "homemesh")],
                    stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
