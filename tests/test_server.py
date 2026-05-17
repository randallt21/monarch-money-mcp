from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import server


class ServerLaunchGuardTests(unittest.TestCase):
    def test_interactive_stdio_detection_uses_stdin_or_stdout(self) -> None:
        with patch("sys.stdin.isatty", return_value=False), patch("sys.stdout.isatty", return_value=True):
            self.assertTrue(server._interactive_stdio_detected())

    def test_interactive_override_env_allows_manual_launch(self) -> None:
        with patch.dict(os.environ, {"MONARCH_ALLOW_INTERACTIVE_SERVER": "true"}, clear=False):
            self.assertTrue(server._allow_interactive_stdio())


if __name__ == "__main__":
    unittest.main()
