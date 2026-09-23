#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal Slack Bot transport for the Codex stock-monitor outbox.

The transport deliberately performs no retries.  A timeout can mean that Slack
accepted the message, so callers must use :func:`verify` before releasing an
uncertain outbox claim.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import push


API_ROOT = "https://slack.com/api/"
TIMEOUT_SECONDS = 20
MAX_MESSAGE_CHARS = 40_000
_USER_ID = re.compile(r"[UW][A-Z0-9]+\Z")
_CHANNEL_ID = re.compile(r"D[A-Z0-9]+\Z")
_TIMESTAMP = re.compile(r"\d{10}\.\d+\Z")
_METHODS = {"auth.test", "conversations.open", "conversations.history", "chat.postMessage"}


def config():
    """Return validated bot credentials, or raise a safe configuration error."""
    push._load_env()
    token = push.ENV.get("SLACK_BOT_TOKEN", "")
    user_id = push.ENV.get("SLACK_USER_ID", "")
    if not (isinstance(token, str) and token.startswith("xoxb-")
            and isinstance(user_id, str) and _USER_ID.fullmatch(user_id)):
        raise ValueError("slack_bot_not_configured")
    return {"token": token, "user_id": user_id}


def _failure(error):
    return {"ok": False, "error": error}


def api(method, payload):
    """Make one Slack Web API request and never disclose remote error text."""
    if method not in _METHODS or not isinstance(payload, dict):
        return _failure("slack_request_invalid")
    credentials = config()
    request = urllib.request.Request(
        API_ROOT + method,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + credentials["token"], "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, ValueError, UnicodeDecodeError):
        return _failure("slack_transport_error")
    if not isinstance(result, dict) or result.get("ok") is not True:
        return _failure("slack_api_error")
    return result


def _bot_user_id():
    result = api("auth.test", {})
    user_id = result.get("user_id") if result.get("ok") is True else None
    bot_id = result.get("bot_id") if result.get("ok") is True else None
    if (not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id)
            or not isinstance(bot_id, str) or not bot_id.strip()):
        return None
    return user_id


def open_dm():
    """Open this bot's distinct DM with the configured user and return its D ID."""
    credentials = config()
    result = api("conversations.open", {"users": credentials["user_id"]})
    channel = result.get("channel") if result.get("ok") is True else None
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    if not isinstance(channel_id, str) or not _CHANNEL_ID.fullmatch(channel_id):
        raise ValueError("slack_open_dm_failed")
    return channel_id


def send_once(text, channel=None):
    """Authenticate then send exactly one bot message; never retry or fall back."""
    if not isinstance(text, str) or len(text) > MAX_MESSAGE_CHARS:
        return {"ok": False, "channel": None, "ts": None, "error": "message_too_long"}
    credentials = config()  # Preserve a distinct configuration failure for the outbox runner.
    # Always mention the configured user so mobile "mentions" prefs can fire.
    mention = "<@" + credentials["user_id"] + ">"
    if mention not in text:
        text = mention + "\n" + text
    if len(text) > MAX_MESSAGE_CHARS:
        return {"ok": False, "channel": None, "ts": None, "error": "message_too_long"}
    if not _bot_user_id():
        return {"ok": False, "channel": None, "ts": None, "error": "slack_auth_failed"}
    try:
        channel = channel or open_dm()
    except ValueError:
        return {"ok": False, "channel": None, "ts": None, "error": "slack_open_dm_failed"}
    if not isinstance(channel, str) or not _CHANNEL_ID.fullmatch(channel):
        return {"ok": False, "channel": None, "ts": None, "error": "slack_channel_invalid"}
    result = api("chat.postMessage", {
        "channel": channel, "text": text, "link_names": True,
        "unfurl_links": False, "unfurl_media": False,
    })
    timestamp = result.get("ts") if result.get("ok") is True else None
    response_channel = result.get("channel") if result.get("ok") is True else None
    if (not isinstance(timestamp, str) or not _TIMESTAMP.fullmatch(timestamp)
            or response_channel != channel):
        return {"ok": False, "channel": None, "ts": None, "error": "slack_send_uncertain"}
    return {"ok": True, "channel": response_channel, "ts": timestamp, "error": None}


def verify(report_id, channel, oldest):
    """Find a report in bot-authored DM history, bounded to five 100-item pages."""
    config()
    if not (isinstance(report_id, str) and report_id and isinstance(channel, str)
            and _CHANNEL_ID.fullmatch(channel) and isinstance(oldest, str) and oldest):
        return {"status": "uncertain", "error": "verify_request_invalid"}
    bot_user_id = _bot_user_id()
    if not bot_user_id:
        return {"status": "uncertain", "error": "slack_auth_failed"}
    cursor = ""
    report_pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(report_id) + r"(?![A-Za-z0-9_-])")
    for _page in range(5):
        payload = {"channel": channel, "oldest": oldest, "limit": 100}
        if cursor:
            payload["cursor"] = cursor
        result = api("conversations.history", payload)
        messages = result.get("messages") if result.get("ok") is True else None
        if not isinstance(messages, list):
            return {"status": "uncertain", "error": "history_unavailable"}
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("user") != bot_user_id or not report_pattern.search(str(message.get("text", ""))):
                continue
            timestamp = message.get("ts")
            if isinstance(timestamp, str) and _TIMESTAMP.fullmatch(timestamp):
                return {"status": "verified", "receipt": timestamp}
            return {"status": "uncertain", "error": "history_receipt_invalid"}
        metadata = result.get("response_metadata")
        next_cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
        if not result.get("has_more") and not next_cursor:
            return {"status": "absent"}
        if not isinstance(next_cursor, str) or not next_cursor:
            return {"status": "uncertain", "error": "history_incomplete"}
        cursor = next_cursor
    return {"status": "uncertain", "error": "history_incomplete"}
