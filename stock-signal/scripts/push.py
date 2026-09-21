# -*- coding: utf-8 -*-
"""多通道推送：飞书(lark-cli) + 微信系四通道(Server酱/PushPlus/企业微信webhook/WxPusher)
.env 里填了哪个通道的配置就启用哪个，可多通道同时收；任一通道成功即算成功。"""
import json, os, shutil, subprocess, time, urllib.parse, urllib.request

import datadir

ENV = {}

def _load_env():
    if ENV:
        return
    try:
        env_file = open(os.path.join(datadir.data_dir(), ".env"), encoding="utf-8")
    except FileNotFoundError:
        return  # 无.env(未配置推送)，各通道返回None自动跳过
    for line in env_file:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

def _lark_bin():
    return shutil.which("lark-cli") or "/Users/habitat/.hermes/node/bin/lark-cli"

def _post(url, data, timeout=15):
    is_json = isinstance(data, dict)
    body = json.dumps(data).encode("utf-8") if is_json else urllib.parse.urlencode(data).encode("utf-8")
    headers = {"Content-Type": "application/json" if is_json else "application/x-www-form-urlencoded"}
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

def _feishu(text):
    uid = ENV.get("LARK_USER_OPEN_ID", "")
    if not uid:
        return None
    env = dict(os.environ,
               LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1",
               LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
    # cron 最小PATH下 env node 找不到node导致lark-cli执行失败 —— 注入lark-cli所在bin目录
    bindir = os.path.dirname(_lark_bin())
    env["PATH"] = bindir + ":/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    for ident in ("bot", "user"):
        cmd = [_lark_bin(), "im", "+messages-send", "--as", ident,
               "--user-id", uid, "--markdown", text]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        except Exception:
            continue
        out = (p.stdout or "").strip() or (p.stderr or "").strip()
        try:
            if json.loads(out).get("ok"):
                return True, "feishu(%s)" % ident
        except Exception:
            if '"ok": true' in out or '"ok":true' in out:
                return True, "feishu(%s)" % ident
    return False, "feishu失败"

def _serverchan(text):
    key = ENV.get("SERVERCHAN_SENDKEY", "")
    if not key:
        return None
    title = text.splitlines()[0].replace("*", "")[:32] or "stock-signal"
    try:
        d = _post("https://sctapi.ftqq.com/%s.send" % key,
                  {"title": title, "desp": text})
        return bool(d.get("code") == 0), "serverchan"
    except Exception as e:
        return False, "serverchan失败:%s" % str(e)[:60]

def _pushplus(text):
    token = ENV.get("PUSHPLUS_TOKEN", "")
    if not token:
        return None
    title = text.splitlines()[0].replace("*", "")[:32] or "stock-signal"
    try:
        d = _post("http://www.pushplus.plus/send",
                  {"token": token, "title": title, "content": text,
                   "template": "markdown"})
        return bool(d.get("code") == 200), "pushplus"
    except Exception as e:
        return False, "pushplus失败:%s" % str(e)[:60]

def _wecom(text):
    url = ENV.get("WECOM_WEBHOOK_URL", "")
    if not url:
        return None
    try:
        while len(text.encode("utf-8")) > 3800:  # 企业微信markdown限4096字节
            text = text[:len(text) - 200]
        d = _post(url, {"msgtype": "markdown", "markdown": {"content": text}})
        return bool(d.get("errcode") == 0), "wecom"
    except Exception as e:
        return False, "wecom失败:%s" % str(e)[:60]

def _wxpusher(text):
    tok, uid = ENV.get("WXPUSHER_APP_TOKEN", ""), ENV.get("WXPUSHER_UID", "")
    if not tok or not uid:
        return None
    try:
        d = _post("https://wxpusher.zjiecode.com/api/send/message",
                  {"appToken": tok, "content": text, "contentType": 3,
                   "uids": [uid]})
        return bool(d.get("code") == 1000), "wxpusher"
    except Exception as e:
        return False, "wxpusher失败:%s" % str(e)[:60]

def send_markdown(text):
    """向所有已配置通道广播。返回(ok, 各通道结果摘要)"""
    _load_env()
    results = []
    for attempt in range(2):
        results = [r for r in (_feishu(text), _serverchan(text), _pushplus(text),
                               _wecom(text), _wxpusher(text)) if r]
        if any(r[0] for r in results):
            return True, "+".join(r[1] for r in results if r[0])
        if attempt == 0:
            time.sleep(3)
    return False, "; ".join("%s" % r[1] for r in results) or "无已配置通道"
