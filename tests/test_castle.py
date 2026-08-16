"""Castle 官方 SDK 运行时兼容性测试。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from providers.castle import CastleSdkTokenProvider


class CastleSdkTokenProviderTest(unittest.TestCase):
    def _provider(self):
        return CastleSdkTokenProvider(
            "pk_test",
            "https://accounts.x.ai/sign-up",
            "Mozilla/5.0",
        )

    def test_uses_nodejs_when_node_is_not_available(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"tokens":["castle-token-1","castle-token-2"]}\n',
            stderr="",
        )

        def find_runtime(name):
            return "/usr/bin/nodejs" if name == "nodejs" else None

        with patch("shutil.which", side_effect=find_runtime), patch(
            "providers.castle.subprocess.run",
            return_value=completed,
        ) as run:
            token = self._provider().acquire(stage="email", email="test@example.com")

        self.assertEqual("castle-token-1", token)
        self.assertEqual("/usr/bin/nodejs", run.call_args.args[0][0])

    def test_missing_node_runtime_has_actionable_error(self):
        with patch("shutil.which", return_value=None), patch(
            "providers.castle.subprocess.run",
            side_effect=FileNotFoundError(2, "No such file or directory", "node"),
        ):
            try:
                self._provider().acquire(stage="email", email="test@example.com")
            except Exception as exc:
                self.assertIsInstance(exc, RuntimeError)
                self.assertIn("Node.js 22", str(exc))
            else:
                self.fail("缺少 Node.js 时应明确报错")


if __name__ == "__main__":
    unittest.main()
