"""질문 → 분석 계획(Plan).

LLM 모드: 질문 텍스트와 '공개 명세 정보(지역·업종 목록)'만 보내 구조화된 계획(JSON)을 받는다.
규칙 모드: 키워드·사전 매칭으로 같은 형식의 계획을 만든다(API 키 없이도 동작).
LLM은 데이터 값을 보지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .llm import LLM, LLMUnavailable

MODES = ["trade_area", "event_impact", "transit", "unsupported"]
MODE_KO = {"trade_area": "창업·상권 분석", "event_impact": "축제·행사 효과 분석", "transit": "버스 정류소·노선 진단",
           "unsupported": "데이터 탐색"}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": MODES},
        "region": {"type": "string"},
        "industry": {"type": "string"},
        "event_name": {"type": "string"},
        "event_start": {"type": "string"},
        "event_end": {"type": "string"},
        "control_region": {"type": "string"},
        "user_type": {"type": "string", "enum": ["예비창업자", "소상공인", "공공기관", "연구자", "기업", "기타"]},
        "purpose": {"type": "string"},
        "clarify": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
    "required": ["mode", "region", "industry", "event_name", "event_start", "event_end", "control_region",
                 "user_type", "purpose", "clarify", "explanation"],
    "additionalProperties": False,
}

# 프롬프트는 prompts.py(PLAN_AGENT_SYSTEM)에 모아 둠


@dataclass
class Plan:
    mode: str
    region: str = ""
    industry: str = ""
    event_name: str = ""
    event_start: str = ""
    event_end: str = ""
    control_region: str = ""
    user_type: str = "기타"
    purpose: str = ""
    clarify: list[str] = field(default_factory=list)
    explanation: str = ""
    source: str = "rule"   # llm | rule
    radius_km: str = ""    # 버스 진단 반경(이용자 수정값)
    datasets: list = field(default_factory=list)   # Agent가 찾아 고른 데이터(카드 + 고른 이유)

    def to_dict(self):
        d = asdict(self)
        d["mode_ko"] = MODE_KO.get(self.mode, self.mode)
        return d


def make_plan(question: str, llm: LLM | None, vocab: dict | None = None, trace: list | None = None) -> Plan:
    """질문 → 계획. AI가 있으면 도구를 스스로 골라 호출하는 Agent 루프, 없거나 실패하면 규칙 기반.

    trace(list)를 주면 단계마다 기록을 덧붙인다 → 화면 「오름이 한 일」에 실시간으로 보인다.
    """
    if llm is not None and llm.available:
        try:
            return agent_plan(question, llm, trace)
        except Exception as e:  # 어떤 AI 오류든 규칙 기반 해석으로 전환(서비스 중단 없음)
            _t(trace, "note", "AI 해석 실패 → 규칙 기반으로 전환", by="규칙", detail=type(e).__name__)
    return rule_plan_traced(question, trace)


# ---------------------------------------------------------------------------------------  Agent 루프
from . import plan_tools as PT  # noqa: E402
from .prompts import PLAN_AGENT_SYSTEM, PLAN_TOOLS_HEADER  # noqa: E402

MAX_ROUNDS = 3
MAX_CALLS = 4

PLAN_OUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {**PLAN_SCHEMA["properties"],
                   "datasets": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["id", "reason"],
                       "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}}},
    "required": PLAN_SCHEMA["required"] + ["datasets"],
}
STEP_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["thought", "action", "calls", "plan"],
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": ["call_tools", "final"]},
        "calls": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["tool", "arguments"],
            "properties": {"tool": {"type": "string", "enum": PT.TOOL_NAMES}, "arguments": {"type": "string"}}}},
        "plan": PLAN_OUT_SCHEMA,
    },
}


def _now() -> str:
    import time as _time

    return _time.strftime("%H:%M:%S")


def _t(trace: list | None, kind: str, title: str, by: str = "AI", detail: str = "", tool: str = "", args: dict | None = None,
       sec: float | None = None):
    if trace is None:
        return
    trace.append({"t": _now(), "kind": kind, "title": title, "by": by, "detail": detail, "tool": tool,
                  "args": args or {}, "sec": sec})


def agent_plan(question: str, llm: LLM, trace: list | None = None) -> Plan:
    import json as _json
    import time as _time

    head = f"[질문]\n{question}\n\n[오늘 날짜] {date.today().isoformat()}\n\n먼저 도구로 지역·업종·행사와 필요한 데이터를 확인하세요."
    transcript = ""
    seen_ids: list[str] = []
    calls_done = 0
    for rnd in range(1, MAX_ROUNDS + 1):
        last = rnd == MAX_ROUNDS
        system = PLAN_AGENT_SYSTEM + PLAN_TOOLS_HEADER + PT.tools_catalog_text() + ("\n\n지금이 마지막 단계입니다. 도구를 더 부르지 말고 action=\"final\"로 계획을 확정하세요." if last else "")
        t0 = _time.time()
        step = llm.json(f"plan_{rnd}", system, head + transcript, STEP_SCHEMA)
        sec = round(_time.time() - t0, 1)
        thought = (step.get("thought") or "").strip()
        calls = [c for c in (step.get("calls") or []) if c.get("tool") in PT.TOOL_NAMES][:MAX_CALLS]
        if step.get("action") == "call_tools" and calls and not last:
            _t(trace, "think", thought or "필요한 정보를 도구로 확인합니다.", sec=sec)
            transcript += f"\n\n[단계 {rnd}] 생각: {thought}"
            for c in calls:
                try:
                    args = _json.loads(c.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError
                except ValueError:
                    args = {}
                t1 = _time.time()
                out = PT.run_tool(c["tool"], args)
                calls_done += 1
                seen_ids += [i for i in out.ids if i not in seen_ids]
                _t(trace, "tool", out.summary, tool=c["tool"], args=args, sec=round(_time.time() - t1, 2), by="도구")
                transcript += (f"\n[도구 호출] {c['tool']} {_json.dumps(args, ensure_ascii=False)}"
                               f"\n[도구 결과]\n{out.text}")
            continue
        d = step.get("plan") or {}
        p = Plan(**{k: d.get(k, "") for k in PLAN_SCHEMA["properties"]}, source="llm")
        if p.mode not in MODES:
            p.mode = "unsupported"
        p.datasets = _valid_datasets(d.get("datasets") or [])
        _t(trace, "final", thought or "계획을 확정했습니다.", sec=sec,
           detail=f"도구 {calls_done}회 사용 · {MODE_KO.get(p.mode, p.mode)}")
        return p
    raise LLMUnavailable("계획을 확정하지 못함")


def _valid_datasets(items: list) -> list[dict]:
    """AI가 고른 데이터 ID를 카탈로그로 검증(없는 ID는 버림)."""
    out, seen = [], set()
    for it in items:
        card = PT.dataset_card(str((it or {}).get("id", "")))
        if card and card["id"] not in seen:
            seen.add(card["id"])
            out.append({**card, "reason": str(it.get("reason", ""))[:160]})
    return out[:8]


def rule_plan_traced(question: str, trace: list | None = None) -> Plan:
    """규칙 기반 계획 + 같은 도구를 정해진 순서로 호출한 기록(키 없이도 작업 과정이 보이게)."""
    p = rule_plan(question)
    _t(trace, "think", f"키워드로 분석 유형 판단: {MODE_KO.get(p.mode, p.mode)}", by="규칙")
    seq = [("find_region", {"text": question})]
    if p.mode == "trade_area":
        seq.append(("find_industry", {"text": question}))
    if p.mode == "event_impact":
        seq.append(("find_festival", {"text": question}))
    seq.append(("search_datasets", {"query": question}))
    found: list[str] = []
    for name, args in seq:
        out = PT.run_tool(name, args)
        found += [i for i in out.ids if i not in found]
        _t(trace, "tool", out.summary, tool=name, args=args, by="도구")
    p.datasets = [{**c, "reason": "질문 키워드와 명세서 설명이 일치"} for c in (PT.dataset_card(i) for i in found[:5]) if c]
    _t(trace, "final", "계획을 확정했습니다.", by="규칙", detail=MODE_KO.get(p.mode, p.mode))
    return p


# ---------------------------------------------------------------------------------------
_TRADE = ["창업", "상권", "가게", "매장", "개업", "입지", "장사", "매출", "카페", "식당", "음식점", "점포", "사업계획",
          "차리", "오픈", "열려고", "운영"]
_EVENT = ["축제", "행사", "페스티벌", "문화제", "박람회", "마라톤", "효과", "대회"]
_TRANSIT = ["버스", "정류소", "정류장", "노선", "대중교통", "승차", "배차"]


def rule_plan(question: str) -> Plan:
    q = question.strip()
    score = {
        "event_impact": sum(k in q for k in _EVENT) * 1.2,
        "transit": sum(k in q for k in _TRANSIT) * 1.1,
        "trade_area": sum(k in q for k in _TRADE),
    }
    mode = max(score, key=score.get) if max(score.values()) > 0 else "unsupported"
    if "축제" in q or "행사" in q:
        mode = "event_impact"
    if mode == "unsupported":  # 키워드가 없어도 업종이 보이면 상권 질문으로 간주(예: "함덕 횟집 차리면")
        try:
            from ..catalog.industries import resolve_industry

            if resolve_industry(q):
                mode = "trade_area"
        except Exception:
            pass
    p = Plan(mode=mode, source="rule")
    p.region = q  # 지역·업종·축제 해석은 resolver가 질문 전체에서 수행
    p.industry = q if mode == "trade_area" else ""
    p.event_name = q if mode == "event_impact" else ""
    m = re.findall(r"(20\d{2})[.\-/년 ]\s*(\d{1,2})[.\-/월 ]\s*(\d{1,2})", q)
    if m:
        y, mo, d = m[0]
        p.event_start = f"{y}-{int(mo):02d}-{int(d):02d}"
        if len(m) > 1:
            y2, mo2, d2 = m[1]
            p.event_end = f"{y2}-{int(mo2):02d}-{int(d2):02d}"
    p.user_type = "예비창업자" if mode == "trade_area" else ("공공기관" if mode in ("event_impact", "transit") else "기타")
    p.purpose = {
        "trade_area": "창업 사업계획서의 시장 분석 근거 자료로 활용",
        "event_impact": "행사 성과 평가 및 차기 행사 운영 개선 보고 자료로 활용",
        "transit": "버스 노선·정류소 운영 개선 검토 자료로 활용",
    }.get(mode, "지역 현황 분석 자료로 활용")
    p.explanation = {
        "trade_area": "지역·업종의 카드 매출, 요일·시간대, 고객, 경쟁 점포, 유동인구, 대중교통 접근성을 분석합니다.",
        "event_impact": "행사 기간 방문인구·버스 승차·업종별 소비를 평시·전년·비교지역과 비교해 효과를 추정합니다.",
        "transit": "정류소별 승하차, 요일×시간 패턴, 첨두 집중도, 저이용 정류소를 진단합니다.",
    }.get(mode, "질문을 상권·축제·버스 분석 중 하나로 바꿔 주세요.")
    return p
