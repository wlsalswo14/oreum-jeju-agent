"""해석·서술 작성기.

- LLM 모드: 반출 검사를 통과한 집계표(llm_gate)만 근거로 보고서 문장을 JSON으로 작성.
- 수치 근거 검증: 작성된 문장의 모든 수치가 근거 표에 있는지 자동 대조, 불일치 시 1회 수정 요청.
- 규칙 모드(API 키 없음/장애): KPI·범주형 사실로 템플릿 문장을 생성 → 어떤 환경에서도 보고서 완성.
"""
from __future__ import annotations

import re

from ..guard.llm_gate import GatedFacts
from ..config import settings
from .llm import LLM, LLMUnavailable

NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "detail", "evidence"],
                "additionalProperties": False,
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
                "required": ["title", "detail"],
                "additionalProperties": False,
            },
        },
        "business_text": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "export_purpose": {"type": "string"},
    },
    "required": ["headline", "summary", "findings", "recommendations", "business_text", "limitations", "export_purpose"],
    "additionalProperties": False,
}

BUSINESS_TITLE = {
    "trade_area": "사업계획서용 '시장 분석' 문안",
    "event_impact": "정책 보고용 요약 문안",
    "transit": "노선·정류소 개선 검토 메모",
}

from .prompts import NARRATIVE_FIX, NARRATIVE_SYSTEM as SYSTEM  # noqa: E402  (프롬프트는 prompts.py)


def write_llm(llm: LLM, question: str, mode: str, context: str, facts: GatedFacts, notes: list[str],
              cat_facts: dict, synthetic: bool) -> tuple[dict, list[str]]:
    user = (
        f"[이용자 질문]\n{question}\n\n[분석 유형] {mode} ({BUSINESS_TITLE.get(mode, '')})\n"
        f"[분석 조건]\n{context}\n"
        f"[데이터 상태] {'가상(SYNTHETIC) 센터 데이터 사용' if synthetic else '실제 데이터'}\n"
        f"[범주형 사실(수치 아님)]\n{_cat_text(cat_facts)}\n"
        f"[분석 시 누락·주의 사항]\n" + ("\n".join(f"- {n}" for n in notes) if notes else "- 없음") +
        f"\n\n[근거 집계표 — 반출 검사 통과본]\n{facts.text}"
    )
    out = llm.json("narrative", SYSTEM, user, NARRATIVE_SCHEMA, effort=settings.llm_effort_write, max_tokens=12000)
    _require_fields(out)
    bad = unverified_numbers(_all_text(out), facts.text + "\n" + question + "\n" + context)
    if bad:
        fix = user + NARRATIVE_FIX.format(bad=", ".join(bad[:15]))
        out = llm.json("narrative_fix", SYSTEM, fix, NARRATIVE_SCHEMA, effort=settings.llm_effort_write, max_tokens=12000)
        _require_fields(out)
        bad = unverified_numbers(_all_text(out), facts.text + "\n" + question + "\n" + context)
    return out, bad


def _require_fields(o) -> None:
    """응답이 스키마의 필수 항목을 갖췄는지 확인(빠지면 규칙 기반으로 전환되도록 예외)."""
    if not isinstance(o, dict):
        raise ValueError("AI 응답이 JSON 객체가 아닙니다")
    miss = [k for k in NARRATIVE_SCHEMA.get("required", []) if k not in o]
    if miss or not isinstance(o.get("findings"), list) or not isinstance(o.get("recommendations"), list):
        raise ValueError(f"AI 응답에 필수 항목이 없습니다: {', '.join(miss) or 'findings/recommendations'}")


def _cat_text(d: dict) -> str:
    lines = []
    for k, v in d.items():
        if k.startswith("_") or v is None:
            continue
        if isinstance(v, float):
            continue  # 수치는 표에서만
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) or "- 없음"


def _all_text(o: dict) -> str:
    parts = [o.get("headline", ""), o.get("summary", ""), o.get("business_text", "")]
    for f in o.get("findings", []):
        parts += [f.get("title", ""), f.get("detail", "")]
    for r in o.get("recommendations", []):
        parts += [r.get("title", ""), r.get("detail", "")]
    parts += o.get("limitations", [])
    return "\n".join(parts)


_NUM = re.compile(r"(?<![\d.,])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(\s*(?:억|만|천)?)")


_NUMPART = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# '8억 2,272만'·'1억 2,000' 같은 복합 한국어 금액(큰 단위 + 작은 단위)
_COMPOUND = re.compile(rf"(?<![\d.,])(-?(?:{_NUMPART}))\s*(조|억)\s*({_NUMPART})(\s*(?:만|천)?)(?![\d.,])")
_KUNIT = {"조": 1e12, "억": 1e8, "만": 1e4, "천": 1e3}


def _numbers(text: str) -> list[tuple[str, float, float]]:
    """(원문, 값, 허용오차) — 허용오차는 표기 자릿수 기준 반올림 폭."""
    out = []
    for m in _COMPOUND.finditer(text):
        try:
            big = float(m.group(1).replace(",", "")) * _KUNIT[m.group(2)]
            small_unit = _KUNIT.get((m.group(4) or "").strip(), 1)
            small = float(m.group(3).replace(",", "")) * small_unit
        except ValueError:
            continue
        sign = -1 if big < 0 else 1
        out.append((m.group(0).strip(), big + sign * small, 0.5 * small_unit + 1e-9))
    text = _COMPOUND.sub(lambda m: " " * len(m.group(0)), text)   # 복합 표기는 위에서 처리했으므로 개별 숫자로 다시 세지 않음
    for m in _NUM.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").strip()
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        mult = {"억": 1e8, "만": 1e4, "천": 1e3}.get(unit, 1)
        dec = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * (10 ** -dec) * mult + 1e-9
        out.append((raw + unit, v * mult, tol))
    return out


def unverified_numbers(text: str, evidence: str) -> list[str]:
    """문장 속 수치가 근거(표·질문·조건) 수치와 반올림 범위 안에서 일치하는지 검사."""
    ev = [v for _, v, _ in _numbers(evidence)]
    bad = []
    for raw, v, tol in _numbers(text):
        a = abs(v)
        if a < 10 or (1990 <= a <= 2100 and float(a).is_integer()):
            continue  # 순위·개수·연도 등 작은 정수는 제외
        if not any(abs(abs(e) - a) <= max(tol, a * 0.001) for e in ev if e):
            bad.append(raw)
    return list(dict.fromkeys(bad))


from .rule_writer import write_rule  # noqa: E402  (규칙 기반 작성기)


def safe_write(llm: LLM | None, **kw) -> tuple[dict, list[str], str]:
    """LLM 가능하면 LLM, 실패하면 규칙 기반. (narrative, unverified, mode)"""
    if llm is not None and llm.available:
        try:
            out, bad = write_llm(llm, kw["question"], kw["mode"], kw["context"], kw["facts"], kw["notes"],
                                 kw["cat_facts"], kw["synthetic"])
            return out, bad, "llm"
        except Exception as e:  # 어떤 AI 오류든 규칙 기반으로 전환(서비스 중단 없음)
            kw["notes"] = list(kw["notes"]) + [f"AI 서술 생성 실패로 규칙 기반 문장 사용({e})"]
    out = write_rule(kw["mode"], kw["title"], kw["kpis"], kw["cat_facts"], kw["notes"], kw["synthetic"],
                     kw["params"], kw["tables_present"])
    return out, [], "rule"
