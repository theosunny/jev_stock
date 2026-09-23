import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "stock-signal/scripts"
sys.path.insert(0, str(SCRIPTS))

import strategy_identity
import codex_cycle as cycle


def _plan(**extra):
    return {
        "watchlist": [{"code": "sz000001", "name": "样例"}],
        "总资金": 100000,
        **extra,
    }


def test_identity_is_known_engine_not_plan_strategy_label(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "one.py").write_text("RULE = 1\n", encoding="utf-8")

    identity = strategy_identity.build_strategy_identity(
        _plan(strategy="任意未启用的策略名称"), rules_dir=rules, rule_files=("one.py",)
    )

    assert identity["id"] == "chen-xiaoq-un-rule-based-v1"
    assert identity["name"] == "陈小群思路 · 主线情绪短线"
    assert identity["source"]["selection_engine"] == "codex_cycle_rule_based"
    assert "任意未启用的策略名称" not in json.dumps(identity, ensure_ascii=False)
    assert identity["version"].startswith("sha256:")


def test_identity_hash_is_stable_for_mapping_order_and_changes_for_plan_or_rules(tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    rule = rules / "one.py"
    rule.write_text("RULE = 1\n", encoding="utf-8")
    first_plan = _plan(watchlist=[{"code": "sz000001", "enabled": True}, {"code": "sh600000", "enabled": False}])
    reordered_plan = {"总资金": 100000, "watchlist": [{"enabled": True, "code": "sz000001"}, {"enabled": False, "code": "sh600000"}]}

    first = strategy_identity.build_strategy_identity(first_plan, rules_dir=rules, rule_files=("one.py",))
    reordered = strategy_identity.build_strategy_identity(reordered_plan, rules_dir=rules, rule_files=("one.py",))
    changed_plan = strategy_identity.build_strategy_identity(
        _plan(watchlist=[{"code": "sz000001", "enabled": False}, {"code": "sh600000", "enabled": False}]),
        rules_dir=rules,
        rule_files=("one.py",),
    )

    assert first == reordered
    assert first["version"] != changed_plan["version"]
    rule.write_text("RULE = 2\n", encoding="utf-8")
    changed_rule = strategy_identity.build_strategy_identity(first_plan, rules_dir=rules, rule_files=("one.py",))
    assert first["version"] != changed_rule["version"]


def test_identity_fails_closed_when_a_rule_file_is_unreadable(tmp_path):
    with pytest.raises(strategy_identity.StrategyIdentityError):
        strategy_identity.build_strategy_identity(_plan(), rules_dir=tmp_path, rule_files=("missing.py",))


def test_rolling_scope_records_its_selector_revision(tmp_path):
    (tmp_path / "rules.py").write_text("RULE = 1\n", encoding="utf-8")
    identity = strategy_identity.strategy_identity(
        _plan(), rules_dir=tmp_path, rule_files=("rules.py",), scope="rolling", selector_revision="rolling-v1"
    )
    assert identity["scope"] == "rolling"
    assert identity["selector_revision"] == "rolling-v1"


def test_cycle_persists_the_current_strategy_identity(tmp_path):
    now = __import__("datetime").datetime(2026, 9, 22, 14, 0)
    (tmp_path / "plan.json").write_text(json.dumps(_plan()), encoding="utf-8")
    snapshot = {"as_of": now.isoformat(), "quotes": {"sz000001": {"price": 10, "time": "20260922140000"}}, "emotion": {}, "warnings": []}
    decision = {"buy_allowed": False, "risk_reasons": [], "stocks": [{"code": "sz000001", "name": "样例", "action": "观察", "reason": "等待", "price": 10}]}
    jev = {"model": "test", "answers": {"market": {"choice": "分歧"}, "mainline": {"choice": "无明确主线"}, "action_sz000001": {"choice": "观察"}, "reason_sz000001": {"choice": "等待"}}}

    result = cycle.run_cycle(tmp_path, now, collector=lambda *_args: snapshot, evaluator=lambda *_args: decision, analyzer=lambda *_args: jev)
    record = cycle.read_record(tmp_path, result["report_id"])

    assert record["strategy"]["id"] == strategy_identity.STRATEGY_ID
    assert record["strategy"]["version"].startswith("sha256:")
