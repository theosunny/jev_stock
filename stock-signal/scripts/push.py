# -*- coding: utf-8 -*-
"""飞书推送：lark-cli im +messages-send，bot身份优先、user身份兜底，整体重试1次"""
import json, os, shutil, subprocess, time

_BASE = os.path.dirname(os.path.abspath(__file__))
import datadir

ENV = {}

def _load_env():
    if ENV:
        return
    for line in open(os.path.join(datadir.data_dir(), ".env"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

def _lark_bin():
    return shutil.which("lark-cli") or "/Users/habitat/.hermes/node/bin/lark-cli"

def _ok_output(out):
    try:
        return bool(json.loads(out).get("ok"))
    except Exception:
        return '"ok": true' in out or '"ok":true' in out

def send_markdown(text):
    """发送到.env中LARK_USER_OPEN_ID对应的飞书私聊。返回(ok, 使用的身份或错误信息)"""
    _load_env()
    uid = ENV.get("LARK_USER_OPEN_ID", "")
    if not uid:
        return False, "LARK_USER_OPEN_ID 未配置(.env)"
    env = dict(os.environ,
               LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1",
               LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
    last = ""
    for attempt in range(2):
        for ident in ("bot", "user"):
            cmd = [_lark_bin(), "im", "+messages-send", "--as", ident,
                   "--user-id", uid, "--markdown", text]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=60, env=env)
            except Exception as e:
                last = str(e)
                continue
            out = (p.stdout or "").strip() or (p.stderr or "").strip()
            if _ok_output(out):
                return True, ident
            last = out[:300]
        if attempt == 0:
            time.sleep(3)
    return False, last
