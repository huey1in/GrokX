"""Web 注册流程顺序测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from registration.flow import ProtocolRegistrationConfig, ProtocolRegistrationFlow


class _Client:
    def __init__(self):
        self.calls = []

    def bootstrap(self, page_url):
        self.calls.append("bootstrap")

    def create_email_validation_code(self, email, *, castle_request_token):
        self.calls.append("create_email_code")

    def verify_email_validation_code(self, email, code, **kwargs):
        self.calls.append("verify_email_code")

    def create_user_and_session(self, **kwargs):
        self.calls.append("create_user")
        return SimpleNamespace(
            response=SimpleNamespace(cookies={"sso": "sso-secret"}),
            messages=[],
        )


class _Mail:
    def create(self):
        return "test@example.com", "mail-token"

    def wait_code(self, token, email):
        return "123456"


class _AntiAbuse:
    def acquire(self, *, stage, email):
        return f"castle-{stage}"


class _HumanVerification:
    def acquire(self, challenge):
        return "turnstile-token"


class RegistrationFlowTest(unittest.TestCase):
    @staticmethod
    def _make_flow(client):
        return ProtocolRegistrationFlow(
            config=ProtocolRegistrationConfig(
                page_url="https://accounts.x.ai/sign-up",
                sitekey="site-key",
            ),
            client=client,
            mail=_Mail(),
            anti_abuse=_AntiAbuse(),
            human_verification=_HumanVerification(),
        )

    def test_verifies_email_code_before_creating_user(self):
        client = _Client()
        flow = self._make_flow(client)

        result = flow.run()

        self.assertTrue(result.success)
        self.assertIn("verify_email_code", client.calls)
        self.assertLess(
            client.calls.index("verify_email_code"),
            client.calls.index("create_user"),
        )

    def test_reports_email_code_verified_before_human_verification(self):
        client = _Client()
        flow = self._make_flow(client)

        result = flow.run()

        self.assertIn("email_code_verified", result.history)
        self.assertLess(
            result.history.index("email_code_verified"),
            result.history.index("turnstile_token_ready"),
        )


if __name__ == "__main__":
    unittest.main()
