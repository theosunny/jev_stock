import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stock-signal/scripts"))
import push
import slack_bot


CONFIG = {"token": "xoxb-test-token", "user_id": "U0C3G6VMADU"}
ENV_CONFIG = {"SLACK_BOT_TOKEN": "xoxb-test-token", "SLACK_USER_ID": "U0C3G6VMADU"}


class SlackBotTests(unittest.TestCase):
    def setUp(self):
        push.ENV.clear()

    def tearDown(self):
        push.ENV.clear()

    def test_config_accepts_bot_token_and_member_id(self):
        with patch.object(push, "_load_env"), patch.dict(push.ENV, ENV_CONFIG, clear=True):
            self.assertEqual(slack_bot.config(), CONFIG)

    def test_config_rejects_missing_or_non_bot_credentials(self):
        for values in ({}, {"SLACK_BOT_TOKEN": "xoxp-user", "SLACK_USER_ID": "U123"},
                       {"SLACK_BOT_TOKEN": "xoxb-ok", "SLACK_USER_ID": "bad"}):
            with self.subTest(values=values), patch.object(push, "_load_env"), patch.dict(push.ENV, values, clear=True):
                with self.assertRaisesRegex(ValueError, "slack_bot_not_configured"):
                    slack_bot.config()

    def test_api_uses_only_slack_api_and_never_surfaces_response_body(self):
        response = {"ok": False, "error": "token-should-not-appear-in-errors"}
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(
            slack_bot.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())
        ) as request:
            result = slack_bot.api("auth.test", {})
        self.assertEqual(result, {"ok": False, "error": "slack_api_error"})
        call = request.call_args.args[0]
        self.assertEqual(call.full_url, "https://slack.com/api/auth.test")
        self.assertEqual(call.get_header("Authorization"), "Bearer xoxb-test-token")
        self.assertEqual(request.call_args.kwargs["timeout"], 20)

    def test_send_once_authenticates_then_opens_and_posts_once(self):
        replies = iter((
            {"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"},
            {"ok": True, "channel": {"id": "D01234567"}},
            {"ok": True, "channel": "D01234567", "ts": "1790065019.555459"},
        ))
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(
            slack_bot, "api", side_effect=lambda *_args: next(replies)
        ) as api:
            result = slack_bot.send_once("report REPORT_ID=20260922-test")
        self.assertEqual(result, {"ok": True, "channel": "D01234567", "ts": "1790065019.555459", "error": None})
        self.assertEqual([call.args[0] for call in api.call_args_list], ["auth.test", "conversations.open", "chat.postMessage"])
        self.assertEqual(api.call_args_list[-1].args[1]["unfurl_links"], False)
        self.assertEqual(api.call_args_list[-1].args[1]["unfurl_media"], False)

    def test_send_once_rejects_oversize_body_without_network(self):
        with patch.object(slack_bot, "api") as api:
            result = slack_bot.send_once("x" * 40001, "D01234567")
        self.assertEqual(result["error"], "message_too_long")
        api.assert_not_called()

    def test_send_once_does_not_post_when_auth_or_open_fails(self):
        for replies in (({"ok": False, "error": "slack_api_error"},),
                        ({"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"}, {"ok": False, "error": "slack_api_error"})):
            with self.subTest(replies=replies), patch.object(slack_bot, "config", return_value=CONFIG), patch.object(
                slack_bot, "api", side_effect=replies
            ) as api:
                result = slack_bot.send_once("report")
            self.assertFalse(result["ok"])
            self.assertNotIn("chat.postMessage", [call.args[0] for call in api.call_args_list])

    def test_send_once_rejects_authentication_without_a_bot_identity(self):
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(
            slack_bot, "api", return_value={"ok": True, "user_id": "U0BOT"}
        ) as api:
            result = slack_bot.send_once("report", "D01234567")
        self.assertEqual(result["error"], "slack_auth_failed")
        self.assertEqual([call.args[0] for call in api.call_args_list], ["auth.test"])

    def test_send_once_requires_requested_channel_and_valid_timestamp(self):
        for reply in (
            {"ok": True, "channel": "D07654321", "ts": "1790065019.555459"},
            {"ok": True, "channel": "D01234567", "ts": "not-a-timestamp"},
        ):
            with self.subTest(reply=reply), patch.object(slack_bot, "config", return_value=CONFIG), patch.object(
                slack_bot, "api", side_effect=(
                    {"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"}, reply
                )
            ) as api:
                result = slack_bot.send_once("report", "D01234567")
            self.assertEqual(result["error"], "slack_send_uncertain")
            self.assertEqual([call.args[0] for call in api.call_args_list], ["auth.test", "chat.postMessage"])

    def test_verify_requires_the_bot_message_and_returns_receipt(self):
        replies = iter((
            {"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"},
            {"ok": True, "messages": [
                {"user": "U_OTHER", "text": "REPORT_ID=20260922-x", "ts": "1790065019.000001"},
                {"user": "U0BOT", "text": "REPORT_ID=20260922-x", "ts": "1790065019.000002"},
            ], "has_more": False, "response_metadata": {"next_cursor": ""}},
        ))
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(
            slack_bot, "api", side_effect=lambda *_args: next(replies)
        ):
            result = slack_bot.verify("REPORT_ID=20260922-x", "D01234567", "1.0")
        self.assertEqual(result, {"status": "verified", "receipt": "1790065019.000002"})

    def test_verify_uses_complete_report_id_boundary_and_valid_timestamp(self):
        replies = iter((
            {"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"},
            {"ok": True, "messages": [
                {"user": "U0BOT", "text": "REPORT_ID=20260922-x_extra", "ts": "1790065019.000001"},
                {"user": "U0BOT", "text": "REPORT_ID=20260922-x", "ts": "1790065019.000002"},
            ], "has_more": False, "response_metadata": {"next_cursor": ""}},
        ))
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(slack_bot, "api", side_effect=lambda *_: next(replies)):
            self.assertEqual(slack_bot.verify("REPORT_ID=20260922-x", "D01234567", "1.0"), {"status": "verified", "receipt": "1790065019.000002"})

        invalid_timestamp = iter((
            {"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"},
            {"ok": True, "messages": [{"user": "U0BOT", "text": "REPORT_ID=x", "ts": "bad"}], "has_more": False},
        ))
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(slack_bot, "api", side_effect=lambda *_: next(invalid_timestamp)):
            self.assertEqual(slack_bot.verify("REPORT_ID=x", "D01234567", "1.0"), {"status": "uncertain", "error": "history_receipt_invalid"})

    def test_verify_absent_only_after_complete_history_and_uncertain_on_page_cap(self):
        absent = iter((
            {"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"},
            {"ok": True, "messages": [], "has_more": False, "response_metadata": {"next_cursor": ""}},
        ))
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(slack_bot, "api", side_effect=lambda *_: next(absent)):
            self.assertEqual(slack_bot.verify("REPORT_ID=x", "D01234567", "1.0"), {"status": "absent"})

        capped = [{"ok": True, "user_id": "U0BOT", "bot_id": "B0BOT"}] + [
            {"ok": True, "messages": [], "has_more": True, "response_metadata": {"next_cursor": "more"}}
            for _ in range(5)
        ]
        with patch.object(slack_bot, "config", return_value=CONFIG), patch.object(slack_bot, "api", side_effect=capped):
            self.assertEqual(slack_bot.verify("REPORT_ID=x", "D01234567", "1.0"), {"status": "uncertain", "error": "history_incomplete"})


if __name__ == "__main__":
    unittest.main()
