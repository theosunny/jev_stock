"""Stable, non-secret provenance for the active rule-based strategy.

The value is report metadata only.  It deliberately identifies the known
Codex-cycle engine rather than accepting a free-form label from plan.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


STRATEGY_ID = "chen-xiaoq-un-rule-based-v1"
STRATEGY_NAME = "陈小群思路 · 主线情绪短线"
SCHEMA_VERSION = 1
RULE_FILES = (
    "codex_cycle.py",
    "live_analysis.py",
    "live_jev.py",
    "report_sections.py",
    "../references/market-review.md",
)


class StrategyIdentityError(ValueError):
    """Raised when complete strategy provenance cannot be calculated."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StrategyIdentityError("strategy plan cannot be canonicalized") from exc


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_strategy_identity(
    plan: dict[str, Any], *, rules_dir: Path | None = None, rule_files: Iterable[str] = RULE_FILES,
    scope: str = "cycle", selector_revision: str | None = None,
) -> dict[str, Any]:
    """Return a reproducible strategy identity without exposing plan contents.

    ``version`` changes when the canonical plan or any declared rule source
    changes.  Missing or unreadable sources raise instead of returning a
    partial fingerprint that could be mistaken for complete provenance.
    """
    if not isinstance(plan, dict):
        raise StrategyIdentityError("strategy plan is invalid")
    if scope not in ("cycle", "rolling"):
        raise StrategyIdentityError("strategy scope is invalid")
    if selector_revision is not None and (not isinstance(selector_revision, str) or not selector_revision):
        raise StrategyIdentityError("research selector revision is invalid")
    root = Path(rules_dir) if rules_dir is not None else Path(__file__).resolve().parent
    plan_version = _hash(_canonical_json(plan))
    source_hashes = []
    for relative_name in tuple(rule_files):
        if not isinstance(relative_name, str) or not relative_name:
            raise StrategyIdentityError("strategy rule declaration is invalid")
        try:
            content = (root / relative_name).read_bytes()
        except OSError as exc:
            raise StrategyIdentityError("strategy rule source is unavailable") from exc
        source_hashes.append({"path": relative_name, "sha256": _hash(content)})
    rule_version = _hash(_canonical_json(source_hashes))
    version = _hash(_canonical_json({
        "schema_version": SCHEMA_VERSION,
        "strategy_id": STRATEGY_ID,
        "scope": scope,
        "plan_version": plan_version,
        "rules_version": rule_version,
        "selector_revision": selector_revision,
    }))
    return {
        "schema_version": SCHEMA_VERSION,
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "version": "sha256:" + version,
        "rules_version": "sha256:" + rule_version,
        "plan_version": "sha256:" + plan_version,
        "scope": scope,
        "selector_revision": selector_revision,
        "source": {
            "selection_engine": "codex_cycle_rule_based",
            "report_scope": scope,
            "rule_files": [item["path"] for item in source_hashes],
            "plan": "canonical_data_dir_plan_json",
        },
    }


def strategy_identity(plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Public shorthand used by cycle and rolling-research report writers."""
    return build_strategy_identity(plan, **kwargs)
