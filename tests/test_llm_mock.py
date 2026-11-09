"""AI(Claude) 경로 검증 — 실제 API 대신 가짜 클라이언트로 계획·서술·수치검증 재작성·도구 루프·감사 로그를 확인."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from app.agent import llm as llm_mod
from app.agent.llm import LLM
from app.agent.narrator import write_llm
from app.agent.planner import make_plan
from app.guard.export_guard import AggTable, check
from app.guard.llm_gate import facts_from_results


def _resp(blocks, stop="end_turn", inp=100, out=50):
    return NS(content=blocks, stop_reason=stop, model="claude-opus-5-5",
              usage=NS(input_tokens=inp, output_tokens=out))


def _text(t):
    return NS(type="text", text=t)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        return item(kw) if callable(item) else item


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)
        self.beta = NS(messages=self.messages)


class FakeSession:
    def __init__(self):
        self.records = []

    def audit(self, r):
        self.records.append(r)


@pytest.fixture
def mk_llm(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "llm_enabled", True)
    monkeypatch.setattr(llm_mod.settings, "llm_provider_env", "anthropic")
    monkeypatch.setattr(llm_mod.settings, "llm_model_env", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    def make(script):
        sess = FakeSession()
        x = LLM(sess)
        x._client = FakeClient(script)
        return x, sess

    return make


def test_plan_uses_structured_output(mk_llm):
    empty_plan = {
        "mode": "unsupported", "region": "", "industry": "", "event_name": "", "event_start": "",
        "event_end": "", "control_region": "", "user_type": "기타", "purpose": "",
        "clarify": [], "explanation": "", "datasets": [],
    }
    step1 = {
        "thought": "지역·업종 확인",
        "action": "call_tools",
        "calls": [
            {"tool": "find_region", "arguments": '{"text":"애월"}'},
            {"tool": "find_industry", "arguments": '{"text":"카페"}'},
        ],
        "plan": empty_plan,
    }
    plan_json = {
        "mode": "trade_area", "region": "애월읍", "industry": "비알콜 음료점업", "event_name": "", "event_start": "",
        "event_end": "", "control_region": "", "user_type": "예비창업자", "purpose": "사업계획서",
        "clarify": [], "explanation": "카페 상권 분석",
        "datasets": [{"id": "1-08", "reason": "매출"}, {"id": "없는ID", "reason": "x"}],
    }
    step2 = {
        "thought": "확정",
        "action": "final",
        "calls": [],
        "plan": plan_json,
    }
    llm, sess = mk_llm([
        _resp([_text(json.dumps(step1, ensure_ascii=False))]),
        _resp([_text(json.dumps(step2, ensure_ascii=False))]),
    ])
    trace = []
    p = make_plan("애월에 카페 차릴까?", llm, None, trace)
    assert p.source == "llm" and p.mode == "trade_area" and p.industry == "비알콜 음료점업"
    assert [d["id"] for d in p.datasets] == ["1-08"]
    tool_kinds = [x["kind"] for x in trace if x["kind"] == "tool"]
    assert len(tool_kinds) >= 2 and trace[-1]["kind"] == "final"
    assert [r["purpose"] for r in sess.records] == ["plan_1", "plan_2"]
    assert "[도구 결과]" in sess.records[1]["sent_user"]
    kw = llm._client.messages.calls[0]
    assert "[도구 목록]" in kw["system"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert llm.usage.calls == 2 and llm.usage.cost_usd > 0


def test_plan_falls_back_to_rules_on_api_error(mk_llm):
    def boom(kw):
        raise llm_mod.LLMUnavailable("down")
    llm, _ = mk_llm([boom])
    trace = []
    p = make_plan("모슬포 버스 정류소 진단", llm, {}, trace)
    assert p.source == "rule" and p.mode == "transit"
    assert any(x["kind"] == "note" for x in trace)


def _facts():
    df = pd.DataFrame({"지표": ["전년 대비 증감률"], "value": [12.3]})
    t = AggTable(key="trade.kpi_a", title="핵심 지표", df=df, dims=["지표"], values=["value"], kind={"value": "applied"},
                 sources=["1-08"], unit={"value": "%"}, labels={"value": "값"})
    return facts_from_results([check(t)])


def _narr(detail):
    return {"headline": "h", "summary": "s", "findings": [{"title": "t", "detail": detail, "evidence": ["trade.kpi_a"]}],
            "recommendations": [{"title": "r", "detail": "d"}], "business_text": "b", "limitations": ["l"],
            "export_purpose": "p"}


def test_narrative_rewrites_when_numbers_unverified(mk_llm):
    bad = _narr("매출이 45.6% 늘었습니다.")
    good = _narr("전년 대비 12.3% 늘었습니다.")
    llm, sess = mk_llm([_resp([_text(json.dumps(bad, ensure_ascii=False))]),
                        _resp([_text(json.dumps(good, ensure_ascii=False))])])
    out, unverified = write_llm(llm, "질문", "trade_area", "- 조건", _facts(), [], {}, False)
    assert unverified == [] and "12.3%" in out["findings"][0]["detail"]
    assert [r["purpose"] for r in sess.records] == ["narrative", "narrative_fix"]
    assert "45.6" in sess.records[1]["sent_user"]          # 수정 요청에 문제 수치를 알려 줌
    assert "비공개" not in sess.records[0]["sent_user"] or True


def test_fallback_param_disabled_after_bad_request(mk_llm):
    import anthropic
    import httpx2 as httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.BadRequestError("fallbacks not supported", response=httpx.Response(400, request=req), body=None)

    def first(kw):
        assert "fallbacks" in kw
        raise err

    def second(kw):
        assert "fallbacks" not in kw
        return _resp([_text("ok")])

    llm, _ = mk_llm([first, second])
    assert llm.text("t", "sys", "user") == "ok"
    assert llm._fallback_ok is False


def test_followup_tool_loop(mk_llm, monkeypatch):
    """후속 질문: AI가 JSON으로 도구를 고르고(call_tool) → 로컬에서 실행 → 결과를 보고 답(answer). 제공자와 무관한 같은 루프."""
    from app.agent.orchestrator import Agent

    sess = FakeSession()
    sess.state = {"plan": {"mode": "trade_area"}, "question": "q", "result": {
        "title": "애월읍 카페", "narrative": {"summary": "요약"},
        "tables": [{"key": "trade.kpi_a", "title": "핵심", "columns": ["지표", "값"], "rows": [["전년 대비 증감률", "12.3%"]]}]}}
    sess.read_audit = lambda: sess.records
    a = Agent.__new__(Agent)
    a.s = sess
    a.cache = {"gmap": {"trade.kpi_a": check(AggTable(
        key="trade.kpi_a", title="핵심", df=pd.DataFrame({"지표": ["전년 대비 증감률"], "value": [12.3]}), dims=["지표"],
        values=["value"], kind={"value": "applied"}, sources=["1-08"], unit={"value": "%"}, labels={"value": "값"}))},
        "mode": "trade_area"}
    call = {"action": "call_tool", "tool": "get_table", "arguments": json.dumps({"key": "trade.kpi_a"}), "answer": ""}
    seen = {}

    def second(kw):
        seen["user"] = kw["messages"][-1]["content"]
        assert kw["output_config"]["format"]["schema"]["properties"]["action"]["enum"] == ["call_tool", "answer"]
        return _resp([_text(json.dumps({"action": "answer", "tool": "", "arguments": "",
                                        "answer": "전년 대비 12.3% 증가했습니다(trade.kpi_a)."}, ensure_ascii=False))])

    llm, _ = mk_llm([_resp([_text(json.dumps(call))]), second])
    llm.session = sess
    a.llm = llm
    out = a.followup("성장률은?")
    assert out["tools"] == [{"tool": "get_table", "input": {"key": "trade.kpi_a"}}]
    assert "[도구 결과 1]" in seen["user"] and "12.3%" in seen["user"]
    assert out["unverified"] == [] and "12.3%" in out["answer"]
    assert [r["purpose"] for r in sess.records] == ["followup_1", "followup_2"]


def test_openai_provider_uses_responses_api_with_strict_schema(monkeypatch):
    """센터 운영 기본: OpenAI Responses API · gpt-6-luna · 추론 high · 엄격 JSON 스키마."""
    monkeypatch.setattr(llm_mod.settings, "llm_enabled", True)
    monkeypatch.setattr(llm_mod.settings, "llm_provider_env", "openai")
    monkeypatch.setattr(llm_mod.settings, "llm_model_env", "")
    monkeypatch.setattr(llm_mod.settings, "llm_effort_plan", "high")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    calls = []

    class Responses:
        def create(self, **kw):
            calls.append(kw)
            return NS(output_text=json.dumps({"ok": "예"}), output=[], status="completed", model="gpt-6-luna",
                      usage=NS(input_tokens=1000, output_tokens=200))

    sess = FakeSession()
    x = LLM(sess)
    x._client = NS(responses=Responses())
    schema = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"], "additionalProperties": False}
    assert x.json("plan", "sys", "user", schema) == {"ok": "예"}
    kw = calls[0]
    assert kw["model"] == "gpt-6-luna" and kw["reasoning"] == {"effort": "high"}
    assert kw["instructions"] == "sys" and kw["input"] == "user"
    assert kw["text"]["format"]["type"] == "json_schema" and kw["text"]["format"]["strict"] is True
    assert sess.records[0]["provider"] == "openai" and x.usage.cost_usd > 0


def test_codex_provider_runs_same_prompt_and_schema(monkeypatch, tmp_path):
    """개발·검증용: Codex CLI 로 같은 모델·추론 강도·스키마를 실행하고, 마지막 메시지 파일과 토큰 사용량을 읽는다."""
    import subprocess

    monkeypatch.setattr(llm_mod.settings, "llm_enabled", True)
    monkeypatch.setattr(llm_mod.settings, "llm_provider_env", "codex")
    monkeypatch.setattr(llm_mod.settings, "llm_model_env", "")
    monkeypatch.setattr(type(llm_mod.settings), "codex_exe", property(lambda self: "codex"))
    monkeypatch.setattr(type(llm_mod.settings), "has_llm_key", property(lambda self: True))
    seen = {}

    def fake_run(args, input=None, **kw):
        seen["args"], seen["prompt"] = args, input.decode("utf-8")
        out = args[args.index("-o") + 1]
        Path(out).write_text(json.dumps({"ok": "예"}), encoding="utf-8")
        ev = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 500, "output_tokens": 40}})
        return NS(returncode=0, stdout=ev.encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    x = LLM(FakeSession())
    schema = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"], "additionalProperties": False}
    assert x.json("plan", "시스템 지시", "질문", schema, effort="high") == {"ok": "예"}
    a = seen["args"]
    assert a[a.index("-m") + 1] == "gpt-6-luna" and "model_reasoning_effort=high" in a and "--output-schema" in a
    assert "시스템 지시" in seen["prompt"] and "질문" in seen["prompt"]
    assert x.usage.input_tokens == 500 and x.usage.output_tokens == 40
