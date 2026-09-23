#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Low-token Codex heartbeat tick: pending → verify → run → send.

Designed for scheduled execution every ~5 minutes without LLM overhead.
Writes health status to STOCK_DATA_DIR/logs/codex-health.json for monitoring.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import datadir


TZ = ZoneInfo('Asia/Shanghai')


def now_cn():
    return dt.datetime.now(TZ)


def now_str():
    return now_cn().strftime('%Y-%m-%d %H:%M:%S %Z')


def setup_paths():
    """Resolve data and scripts directories."""
    root = Path(datadir.data_dir())
    scripts = Path(__file__).parent
    py = os.environ.get('PYTHON', sys.executable)
    log_dir = root / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        'root': root,
        'scripts': scripts,
        'py': py,
        'log_dir': log_dir,
        'lock': log_dir / 'codex-tick.lock',
        'log': log_dir / 'codex-tick.log',
        'err': log_dir / 'codex-tick.err',
        'health': log_dir / 'codex-health.json',
    }


def cycle(paths: dict, *args: str) -> dict | list | None:
    """Execute one codex_cycle.py command and parse JSON output."""
    proc = subprocess.run(
        [paths['py'], str(paths['scripts'] / 'codex_cycle.py'), *args],
        cwd=str(paths['scripts']),
        capture_output=True,
        text=True,
        env={**os.environ, 'STOCK_DATA_DIR': str(paths['root']), 'TZ': 'Asia/Shanghai'},
    )
    line = f"{now_str()} {' '.join(args)} rc={proc.returncode} {proc.stdout.strip()}"
    with paths['log'].open('a', encoding='utf-8') as fh:
        fh.write(line + '\n')
    if proc.stderr:
        with paths['err'].open('a', encoding='utf-8') as fh:
            fh.write(f"{now_str()} {' '.join(args)}\n{proc.stderr}\n")
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'raw': text, 'status': 'non_json', 'rc': proc.returncode}


def load_state(root: Path) -> dict:
    """Load the current codex_monitor state."""
    path = root / 'codex_monitor' / 'state.json'
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def main() -> int:
    """Execute one complete tick cycle with lock protection."""
    paths = setup_paths()
    os.environ['STOCK_DATA_DIR'] = str(paths['root'])

    with paths['lock'].open('w') as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            paths['health'].write_text(
                json.dumps({'ok': True, 'status': 'busy', 'at': now_str()}, ensure_ascii=False) + '\n',
                encoding='utf-8',
            )
            return 0

        # Step 1: Check pending and verify if needed
        cycle(paths, 'pending')
        state = load_state(paths['root'])
        outstanding = (state.get('unconfirmed') or {}).get('slack')
        if outstanding:
            cycle(paths, 'verify-slack', '--id', str(outstanding))

        # Step 2: Run analysis
        run = cycle(paths, 'run') or {}
        pending_map: dict[str, set[str]] = {}

        def add_pending(report_id, channels):
            if not report_id:
                return
            pending_map.setdefault(str(report_id), set()).update(channels or [])

        # Collect pending channels from run result
        if isinstance(run, dict):
            add_pending(run.get('report_id') or run.get('id'), run.get('pending_channels') or [])

        # Also check explicit pending call
        pend = cycle(paths, 'pending')
        if isinstance(pend, dict):
            add_pending(pend.get('report_id') or pend.get('id'), pend.get('pending_channels') or [])
            for item in pend.get('pending') or pend.get('items') or []:
                if isinstance(item, dict):
                    add_pending(item.get('report_id'), item.get('pending_channels') or [])
        elif isinstance(pend, list):
            for item in pend:
                if isinstance(item, dict):
                    add_pending(item.get('report_id'), item.get('pending_channels') or [])

        # Fallback: use latest_id if still has pending channels
        latest = load_state(paths['root']).get('latest_id')
        if latest and latest not in pending_map and isinstance(run, dict):
            add_pending(latest, run.get('pending_channels') or [])

        # Step 3: Send to pending channels
        send_results = []
        for report_id, channels in pending_map.items():
            if 'slack' in channels:
                send_results.append(cycle(paths, 'send-slack', '--id', report_id))
            if 'feishu' in channels:
                send_results.append(cycle(paths, 'send-feishu', '--id', report_id))

        # Step 4: Write health status
        state = load_state(paths['root'])
        health = {
            'ok': True,
            'at': now_str(),
            'run_status': (run or {}).get('status') if isinstance(run, dict) else None,
            'run_reason': (run or {}).get('reason') if isinstance(run, dict) else None,
            'report_id': (run or {}).get('report_id') if isinstance(run, dict) else None,
            'pending': {k: sorted(v) for k, v in pending_map.items()},
            'send_results': [
                {k: r.get(k) for k in ('status', 'report_id', 'reason', 'verify_channel') if isinstance(r, dict) and k in r}
                for r in send_results
                if r
            ],
            'latest_id': state.get('latest_id'),
            'last_run': state.get('last_run'),
            'unconfirmed': state.get('unconfirmed') or {},
        }

        # Determine if health is OK (not-ok only on hard failures)
        bad = []
        if isinstance(run, dict) and run.get('status') == 'error':
            bad.append('run_error')
        for r in send_results:
            if isinstance(r, dict) and r.get('status') in {'delivery_failed', 'delivery_uncertain'}:
                # feishu_not_configured is expected until lark-cli exists
                if r.get('reason') == 'feishu_not_configured':
                    continue
                bad.append(r.get('status'))
        if (state.get('unconfirmed') or {}).get('slack'):
            bad.append('slack_unconfirmed')

        health['ok'] = not bad
        health['alerts'] = bad
        paths['health'].write_text(json.dumps(health, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps(health, ensure_ascii=False))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
