"""계획 단계 도구 — Agent가 질문을 해석할 때 스스로 호출하는 '찾기' 도구 5종.

모든 도구는 이 PC 안의 사전·명세서 카탈로그만 조회한다(원본 데이터 값은 보지 않는다).
- find_region     : 자연어 지역 표현 → 법정동·행정동·권역 후보(점수순)
- find_industry   : 업종 표현(카페, 횟집 …) → 비씨카드 42업종 후보
- find_festival   : 축제·행사 이름 → 제주데이터허브 축제 목록(761)의 회차·날짜·개최지
- search_datasets : 키워드 → 센터 명세서 71종 + 제주데이터허브 데이터 중 관련 데이터(반출 등급 포함)
- get_dataset     : 데이터 ID → 기간·갱신 주기·주요 컬럼·반출 등급

도구 결과는 (AI에게 줄 텍스트, 화면에 보여 줄 한 줄 요약, 구조화 값) 세 가지로 돌려준다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PLAN_TOOLS: list[dict] = [
    {"name": "find_region",
     "description": "질문 속 지역 표현(예: '애월', '모슬포', '중문 쪽', '일도1동')을 법정동·행정동·권역 표준 이름으로 찾는다.",
     "example": {"text": "애월"},
     "input_schema": {"type": "object", "properties": {"text": {"type": "string", "description": "지역 표현"}}, "required": ["text"]}},
    {"name": "find_industry",
     "description": "업종 표현(예: 카페, 횟집, 게스트하우스, 렌터카)을 비씨카드 42업종으로 찾는다. 상권 분석에 필요.",
     "example": {"text": "카페"},
     "input_schema": {"type": "object", "properties": {"text": {"type": "string", "description": "업종 표현"}}, "required": ["text"]}},
    {"name": "find_festival",
     "description": "축제·행사 이름으로 제주데이터허브 축제 목록(761)을 찾아 회차별 날짜·개최 지역을 돌려준다.",
     "example": {"text": "방어축제", "year": 2024},
     "input_schema": {"type": "object", "properties": {"text": {"type": "string", "description": "축제·행사 이름"},
                                                       "year": {"type": "integer", "description": "연도(모르면 0)"}},
                      "required": ["text"]}},
    {"name": "search_datasets",
     "description": "키워드로 제주빅데이터센터 명세서 데이터 71종과 제주데이터허브 데이터 중 관련 데이터를 찾는다(반출 등급 포함).",
     "example": {"query": "관광객 카드 소비"},
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "찾을 내용(예: 관광객 카드 소비, 와이파이 이용)"}},
                      "required": ["query"]}},
    {"name": "get_dataset",
     "description": "데이터 ID(예: 1-08, 4-11, hub:746)의 기간·갱신 주기·주요 컬럼·반출 등급을 확인한다.",
     "example": {"id": "1-08"},
     "input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "데이터 ID"}}, "required": ["id"]}},
]
TOOL_NAMES = [t["name"] for t in PLAN_TOOLS]
TOOL_KO = {"find_region": "지역 찾기", "find_industry": "업종 찾기", "find_festival": "축제 찾기",
           "search_datasets": "데이터 검색", "get_dataset": "데이터 상세"}


@dataclass
class ToolOut:
    text: str                       # AI에게 돌려줄 결과(짧은 표 형식)
    summary: str                    # 화면 한 줄 요약
    data: Any = None
    ids: list[str] = field(default_factory=list)   # search_datasets/get_dataset 이 확인한 데이터 ID


def _grade(ds) -> str:
    if ds.is_hub:
        return "공개 데이터(반출 가능)"
    return "반출 가능(원본 포함)" if ds.exportable else "가공·집계 결과만 반출(10 미만 처리)"


def find_region(text: str) -> ToolOut:
    from ..catalog import regions as R

    ms = [m for m in R.resolve_region(str(text or ""))[:5] if getattr(m, "level", "") != "island"]
    if not ms:
        return ToolOut("찾은 지역 없음. 다른 표현(읍면동 이름)으로 다시 찾아보세요.", f"'{text}' → 찾지 못함", [])
    rows, data = [], []
    for m in ms:
        d = m.to_dict()
        data.append({"name": d["name"], "level": d["level"], "bjd": d["bjd"], "city": d.get("city"), "score": d["score"]})
        rows.append(f"- {d['name']} (수준 {d['level']}, 법정동 {', '.join(d['bjd'][:6])}{' 외' if len(d['bjd']) > 6 else ''}, "
                    f"{d.get('city') or ''} {d.get('dstrct') or ''}, 일치 '{d.get('matched')}', 점수 {d['score']:.2f})")
    top = data[0]
    return ToolOut("\n".join(rows), f"'{text}' → {top['name']}" + (f" 외 {len(data) - 1}곳" if len(data) > 1 else ""), data)


def find_industry(text: str) -> ToolOut:
    from ..catalog import industries as I

    ms = I.resolve_industry(str(text or ""))[:4]
    if not ms:
        return ToolOut("찾은 업종 없음. 비씨카드 42업종 이름이나 다른 표현으로 다시 찾아보세요.", f"'{text}' → 찾지 못함", [])
    data = [{"code": m.code, "name": m.name, "matched": m.matched, "score": m.score} for m in ms]
    rows = [f"- {d['name']} (코드 {d['code']}, 일치 '{d['matched']}', 점수 {d['score']:.2f})" for d in data]
    return ToolOut("\n".join(rows), f"'{text}' → {data[0]['name']}", data)


def find_festival(text: str, year: int | None = None) -> ToolOut:
    from ..catalog import festivals as F

    ms = F.search_festival(str(text or ""), year=year or None)[:3]
    if not ms:
        return ToolOut("축제 목록(761)에서 찾지 못함. 이용자에게 날짜·장소를 물어야 합니다.", f"'{text}' → 찾지 못함", [])
    data, rows = [], []
    for m in ms:
        d = m.to_dict()
        others = [f"{o['start']}~{o['end']}" for o in (d.get("other_editions") or [])[-3:]]
        data.append({"name": d["name"], "start": d.get("start"), "end": d.get("end"), "region": d.get("region"),
                     "bjd": d.get("bjd"), "venue": d.get("venue"), "score": d["score"]})
        rows.append(f"- {d['name']}: {d.get('start') or '해당 연도 일정 없음'}~{d.get('end') or ''}, 개최지 {d.get('region') or '?'}"
                    f"({d.get('venue') or ''}), 점수 {d['score']:.2f}" + (f", 다른 회차 {', '.join(others)}" if others else "")
                    + (f", 메모 {d['note']}" if d.get("note") else ""))
    t = data[0]
    when = f" {t['start']}~{t['end']}" if t.get("start") else " (일정 없음)"
    return ToolOut("\n".join(rows), f"'{text}' → {t['name']}{when} · {t.get('region') or '지역 미상'}", data)


def search_datasets(query: str) -> ToolOut:
    from ..catalog.catalog import load_catalog

    cat = load_catalog()
    found = cat.search(str(query or ""), limit=6, include_hub=True)
    if not found:
        return ToolOut("관련 데이터 없음. 더 일반적인 단어(예: 카드, 유동인구, 버스, 날씨)로 다시 찾아보세요.",
                       f"'{query}' → 0종", [], [])
    rows, data = [], []
    for ds in found:
        data.append({"id": ds.id, "name": ds.name, "period": ds.period, "grade": _grade(ds)})
        rows.append(f"- [{ds.id}] {ds.name} | {ds.period or '-'} | {ds.update or '-'} 갱신 | {_grade(ds)} | "
                    f"{(ds.description or '').split('.')[0][:70]}")
    return ToolOut("\n".join(rows), f"'{query}' → {len(found)}종 ({', '.join(d.id for d in found[:4])}{'…' if len(found) > 4 else ''})",
                   data, [d.id for d in found])


def get_dataset(did: str) -> ToolOut:
    from ..catalog.catalog import load_catalog, normalize_id

    cat = load_catalog()
    try:
        ds = cat.get(normalize_id(str(did or "")))
    except KeyError:
        return ToolOut(f"'{did}' 데이터 없음. search_datasets로 ID를 먼저 확인하세요.", f"{did} → 없음", None, [])
    cols = ", ".join(f"{c.kor or c.name}" for c in ds.columns[:16])
    text = (f"[{ds.id}] {ds.name}\n제공: {ds.provider or ('제주데이터허브' if ds.is_hub else '제주빅데이터센터')}\n"
            f"기간: {ds.period or '-'} / 갱신: {ds.update or '-'} / 단위: {ds.grain}\n반출 등급: {_grade(ds)}\n"
            f"주요 컬럼: {cols}\n설명: {(ds.description or '')[:240]}")
    return ToolOut(text, f"{ds.id} {ds.name} · {_grade(ds)}",
                   {"id": ds.id, "name": ds.name, "period": ds.period, "grade": _grade(ds)}, [ds.id])


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        for k in ("value", "description", "text", "query"):
            if k in v and v[k] is not None:
                v = v[k]
                break
        if isinstance(v, (dict, list)):
            v = str(v)
    return v


def run_tool(name: str, args: dict) -> ToolOut:
    args = args if isinstance(args, dict) else {}
    args = {k: _plain(v) for k, v in args.items()}
    try:
        if name == "find_region":
            return find_region(args.get("text", ""))
        if name == "find_industry":
            return find_industry(args.get("text", ""))
        if name == "find_festival":
            y = args.get("year")
            try:
                y = int(y) if y else None
            except (TypeError, ValueError):
                y = None
            return find_festival(args.get("text", ""), y)
        if name == "search_datasets":
            return search_datasets(args.get("query", ""))
        if name == "get_dataset":
            return get_dataset(args.get("id", ""))
    except Exception as e:  # 도구 오류는 Agent에게 알려 다른 방법을 고르게 한다
        return ToolOut(f"도구 오류: {type(e).__name__}", f"오류({type(e).__name__})", None)
    return ToolOut(f"알 수 없는 도구 '{name}'", "알 수 없는 도구", None)


def tools_catalog_text() -> str:
    return "\n".join(f"- {t['name']}: {t['description']} / arguments 예: "
                     + json.dumps(t["example"], ensure_ascii=False) for t in PLAN_TOOLS)


def dataset_card(did: str) -> dict | None:
    """화면용 데이터 카드(탐색 결과)."""
    from ..catalog.catalog import load_catalog, normalize_id

    try:
        ds = load_catalog().get(normalize_id(did))
    except KeyError:
        return None
    return {"id": ds.id, "name": ds.name, "period": ds.period or "-", "update": ds.update or "-", "grade": _grade(ds),
            "hub": ds.is_hub, "columns": [c.kor or c.name for c in ds.columns[:10]],
            "desc": (ds.description or "").split(".")[0][:120]}
