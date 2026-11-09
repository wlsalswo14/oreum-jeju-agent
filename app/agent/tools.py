"""후속 질문 에이전트의 도구(Tool use).

이용자가 결과를 본 뒤 "한림읍이랑 비교해 줘", "업종을 한식으로 바꿔 보면?" 같은 질문을 하면
Claude가 아래 도구를 스스로 골라 호출하고, 오름이 **센터 PC 안에서** 계산한 뒤
**반출 검사를 통과한 결과만** 돌려준다(원본·차단 표는 도구 결과에도 포함되지 않음).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..analysis import event_impact, trade_area, transit
from ..guard import export_guard as eg
from ..guard.llm_gate import facts_from_results

TOOLS = [
    {
        "name": "get_table",
        "description": "이번 분석에서 만든 집계표(반출 검사 통과본)를 표 키로 조회한다. 근거 표의 전체 행이 필요할 때 사용.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {"key": {"type": "string", "description": "표 키. 예: trade.week, event.lift"}},
                         "required": ["key"], "additionalProperties": False},
    },
    {
        "name": "compare_regions",
        "description": "같은 업종에 대해 여러 지역(법정동·읍면)의 분석 기간(가장 최근 12개월 자료) 매출 규모·전년 대비·건당 결제액·관광객 비중·동종 점포 수를 비교한다. 창업·상권 분석에서만 사용.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {
            "regions": {"type": "array", "items": {"type": "string"}, "description": "비교할 지역 이름 2~6개"}},
            "required": ["regions"], "additionalProperties": False},
    },
    {
        "name": "rerun_analysis",
        "description": "지역·업종·비교지역·행사 기간 중 일부를 바꿔 같은 종류의 분석을 다시 계산하고 핵심 지표를 돌려준다. 바꾸지 않을 항목은 빈 문자열.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {
            "region": {"type": "string"}, "industry": {"type": "string"}, "control_region": {"type": "string"},
            "event_start": {"type": "string", "description": "YYYY-MM-DD"}, "event_end": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["region", "industry", "control_region", "event_start", "event_end"], "additionalProperties": False},
    },
    {
        "name": "explain_method",
        "description": "지표 계산 방법·데이터 한계·반출 기준을 설명하는 문서를 돌려준다.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {"topic": {"type": "string", "enum": [
            "상권 매력도 지수", "요일 지수", "시간당 매출 지수", "이중차분", "추정 추가 소비액", "첨두 집중률", "반출 기준", "데이터 한계"]}},
            "required": ["topic"], "additionalProperties": False},
    },
]

METHODS = {
    "상권 매력도 지수": "업종 내 법정동별로 시장규모(최근 12개월 카드매출의 로그) 30%, 성장성(전년 대비 증감률, -50~100% 구간) 25%, "
                  "점포당 매출(카드매출/허브 상가정보 동종 점포 수의 로그) 25%, 관광 수요(내국인·외국인 관광 결제 비중) 20%를 "
                  "각각 지역 간 최소-최대 정규화(0~100)한 뒤 가중합. 상대 지표이므로 같은 업종 안에서만 비교 가능.",
    "요일 지수": "요일별 매출을 분석 기간에 포함된 그 요일의 날짜 수로 나눠 '하루 평균'을 구하고, 월~일 평균을 100으로 둔 지수. "
              "공휴일 행은 원래 요일에도 중복 집계되므로(센터 답변) 공휴일 지수는 참고용.",
    "시간당 매출 지수": "시간대 매출을 시간대 길이(오전 07~11시 4h, 점심 11~14시 3h, 오후 14~18시 4h, 저녁 18~22시 4h, 밤 22~07시 9h)로 나눈 뒤 평균=100.",
    "이중차분": "행사 지역 방문인구의 '행사기간 vs 직전 3주 같은 요일' 증가율에서, 같은 기간 비교 지역의 증가율을 뺀 값(%p). "
             "날씨·계절·요일처럼 두 지역에 공통으로 작용한 요인을 덜어낸 순효과 추정치.",
    "추정 추가 소비액": "업종별 (행사지역 전년 동월 대비 증감률 − 비교지역 증감률) × 행사지역 전년 동월 매출을 증가 업종만 합산. "
                 "카드는 월 단위·비씨카드 결제분이므로 실제 효과의 일부만 반영한 보수적 추정.",
    "첨두 집중률": "평일 시간대별 평균 승차 중 가장 많은 1시간의 승차가 하루 승차에서 차지하는 비율. 높을수록 특정 시간에 몰림.",
    "반출 기준": "제주빅데이터센터 반출 정책(2026.09.01): 카드·유동인구 등은 원본·발췌 반출 불가, 단순집계는 10 미만 수치가 없을 때 가능"
             "(범주처리·삭제처리·비식별), 응용집계(비율·증감률·지수)는 수치 포함 가능, 시각화는 PNG/JPG. 오름은 역산 가능한 셀까지 추가로 가린다.",
    "데이터 한계": "카드=비씨카드 결제분(전체 소비 아님), 유동인구=통신 신호 기반 추정(정각 측정), 카드 데이터는 월 단위, "
              "정류소 지역 배정은 좌표 반경 근사, 법정동→권역은 추정 매핑(CSV의 권역 값 우선).",
}


class ToolBox:
    def __init__(self, agent):
        self.a = agent

    # ------------------------------------------------------------------ AI 키 없이 쓰는 도구 라우터
    def rule_answer(self, question: str) -> dict:
        """API 키가 없을 때도 후속 질문을 같은 도구로 처리(키워드로 도구 선택)."""
        from ..catalog import regions as R

        q = question.strip()
        mode = self.a.cache.get("mode")
        used: list[dict] = []
        parts: list[str] = []
        topic = next((t for t, kws in [
            ("상권 매력도 지수", ["매력도"]), ("요일 지수", ["요일"]), ("시간당 매출 지수", ["시간당", "시간대"]),
            ("이중차분", ["이중차분", "순효과", "did"]), ("추정 추가 소비액", ["추가 소비", "소비액"]),
            ("첨두 집중률", ["첨두", "집중률"]), ("반출 기준", ["반출", "비공개", "10 미만"]), ("데이터 한계", ["한계", "믿을", "정확"]),
        ] if any(k in q.lower() for k in kws)), None)
        if topic and any(k in q for k in ("어떻게", "방법", "뜻", "란", "무엇", "뭐", "왜", "계산", "기준", "한계", "믿")):
            used.append({"tool": "explain_method", "input": {"topic": topic}})
            parts.append(f"[{topic}] " + self.run("explain_method", {"topic": topic}))
        regions = [m.name for m in R.resolve_region(q)[:6] if getattr(m, "level", "") != "island"]
        if mode == "trade_area" and regions and any(k in q for k in ("비교", "랑", "와", "과", "vs", "대비", "어디")):
            cur = self.a.cache["rv"]["region_sel"].label
            regs = list(dict.fromkeys([cur] + regions))
            used.append({"tool": "compare_regions", "input": {"regions": regs}})
            parts.append(self.run("compare_regions", {"regions": regs}))
        elif regions and any(k in q for k in ("바꿔", "대신", "다시", "이면", "라면", "으로")):
            used.append({"tool": "rerun_analysis", "input": {"region": regions[0]}})
            parts.append(self.run("rerun_analysis", {"region": regions[0], "industry": "", "control_region": "",
                                                     "event_start": "", "event_end": ""}))
        if not parts:  # 키워드에 맞는 표를 찾아 보여 줌
            gmap = self.a.cache.get("gmap", {})
            stop = {"보여", "보여줘", "줘", "알려", "알려줘", "주세요", "해줘", "표", "좀", "어때", "어떤가요", "자료"}
            words = [w.rstrip("은는이가을를의도요줘") for w in q.replace("?", " ").split()]
            words = [w for w in words if len(w) >= 2 and w not in stop]

            def score(g):
                return sum((w in g.table.title) * 2 + (w in g.table.key) for w in words)

            best = max(gmap.values(), key=score, default=None)
            if best is not None and score(best) > 0:
                used.append({"tool": "get_table", "input": {"key": best.table.key}})
                parts.append(self.run("get_table", {"key": best.table.key}))
        if not parts:
            return {"answer": "'한림읍이랑 비교해 줘', '매력도 지수는 어떻게 계산해?', '요일별 표 보여 줘'처럼 "
                              "비교·지표 설명·표 조회 질문에 답할 수 있습니다.", "tools": [], "mode": "rule"}
        return {"answer": "질문에 맞는 분석 도구 결과입니다.\n\n" + "\n\n".join(parts),
                "tools": used, "mode": "rule"}

    def run(self, name: str, args: dict) -> str:
        try:
            if name == "get_table":
                return self._get_table(args.get("key", ""))
            if name == "compare_regions":
                return self._compare(args.get("regions", []))
            if name == "rerun_analysis":
                return self._rerun(args)
            if name == "explain_method":
                return METHODS.get(args.get("topic", ""), "해당 주제 설명이 없습니다.")
            return f"알 수 없는 도구: {name}"
        except Exception as e:  # 도구 오류는 모델에 그대로 알려 다른 방법을 찾게 함
            return f"도구 실행 오류: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    def _get_table(self, key: str) -> str:
        gmap = self.a.cache.get("gmap", {})
        gr = gmap.get(key)
        if gr is None:
            return "해당 키의 표가 없습니다. 사용 가능한 키: " + ", ".join(gmap)
        if not gr.ok:
            return "이 표는 반출 기준을 충족하지 못해 제공할 수 없습니다."
        return facts_from_results([gr], max_rows=80).text

    def _compare(self, regions: list[str]) -> str:
        from . import resolve

        c = self.a.cache
        if c.get("mode") != "trade_area":
            return "지역 비교는 창업·상권 분석에서만 사용할 수 있습니다."
        card, src = trade_area.card_frame(c["data"])
        if card is None:
            return "카드 데이터가 없어 비교할 수 없습니다."
        ind = c["rv"]["industry_sel"]
        biz = card[card["biz_cd"] == ind.code]
        months = sorted(biz["ym"].dropna().unique().tolist())
        if not months:
            return "해당 업종 데이터가 없습니다."
        last = months[-1]
        from ..analysis.common import pct, ym_add, ym_label

        last12 = [m for m in months if m > ym_add(last, -12)]
        prev12 = [ym_add(m, -12) for m in last12]
        has_prev = all(p in months for p in prev12)
        stores = c.get("pub", {}).get("stores")
        rows = []
        for text in regions[:6]:
            sel = resolve.region_sel_from_text(text)
            if sel is None:
                rows.append({"region": text, "amt": np.nan, "cnt": np.nan, "growth": np.nan, "ticket": np.nan,
                             "tour": np.nan, "stores": np.nan})
                continue
            cur = biz[biz["bjd"].isin(sel.bjd) & biz["ym"].isin(last12)]
            amt, cnt = cur["use_amt"].sum(), cur["use_cnt"].sum()
            prev = biz[biz["bjd"].isin(sel.bjd) & biz["ym"].isin(prev12)]["use_amt"].sum() if has_prev else np.nan
            tour = cur[cur["user_type"].isin(trade_area.TOURIST_TYPES) & (cur["tour_type"] != "도민")]["use_amt"].sum()
            n_st = resolve.count_stores(stores, sel, ind) if stores is not None else np.nan
            rows.append({"region": sel.label, "amt": amt, "cnt": cnt, "growth": pct(amt, prev) if has_prev else np.nan,
                         "ticket": amt / cnt if cnt else np.nan, "tour": tour / amt * 100 if amt else np.nan, "stores": n_st})
        df = pd.DataFrame(rows)
        t = eg.AggTable(
            key="tool.compare", title=f"{ind.name} 지역 비교({ym_label(last12[0])}~{ym_label(last)} 12개월)",
            df=df[["region", "amt", "growth", "ticket", "tour", "stores"]],
            dims=["region"], values=["amt", "growth", "ticket", "tour", "stores"],
            kind={"amt": "simple", "growth": "applied", "ticket": "applied", "tour": "applied", "stores": "simple"},
            sources=[src, "hub:793"], col_sources={"stores": ["hub:793"], "amt": [src], "growth": [src], "ticket": [src], "tour": [src]},
            unit={"amt": "원", "growth": "%", "ticket": "원", "tour": "%", "stores": "개"},
            labels={"region": "지역", "amt": "이용금액", "growth": "전년 대비", "ticket": "건당 결제액", "tour": "관광객 결제 비중", "stores": "동종 점포 수"},
            support=pd.DataFrame({k: df["cnt"].values for k in ["amt", "growth", "ticket", "tour"]}), support_only=["amt"],
        )
        return facts_from_results([eg.check(t)]).text or "반출 기준을 충족하는 비교 결과가 없습니다."

    def _rerun(self, args: dict) -> str:
        from . import resolve
        from .planner import Plan

        c = self.a.cache
        plan = Plan(**{k: v for k, v in self.a.st["plan"].items() if k in Plan.__dataclass_fields__})
        for k in ("region", "industry", "control_region", "event_start", "event_end"):
            if args.get(k):
                setattr(plan, k, args[k])
        rv = resolve.resolve_plan(plan, self.a.st.get("question", ""), explicit=True)
        data, pub = c["data"], c["pub"]
        stops, usage, bus_src, stops_src = resolve.bus_inputs(data, pub)
        if plan.mode == "trade_area":
            res = trade_area.analyze(data, rv["region_sel"], rv["industry_sel"], stores=pub.get("stores"),
                                     stops=stops, bus_usage=usage, bus_src=bus_src)
        elif plan.mode == "event_impact":
            ev = rv["event"]
            res = event_impact.analyze(data, rv["region_sel"], date.fromisoformat(ev["start"]), date.fromisoformat(ev["end"]),
                                       ev["name"], control=rv.get("control_sel"), stops=stops, bus_usage=usage,
                                       venue=tuple(ev["venue"]) if ev.get("venue") else None, bus_src=bus_src)
        else:
            res = transit.analyze(data, rv.get("region_sel"), stops, usage, src=bus_src, stops_src=stops_src)
        results = eg.check_all(res.tables)
        kpi = self.a._kpi_values(res, {r.table.key: r for r in results})
        cat = {k: v for k, v in res.facts.items() if not k.startswith("_") and not isinstance(v, float)}
        head = f"[재분석] {res.title}\n핵심 지표: " + "; ".join(f"{k['label']} {k['value']}" for k in kpi)
        head += "\n범주형 사실: " + "; ".join(f"{k}={v}" for k, v in cat.items())
        if res.notes:
            head += "\n주의: " + " / ".join(res.notes)
        keep = [r for r in results if r.table.key.endswith(("kpi_a", "kpi_s", "lift", "card", "week", "time", "kpi"))]
        return head + "\n\n" + facts_from_results(keep, max_rows=20).text
