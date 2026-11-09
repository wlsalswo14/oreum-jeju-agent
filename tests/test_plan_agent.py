"""계획 Agent 도구 및 규칙 추적, 체크리스트 병합 테스트 (네트워크 없이)."""
from app.agent import plan_tools, planner


def test_plan_tools_find_region():
    out = plan_tools.run_tool("find_region", {"text": "모슬포"})
    assert out.data[0]["name"] == "모슬포"
    assert "대정읍" in out.data[0]["bjd"]


def test_plan_tools_plain_normalization_and_catalog():
    out = plan_tools.run_tool("find_region", {"text": {"type": "string", "description": "애월읍"}})
    assert out.data[0]["name"] == "애월읍"

    cat = plan_tools.tools_catalog_text()
    assert '"type"' not in cat
    assert "애월" in cat


def test_plan_tools_find_industry():
    assert plan_tools.run_tool("find_industry", {"text": "카페"}).data[0]["name"] == "비알콜 음료점업"


def test_plan_tools_find_festival():
    assert plan_tools.run_tool("find_festival", {"text": "방어축제", "year": 2024}).data[0]["start"] == "2024-11-28"


def test_plan_tools_search_datasets():
    res = plan_tools.run_tool("search_datasets", {"query": "와이파이"})
    assert "4-04" in res.ids


def test_plan_tools_get_dataset():
    assert plan_tools.run_tool("get_dataset", {"id": "1-08"}).ids == ["1-08"]
    assert plan_tools.run_tool("get_dataset", {"id": "9-99"}).ids == []


def test_plan_tools_unknown():
    res = plan_tools.run_tool("없는도구", {})
    assert "알 수 없는" in res.summary


def test_rule_plan_traced():
    trace = []
    p = planner.rule_plan_traced("애월읍에서 카페 창업 상권", trace)
    assert p.mode == "trade_area"
    tools = [t["tool"] for t in trace if t["kind"] == "tool"]
    assert tools == ["find_region", "find_industry", "search_datasets"]
    assert trace[-1]["kind"] == "final"
    assert isinstance(p.datasets, list)


def test_agent_checklist_merge(tmp_path, monkeypatch):
    from app.agent import resolve
    from app.agent.orchestrator import Agent
    from app.agent.plan_tools import dataset_card
    from app.config import settings
    from app.session import SessionStore

    monkeypatch.setattr(settings, "llm_enabled", False)
    s = SessionStore(tmp_path).create()
    a = Agent(s)
    a.plan("애월읍에서 카페를 창업하려고 하는데 상권이 어떤가요?")
    a.st["plan"]["datasets"] = [
        dataset_card("1-08") | {"reason": "r1"},
        dataset_card("4-05") | {"reason": "r2"},
    ]
    a.st["checklist"] = resolve.data_checklist(a.st["plan"]["mode"], a.st["resolved"])
    a._merge_ai_picks()

    item_108 = next(c for c in a.st["checklist"] if c.get("id") == "1-08")
    assert item_108.get("ai_pick") == "r1"

    last_item = a.st["checklist"][-1]
    assert last_item.get("id") == "4-05"
    assert last_item.get("need") == "suggested"
    assert last_item.get("ai_pick") == "r2"
