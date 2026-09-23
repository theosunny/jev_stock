import json

import pytest

from assistant.strategies import SCHEMA_VERSION, StrategyCatalogError, get_catalog, validate_catalog


def strategy(strategy_id="test", version="1.0"):
    return {
        "id": strategy_id,
        "version": version,
        "name": "测试策略",
        "status": "template",
        "summary": "仅用于测试。",
        "horizon": "测试周期",
        "principles": ["原则"],
        "selection": ["筛选"],
        "entry": ["入场"],
        "exit": ["退出"],
        "risk": ["风控"],
        "data_requirements": ["数据"],
        "validation": ["验证"],
    }


def test_catalog_is_json_serializable_and_identifies_current_research_framework():
    catalog = get_catalog()

    assert catalog["schema_version"] == SCHEMA_VERSION == 1
    assert catalog["current_strategy_id"] == "chen-xiaoq-un-rule-based-v1"
    assert [item["status"] for item in catalog["strategies"]] == ["current", "template"]
    assert json.loads(json.dumps(catalog, ensure_ascii=False)) == catalog
    assert any("未实现" in item for item in catalog["strategies"][1]["validation"])


@pytest.mark.parametrize(
    "catalog",
    [
        {"schema_version": 99, "current_strategy_id": "test", "strategies": [strategy()]},
        {"schema_version": 1, "current_strategy_id": "test", "strategies": [strategy(version="")]},
        {"schema_version": 1, "current_strategy_id": "test", "strategies": [strategy(), strategy()]},
    ],
)
def test_catalog_validation_rejects_invalid_schema_version_or_duplicate_strategy_identity(catalog):
    with pytest.raises(StrategyCatalogError):
        validate_catalog(catalog)


def test_catalog_returns_independent_copies():
    first = get_catalog()
    first["strategies"][0]["principles"].append("不应保留")
    first["strategies"][0]["name"] = "被污染"

    second = get_catalog()

    assert "不应保留" not in second["strategies"][0]["principles"]
    assert second["strategies"][0]["name"] != "被污染"
