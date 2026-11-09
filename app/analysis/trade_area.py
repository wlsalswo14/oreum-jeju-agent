"""① 창업·상권 분석.

질문 예: "애월읍에 카페 창업하려는데 상권 어때?"
입력: 카드 이용 현황(읍면동/요일별/시간대) + 유동인구 + 버스 + 허브 상가정보(경쟁 점포)
출력: 매출 추이·요일/시간 패턴·고객 구성·경쟁·유동인구·접근성·상권 매력도 지수(모두 반출 검사 대상 집계표)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..guard.export_guard import AggTable, Rollup
from .calendar_kr import day_counts
from .common import (
    AGE_LABEL, AGE_ROLLUP, TIME_DESC, TIME_ORDER, USER_GROUP, AnalysisResult, ChartSpec, IndustrySel,
    KPI, RegionSel, haversine_km, minmax_score, num, pct, quarter_of, ym_add, ym_int, ym_label,
)

TOURIST_TYPES = {"내국인", "중국", "일본", "동남아", "기타외국"}
TIME_HOURS = {"오전": 4, "점심": 3, "오후": 4, "저녁": 4, "밤": 9}
_HUB_CARD_RENAME = {
    "년월": "base_year_month", "읍면동명": "bjd", "업종코드": "biz_cd", "업종명": "biz_nm",
    "이용자 구분": "user_type", "이용자구분": "user_type", "관광구분": "tour_type", "연령대": "age",
    "성별": "sex", "이용금액": "use_amt", "이용건수": "use_cnt", "이용자수": "user_cnt",
}


def _clean_str(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    return s.where(~s.isin(["nan", "NA", "None", "<NA>", ""]), None)


def card_frame(data: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame | None, str | None]:
    """월별 법정동×업종 카드 데이터 표준화. 우선순위: 1-08(v02) > 1-07(v01) > 허브 매쉬업(746)."""
    for src in ("1-08", "1-07"):
        if src in data and len(data[src]):
            df = data[src].copy()
            df["ym"] = ym_int(df["base_year_month"])
            for c in ("user_cnt", "use_cnt", "use_amt"):
                df[c] = num(df[c]) if c in df else np.nan
            for c in ("user_type", "tour_type", "age", "sex", "bjd", "biz_cd", "biz_nm"):
                if c not in df:
                    df[c] = np.nan
                df[c] = _clean_str(df[c])
            df["age"] = df["age"].str.replace(r"\.0$", "", regex=True)
            return df, src
    for key in list(data):
        if key.startswith("hub:746"):
            df = data[key].rename(columns=_HUB_CARD_RENAME).copy()
            df["ym"] = ym_int(df["base_year_month"] if "base_year_month" in df else df["ym"])
            for c in ("user_cnt", "use_cnt", "use_amt"):
                df[c] = num(df[c]) if c in df else np.nan
                if c != "use_amt" and (df[c].fillna(0) == 0).all():
                    df[c] = np.nan   # 허브 매쉬업에 없는 항목(0으로 채워짐) → 결측 처리
            for c in ("user_type", "tour_type", "age", "sex", "bjd", "biz_cd", "biz_nm"):
                if c not in df:
                    df[c] = np.nan
                df[c] = _clean_str(df[c])
            df["age"] = df["age"].str.replace(r"\.0$", "", regex=True)
            return df, key
    return None, None


def analyze(data: dict[str, pd.DataFrame], region: RegionSel, ind: IndustrySel,
            stores: pd.DataFrame | None = None, stops: pd.DataFrame | None = None,
            bus_usage: pd.DataFrame | None = None, bus_src: str = "3-04") -> AnalysisResult:
    res = AnalysisResult(
        mode="trade_area",
        title=f"{region.label} {ind.label or ind.name} 창업·상권 분석",
        params={"region": region.label, "bjd": region.bjd, "industry": ind.name, "biz_cd": ind.code},
    )
    card, csrc = card_frame(data)
    if card is None:
        res.notes.append("카드 이용 현황(읍면동) 데이터가 없어 매출 분석을 생략했습니다.")
    else:
        _card_tables(res, card, csrc, region, ind)
    if "1-12" in data:
        _week_table(res, data["1-12"], region, ind)
    elif "1-11" in data:
        _week_table(res, data["1-11"], region, ind, src="1-11")
    else:
        res.notes.append("요일별 카드 데이터(1-12)가 없어 요일 패턴 분석을 생략했습니다.")
    if "1-14" in data:
        _time_table(res, data["1-14"], region, ind)
    elif "1-13" in data:
        _time_table(res, data["1-13"], region, ind, src="1-13")
    else:
        res.notes.append("시간대별 카드 데이터(1-14)가 없어 시간대 분석을 생략했습니다.")
    if stores is not None and len(stores):
        _competition_table(res, stores, card, csrc, region, ind)
    pop_src = "4-11" if "4-11" in data else ("4-10" if "4-10" in data else None)
    if pop_src:
        _population_table(res, data[pop_src], region, pop_src)
    else:
        if "hub:583" in data:
            _population_583(res, data["hub:583"], region)
        if "hub:748" in data:
            _population_748(res, data["hub:748"], region, ind, with_trend="hub:583" not in data)
        if "hub:583" not in data and "hub:748" not in data:
            res.notes.append("유동인구(읍면동) 데이터가 없어 유동인구 분석을 생략했습니다.")
    if stops is not None and bus_usage is not None and len(stops) and len(bus_usage):
        _transit_access(res, stops, bus_usage, region, bus_src)
    _attractiveness(res, region)
    return res


# ---------------------------------------------------------------------------------------
def _recompute_monthly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["avg_ticket"] = df["use_amt"] / df["use_cnt"].replace(0, np.nan)
    if "yoy" in df.columns or True:
        lab = df["ym"].astype(str)
        prev = {}
        for v, a in zip(lab, df["use_amt"]):
            prev[v] = a
        yo = []
        for v, a in zip(lab, df["use_amt"]):
            if "Q" in v:
                y, q = v.split("Q")
                p = prev.get(f"{int(y) - 1}Q{q}")
            else:
                p = prev.get(str(ym_add(int(v), -12))) if v.isdigit() else None
            yo.append(pct(a, p) if p else np.nan)
        df["yoy"] = yo
    return df


def _card_tables(res: AnalysisResult, card: pd.DataFrame, src: str, region: RegionSel, ind: IndustrySel):
    res.used_sources.append(src)
    if src.startswith("hub:"):
        res.notes.append("센터 카드 데이터(1-08)가 없어 매출 지표는 제주데이터허브 공개 매쉬업(비씨카드, 2017.01~2021.05) 기준입니다. "
                         "최신 수치는 센터에서 「카드 이용 현황(읍면동)_v02」를 넣어 다시 분석하세요.")
        res.facts["card_source"] = "허브 공개 매쉬업(2017~2021)"
    biz = card[card["biz_cd"] == ind.code]
    if biz.empty:
        res.notes.append(f"카드 데이터에 업종 '{ind.name}'({ind.code}) 행이 없습니다.")
        return
    months = sorted(biz["ym"].dropna().unique().tolist())
    last = months[-1]
    last12 = [m for m in months if m > ym_add(last, -12)]
    prev12 = [ym_add(m, -12) for m in last12]
    has_prev = all(p in months for p in prev12)
    res.params["period"] = f"{ym_label(last12[0])}~{ym_label(last)}"
    tgt = biz[biz["bjd"].isin(region.bjd)]
    if tgt.empty:
        res.notes.append(f"카드 데이터에 '{region.label}'({', '.join(region.bjd)}) 행이 없습니다.")
        return

    # T1 월별 매출 추이
    m = tgt.groupby("ym")[["use_amt", "use_cnt"]].sum(min_count=1).reset_index().sort_values("ym")   # 건수가 없는 달은 0이 아니라 결측
    m = _recompute_monthly(m)
    has_cnt = m["use_cnt"].notna().any() and m["use_cnt"].sum() > 0
    t1 = AggTable(
        key="trade.monthly", title=f"{region.label} {ind.name} 월별 카드 매출 추이",
        df=m[["ym", "use_amt", "use_cnt", "avg_ticket", "yoy"]].copy(), dims=["ym"],
        values=["use_amt", "use_cnt", "avg_ticket", "yoy"],
        kind={"use_amt": "simple", "use_cnt": "simple", "avg_ticket": "applied", "yoy": "applied"},
        sources=[src], unit={"use_amt": "원", "use_cnt": "건", "avg_ticket": "원", "yoy": "%"},
        labels={"ym": "연월", "use_amt": "이용금액", "use_cnt": "이용건수", "avg_ticket": "건당 결제액", "yoy": "전년동월 대비"},
        support=pd.DataFrame({"use_amt": m["use_cnt"].values}) if has_cnt else None,
        support_only=["use_amt"], derived={"avg_ticket": ["use_amt", "use_cnt"], "yoy": ["use_amt"]},
        margins=["ym"], rollups=[Rollup("ym", quarter_of, "월 → 분기")], recompute=_recompute_monthly,
        note="비씨카드 결제 기준(전체 소비가 아닌 표본). 이용금액 단위 원.",
    )
    res.tables.append(t1)
    res.charts.append(ChartSpec("c.monthly", "trade.monthly", "bar", f"{region.label} {ind.label or ind.name} 월별 매출",
                                x="ym", y=["use_amt"], y_label="이용금액(원)"))

    # 비교지역 지표(업종 내 법정동 비교) — 응용집계만
    cur = biz[biz["ym"].isin(last12)]
    g = cur.groupby("bjd").agg(amt=("use_amt", "sum"), cnt=("use_cnt", "sum"))
    tour = cur[cur["user_type"].isin(TOURIST_TYPES) & (cur["tour_type"] != "도민")].groupby("bjd")["use_amt"].sum()
    g["tour_share"] = (tour.reindex(g.index).fillna(0) / g["amt"].replace(0, np.nan)) * 100
    if has_prev:
        p = biz[biz["ym"].isin(prev12)].groupby("bjd")["use_amt"].sum()
        g["growth"] = [pct(a, p.get(i)) for i, a in zip(g.index, g["amt"])]
    else:
        g["growth"] = np.nan
    g["ticket"] = g["amt"] / g["cnt"].replace(0, np.nan)
    pos = g[g["amt"] > 0]
    g["sales_idx"] = g["amt"] / pos["amt"].mean() * 100 if len(pos) else np.nan
    g["rank"] = g["amt"].rank(ascending=False, method="min")
    g = g.reset_index()
    res.facts["n_regions"] = int((g["amt"] > 0).sum())
    peers = g.sort_values("amt", ascending=False)
    if region.peer_bjd:
        peers = peers[peers["bjd"].isin(region.peer_bjd + region.bjd)]
    top = peers.head(10)
    tgt_rows = g[g["bjd"].isin(region.bjd)]
    show = pd.concat([top, tgt_rows]).drop_duplicates("bjd")
    vcols = [c for c in ["sales_idx", "growth", "ticket", "tour_share", "rank"] if show[c].notna().any()]  # 자료에 없는 지표(예: 허브 자료의 건당 결제액)는 뺌
    cmp_df = show[["bjd"] + vcols].reset_index(drop=True)
    sup = show[["cnt"]].reset_index(drop=True)
    t2 = AggTable(
        key="trade.compare", title=f"{ind.name} 지역별 비교({res.params.get('period') or '최근 12개월'}, 매출 상위 지역 + 대상 지역)",
        df=cmp_df, dims=["bjd"], values=vcols,
        kind={c: "applied" for c in vcols}, sources=[src],
        unit={k: v for k, v in {"sales_idx": "점", "growth": "%", "ticket": "원", "tour_share": "%", "rank": "위"}.items() if k in vcols},
        labels={"bjd": "법정동", "sales_idx": "매출지수(평균=100)", "growth": "전년 대비 증감률",
                "ticket": "건당 결제액", "tour_share": "관광객 결제 비중", "rank": "매출 순위"},
        support=pd.DataFrame({c: sup["cnt"].values for c in vcols})
        if sup["cnt"].notna().any() else None,
        note="매출지수 = 지역 매출 / 해당 업종 매출 발생 지역 평균 × 100 (응용집계).",
    )
    res.tables.append(t2)
    res.charts.append(ChartSpec("c.compare", "trade.compare", "barh", f"{ind.label or ind.name} 지역별 매출지수",
                                x="bjd", y=["sales_idx"], sort=True, extra={"highlight_rows": region.bjd}))

    # KPI(단순/응용 분리)
    tg = g[g["bjd"].isin(region.bjd)]
    amt12 = float(tg["amt"].sum())
    cnt12 = float(tg["cnt"].sum()) if tg["cnt"].notna().any() else np.nan
    kpi_s = pd.DataFrame({"지표": ["분석 기간 12개월 이용금액(원)", "월평균 이용건수(건)"],
                          "value": [amt12, cnt12 / max(1, len(last12)) if cnt12 == cnt12 else np.nan]})
    res.tables.append(AggTable(
        key="trade.kpi_s", title="핵심 지표(단순집계)", df=kpi_s, dims=["지표"], values=["value"],
        kind={"value": "simple"}, sources=[src], unit={"value": ""}, labels={"value": "값"},
        support=pd.DataFrame({"value": [cnt12, cnt12]}) if cnt12 == cnt12 else None,
    ))
    prev_amt = float(biz[biz["ym"].isin(prev12) & biz["bjd"].isin(region.bjd)]["use_amt"].sum()) if has_prev else np.nan
    tour_amt = float(tour.reindex(region.bjd).fillna(0).sum())
    rank_val = float(g.set_index("bjd").loc[region.bjd[0], "rank"]) if region.bjd[0] in set(g["bjd"]) else np.nan
    kpi_a = pd.DataFrame({
        "지표": ["전년 대비 증감률(%)", "건당 결제액(원)", "관광객 결제 비중(%)", "업종 내 매출 순위(위)"],
        "value": [pct(amt12, prev_amt) if has_prev else np.nan,
                  amt12 / cnt12 if cnt12 and cnt12 == cnt12 else np.nan,
                  tour_amt / amt12 * 100 if amt12 else np.nan, rank_val],
    })
    res.tables.append(AggTable(
        key="trade.kpi_a", title="핵심 지표(응용집계)", df=kpi_a, dims=["지표"], values=["value"],
        kind={"value": "applied"}, sources=[src], unit={"value": ""}, labels={"value": "값"},
        support=pd.DataFrame({"value": [cnt12] * 4}) if cnt12 == cnt12 else None,
    ))
    res.kpis += [
        KPI("12개월 매출" if src.startswith("hub:") else "최근 12개월 매출", "trade.kpi_s", {"지표": "분석 기간 12개월 이용금액(원)"}, "value", "원"),
        KPI("전년 대비", "trade.kpi_a", {"지표": "전년 대비 증감률(%)"}, "value", "%±"),
        KPI("건당 결제액", "trade.kpi_a", {"지표": "건당 결제액(원)"}, "value", "원"),
        KPI("관광객 결제 비중", "trade.kpi_a", {"지표": "관광객 결제 비중(%)"}, "value", "%"),
        KPI("업종 내 매출 순위", "trade.kpi_a", {"지표": "업종 내 매출 순위(위)"}, "value", "위"),
    ]

    # T5 고객 구성
    cust = cur[cur["bjd"].isin(region.bjd)].copy()
    cust["group"] = cust["user_type"].map(USER_GROUP).fillna("기타")
    cust.loc[(cust["user_type"] == "내국인") & (cust["tour_type"] == "도민"), "group"] = "제주도민"
    cg = cust.groupby("group").agg(amt=("use_amt", "sum"), cnt=("use_cnt", "sum")).reset_index()
    cg["share"] = cg["amt"] / cg["amt"].sum() * 100 if cg["amt"].sum() else np.nan
    res.tables.append(AggTable(
        key="trade.customer", title=f"{region.label} {ind.name} 고객 구성(결제금액 비중)",
        df=cg[["group", "share"]], dims=["group"], values=["share"], kind={"share": "applied"}, sources=[src],
        unit={"share": "%"}, labels={"group": "고객 구분", "share": "결제금액 비중"},
        support=pd.DataFrame({"share": cg["cnt"].values}) if cg["cnt"].notna().any() else None,
        margins=["group"],
    ))
    res.charts.append(ChartSpec("c.customer", "trade.customer", "donut", "고객 구성(결제금액 비중)", x="group", y=["share"]))
    if len(cg):
        res.facts["top_customer"] = str(cg.sort_values("share", ascending=False).iloc[0]["group"])

    demo = cust[cust["age"].isin(list(AGE_LABEL)) & cust["sex"].isin(["남성", "여성"])]
    if len(demo):
        dg = demo.groupby(["age", "sex"]).agg(amt=("use_amt", "sum"), cnt=("use_cnt", "sum")).reset_index()
        dg["share"] = dg["amt"] / dg["amt"].sum() * 100
        dg["age"] = dg["age"].map(lambda a: AGE_LABEL.get(a, a))
        res.tables.append(AggTable(
            key="trade.demo", title=f"{region.label} {ind.name} 연령·성별 결제 비중",
            df=dg[["age", "sex", "share"]], dims=["age", "sex"], values=["share"], kind={"share": "applied"},
            sources=[src], unit={"share": "%"}, labels={"age": "연령대", "sex": "성별", "share": "결제금액 비중"},
            support=pd.DataFrame({"share": dg["cnt"].values}) if dg["cnt"].notna().any() else None,
            margins=["age"],
            rollups=[Rollup("age", lambda a: {v: AGE_ROLLUP[k] for k, v in AGE_LABEL.items()}.get(a, a), "연령 10세 → 20세 단위")],
            rollup_agg={"share": "sum"},
        ))
        res.charts.append(ChartSpec("c.demo", "trade.demo", "stacked", "연령·성별 결제 비중", x="age", y=["share"], series="sex"))
        top = dg.sort_values("share", ascending=False).iloc[0]
        res.facts["top_demo"] = f"{top['age']} {top['sex']}"
    _foreign_table(res, cust, src, region, ind)
    res.facts["_card"] = {"g": g, "has_prev": has_prev}


FOREIGN = ["중국", "일본", "동남아", "기타외국"]


def _foreign_table(res: AnalysisResult, cust: pd.DataFrame, src: str, region: RegionSel, ind: IndustrySel):
    """외국인 고객 국적별 분기 결제 — 소규모 셀이 생기기 쉬워 반출 기준(범주처리)이 자주 적용되는 표."""
    f = cust[cust["user_type"].isin(FOREIGN)].copy()
    if f.empty or f["use_cnt"].isna().all():
        return
    f["quarter"] = f["ym"].map(quarter_of)
    g = f.groupby(["quarter", "user_type"]).agg(use_amt=("use_amt", "sum"), use_cnt=("use_cnt", "sum")).reset_index()
    full = pd.MultiIndex.from_product([sorted(g["quarter"].unique()), FOREIGN], names=["quarter", "user_type"])
    g = g.set_index(["quarter", "user_type"]).reindex(full).reset_index()
    g = g[g.groupby("user_type")["use_cnt"].transform(lambda s: s.notna().any())]  # 기간 내 한 번도 없는 국적 제외
    g[["use_amt", "use_cnt"]] = g[["use_amt", "use_cnt"]].fillna(0)
    res.tables.append(AggTable(
        key="trade.foreign", title=f"{region.label} {ind.name} 외국인 고객 국적별 분기 결제",
        df=g[["quarter", "user_type", "use_cnt", "use_amt"]].reset_index(drop=True), dims=["quarter", "user_type"],
        values=["use_cnt", "use_amt"], kind={"use_cnt": "simple", "use_amt": "simple"}, sources=[src],
        unit={"use_cnt": "건", "use_amt": "원"},
        labels={"quarter": "분기", "user_type": "국적", "use_cnt": "이용건수", "use_amt": "이용금액"},
        support=pd.DataFrame({"use_amt": g["use_cnt"].values}), support_only=["use_amt"], margins=["user_type"],
        rollups=[
            Rollup("user_type", {"동남아": "동남아·기타", "기타외국": "동남아·기타"}, "국적: 동남아·기타외국 통합"),
            Rollup("user_type", lambda v: "외국인 합계", "국적 → 외국인 합계"),
        ],
        note="외국인 결제는 표본이 작아 반출 기준(10 미만)에 따라 국적을 묶거나 일부 값을 비공개로 처리합니다.",
    ))
    res.charts.append(ChartSpec("c.foreign", "trade.foreign", "stacked", "외국인 고객 국적별 분기 이용건수",
                                x="quarter", y=["use_cnt"], series="user_type", y_label="건"))


def _week_table(res: AnalysisResult, df: pd.DataFrame, region: RegionSel, ind: IndustrySel, src: str = "1-12"):
    d = df.copy()
    d["ym"] = ym_int(d["base_year_month"])
    d = d[(d["biz_cd"].astype(str) == ind.code) & (d["bjd"].astype(str).isin(region.bjd))] if "bjd" in d else d[d["biz_cd"].astype(str) == ind.code]
    if d.empty:
        res.notes.append("요일별 카드 데이터에 대상 지역·업종 행이 없습니다.")
        return
    res.used_sources.append(src)
    months = sorted(d["ym"].dropna().unique().tolist())[-12:]
    d = d[d["ym"].isin(months)]
    d["use_amt"] = num(d["use_amt"])
    d["use_cnt"] = num(d["use_cnt"])
    g = d.groupby("week").agg(amt=("use_amt", "sum"), cnt=("use_cnt", "sum"))
    days = day_counts(months)
    g["per_day"] = [a / max(1, days.get(w, 0)) for w, a in zip(g.index, g["amt"])]
    base = g.loc[[w for w in g.index if w != "공휴일"], "per_day"].mean()
    g["idx"] = g["per_day"] / base * 100 if base else np.nan
    wk = g.loc[[w for w in g.index if w != "공휴일"]]
    g["share"] = np.nan
    g.loc[wk.index, "share"] = wk["amt"] / wk["amt"].sum() * 100
    order = [w for w in ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"] if w in g.index]
    g = g.loc[order].reset_index()
    res.tables.append(AggTable(
        key="trade.week", title=f"{region.label} {ind.name} 요일별 하루 평균 매출 지수",
        df=g[["week", "idx", "share"]], dims=["week"], values=["idx", "share"],
        kind={"idx": "applied", "share": "applied"}, sources=[src], unit={"idx": "점", "share": "%"},
        labels={"week": "요일", "idx": "하루 평균 매출 지수(평균 요일=100)", "share": "요일 매출 비중(월~일)"},
        support=pd.DataFrame({"idx": g["cnt"].values, "share": g["cnt"].values}),
        note="공휴일은 원래 요일에도 중복 집계됨(센터 답변). 지수는 기간 내 해당 요일 일수로 나눈 하루 평균 기준.",
    ))
    res.charts.append(ChartSpec("c.week", "trade.week", "bar", "요일별 하루 평균 매출 지수", x="week", y=["idx"],
                                y_label="지수(평균=100)"))
    wk = g[g["week"] != "공휴일"]
    if len(wk):
        res.facts["peak_week"] = str(wk.sort_values("idx", ascending=False).iloc[0]["week"])
        we = wk[wk["week"].isin(["토요일", "일요일"])]["share"].sum()
        res.tables.append(AggTable(
            key="trade.weekend", title="주말(토·일) 매출 비중", df=pd.DataFrame({"구분": ["주말 매출 비중"], "value": [we]}),
            dims=["구분"], values=["value"], kind={"value": "applied"}, sources=[src], unit={"value": "%"},
            labels={"value": "값"},
        ))
        res.kpis.append(KPI("주말 매출 비중", "trade.weekend", {"구분": "주말 매출 비중"}, "value", "%"))


def _time_table(res: AnalysisResult, df: pd.DataFrame, region: RegionSel, ind: IndustrySel, src: str = "1-14"):
    d = df.copy()
    d["ym"] = ym_int(d["base_year_month"])
    if "bjd" in d:
        d = d[(d["biz_cd"].astype(str) == ind.code) & (d["bjd"].astype(str).isin(region.bjd))]
    else:
        d = d[d["biz_cd"].astype(str) == ind.code]
    if d.empty:
        res.notes.append("시간대별 카드 데이터에 대상 지역·업종 행이 없습니다.")
        return
    res.used_sources.append(src)
    months = sorted(d["ym"].dropna().unique().tolist())[-12:]
    d = d[d["ym"].isin(months)]
    d["use_amt"] = num(d["use_amt"])
    d["use_cnt"] = num(d["use_cnt"])
    g = d.groupby("time").agg(amt=("use_amt", "sum"), cnt=("use_cnt", "sum"))
    g = g.loc[[t for t in TIME_ORDER if t in g.index]]
    g["share"] = g["amt"] / g["amt"].sum() * 100
    hourly = g["amt"] / pd.Series({t: TIME_HOURS[t] for t in g.index})
    g["hour_idx"] = hourly / hourly.mean() * 100
    g = g.reset_index()
    g["time_label"] = g["time"].map(lambda t: f"{t}({TIME_DESC.get(t, '')})")
    res.tables.append(AggTable(
        key="trade.time", title=f"{region.label} {ind.name} 시간대별 매출 비중",
        df=g[["time_label", "share", "hour_idx"]], dims=["time_label"], values=["share", "hour_idx"],
        kind={"share": "applied", "hour_idx": "applied"}, sources=[src], unit={"share": "%", "hour_idx": "점"},
        labels={"time_label": "시간대", "share": "매출 비중", "hour_idx": "시간당 매출 지수"},
        support=pd.DataFrame({"share": g["cnt"].values, "hour_idx": g["cnt"].values}), margins=["time_label"],
    ))
    res.charts.append(ChartSpec("c.time", "trade.time", "bar", "시간대별 매출 비중", x="time_label", y=["share"], y_label="%"))
    if len(g):
        res.facts["peak_time"] = str(g.sort_values("hour_idx", ascending=False).iloc[0]["time_label"])


def _competition_table(res, stores: pd.DataFrame, card, csrc, region: RegionSel, ind: IndustrySel):
    s = stores
    if "bc_biz_cd" in s.columns and ind.code:
        s = s[s["bc_biz_cd"].astype(str) == ind.code]
    elif ind.store_keywords:
        pat = "|".join(map(_re_escape, ind.store_keywords))
        cols = [c for c in ("상권업종소분류명", "표준산업분류명", "상권업종중분류명", "sub", "std", "mid") if c in s.columns]
        m = np.zeros(len(s), dtype=bool)
        for c in cols:
            m |= s[c].astype(str).str.contains(pat, regex=True, na=False).values
        s = s[m]
    bjd_col = "법정동명" if "법정동명" in s.columns else ("bjd" if "bjd" in s.columns else None)
    if bjd_col is None or s.empty:
        return
    res.used_sources.append("hub:793")
    cnt = s.groupby(bjd_col).size().rename("store_cnt")
    df = cnt.reset_index().rename(columns={bjd_col: "bjd"})
    col_src = {"store_cnt": ["hub:793"]}
    values = ["store_cnt"]
    kind = {"store_cnt": "simple"}
    labels = {"bjd": "법정동", "store_cnt": "업종 점포 수(허브 상가정보)"}
    unit = {"store_cnt": "개"}
    sup = None
    cardinfo = res.facts.get("_card")
    if cardinfo is not None:
        g = cardinfo["g"].set_index("bjd")
        df["amt"] = df["bjd"].map(g["amt"])
        df["sps"] = df["amt"] / df["store_cnt"]
        mean_sps = df["sps"].replace([np.inf, -np.inf], np.nan).mean()
        df["sps_idx"] = df["sps"] / mean_sps * 100 if mean_sps else np.nan
        df["cnt"] = df["bjd"].map(g["cnt"])
        values.append("sps_idx")
        kind["sps_idx"] = "applied"
        col_src["sps_idx"] = [csrc]
        labels["sps_idx"] = "점포당 매출지수(평균=100)"
        unit["sps_idx"] = "점"
        sup = df[["cnt"]].rename(columns={"cnt": "sps_idx"})
    df = df.sort_values("store_cnt", ascending=False)
    top = df.head(12)
    show = pd.concat([top, df[df["bjd"].isin(region.bjd)]]).drop_duplicates("bjd").reset_index(drop=True)
    if sup is not None:
        sup = df.set_index("bjd").loc[show["bjd"], ["cnt"]].rename(columns={"cnt": "sps_idx"}).reset_index(drop=True)
    res.tables.append(AggTable(
        key="trade.competition", title=f"{ind.label or ind.name} 경쟁 점포 현황(법정동별)",
        df=show[["bjd"] + values], dims=["bjd"], values=values, kind=kind,
        sources=["hub:793"] + ([csrc] if csrc and "sps_idx" in values else []), col_sources=col_src,
        unit=unit, labels=labels, support=sup,
        note="점포 수: 제주데이터허브 「제주특별자치도 상가(상권) 정보」(공공데이터포털 원출처, 2025.09 기준) 업종 키워드 매칭.",
    ))
    res.charts.append(ChartSpec("c.competition", "trade.competition", "barh", "법정동별 경쟁 점포 수",
                                x="bjd", y=["store_cnt"], sort=True, extra={"highlight_rows": region.bjd}))
    tgt_cnt = int(df[df["bjd"].isin(region.bjd)]["store_cnt"].sum())
    res.facts["store_cnt"] = tgt_cnt
    res.kpis.append(KPI("경쟁 점포 수", "trade.competition", {"bjd": region.bjd[0]}, "store_cnt", "개"))
    res.facts["_stores"] = df
    # 지도: 대상 지역 반경 내 동종 점포(공개 데이터)
    lon_c = "경도" if "경도" in s.columns else ("lon" if "lon" in s.columns else None)
    lat_c = "위도" if "위도" in s.columns else ("lat" if "lat" in s.columns else None)
    if region.center and lon_c and lat_c:
        dist = haversine_km(s[lon_c].astype(float), s[lat_c].astype(float), region.center[0], region.center[1])
        near = s[dist <= max(region.radius_km, 3.0)].head(400)
        name_c = "상호명" if "상호명" in s.columns else None
        for _, r in near.iterrows():
            res.map_points.append({"type": "store", "lon": float(r[lon_c]), "lat": float(r[lat_c]),
                                   "name": str(r[name_c]) if name_c else ind.name})


def _population_table(res, df: pd.DataFrame, region: RegionSel, src: str):
    d = df
    if "emd_nm" not in d:
        return
    names = set(region.emd)
    d = d[d["emd_nm"].astype(str).isin(names)]
    if d.empty:
        res.notes.append(f"유동인구 데이터에 '{', '.join(region.emd)}' 행이 없습니다.")
        return
    res.used_sources.append(src)
    d = d.copy()
    for c in ("resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"):
        d[c] = num(d[c])
    d["date"] = pd.to_datetime(d["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    d["hour"] = num(d["time"]).astype("Int64")
    tot = d.groupby(["date", "hour"])[["resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"]].sum().reset_index()
    tot["day_type"] = np.where(tot["date"].dt.weekday >= 5, "주말", "평일")
    h = tot.groupby(["day_type", "hour"])[["resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"]].mean().reset_index()
    h = h.rename(columns={"resd_pop_cnt": "resd", "work_pop_cnt": "work", "visit_pop_cnt": "visit"})
    period = f"{tot['date'].min():%Y.%m.%d}~{tot['date'].max():%Y.%m.%d}"
    res.tables.append(AggTable(
        key="trade.population", title=f"{region.label} 시간대별 평균 유동인구({period})",
        df=h, dims=["day_type", "hour"], values=["visit", "resd", "work"],
        kind={"visit": "simple", "resd": "simple", "work": "simple"}, sources=[src],
        unit={"visit": "명", "resd": "명", "work": "명"},
        labels={"day_type": "구분", "hour": "시각", "visit": "방문인구", "resd": "거주인구", "work": "근무인구"},
        note="SKT 기반 추정 유동인구. 해당 시각 정각 측정 인구의 일평균.",
    ))
    res.charts.append(ChartSpec("c.population", "trade.population", "line", f"{region.label} 시간대별 방문인구(평일/주말)",
                                x="hour", y=["visit"], series="day_type", y_label="명"))
    vis = h[h["day_type"] == "주말"] if (h["day_type"] == "주말").any() else h
    if len(vis):
        res.facts["peak_visit_hour"] = int(vis.sort_values("visit", ascending=False).iloc[0]["hour"])
    res.facts["_pop_visit_mean"] = float(tot["visit_pop_cnt"].mean())


def _transit_access(res, stops: pd.DataFrame, usage: pd.DataFrame, region: RegionSel, src: str):
    if not region.center:
        return
    s = stops.copy()
    s["longitude"] = num(s["longitude"])
    s["latitude"] = num(s["latitude"])
    s = s.dropna(subset=["longitude", "latitude"]).drop_duplicates("bus_station_id")
    dist = haversine_km(s["longitude"], s["latitude"], region.center[0], region.center[1])
    s = s[dist <= region.radius_km]
    if s.empty:
        return
    u = usage[usage["bus_station_id"].astype(str).isin(s["bus_station_id"].astype(str))].copy()
    if u.empty:
        return
    res.used_sources += ["3-03", src]
    u["user_count"] = num(u["user_count"])
    u = u[u["get_on_off_type"].astype(str).isin(["승차", "환승"])]
    from .transit import latest_block

    u["date"] = pd.to_datetime(u["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    u = latest_block(u)
    n_days = max(1, u["date"].nunique())
    days_stop = u.groupby("bus_station_id")["date"].nunique()
    st = u.groupby(["bus_station_id", "bus_station_name"])["user_count"].sum().reset_index()
    st["daily"] = st["user_count"] / st["bus_station_id"].map(days_stop).clip(lower=1)
    st = st.sort_values("daily", ascending=False).head(10)
    res.tables.append(AggTable(
        key="trade.transit", title=f"{region.label} 반경 {region.radius_km:g}km 주요 정류소 일평균 승차",
        df=st[["bus_station_name", "daily"]].reset_index(drop=True), dims=["bus_station_name"], values=["daily"],
        kind={"daily": "simple"}, sources=["3-03", src], unit={"daily": "명"},
        labels={"bus_station_name": "정류소", "daily": "일평균 승차(환승 포함)"},
    ))
    res.facts["n_stops"] = int(len(s))
    res.facts["_bus_daily"] = float(u["user_count"].sum() / n_days)
    for _, r in s.iterrows():
        res.map_points.append({"type": "stop", "lon": float(r["longitude"]), "lat": float(r["latitude"]),
                               "name": str(r["bus_station_name"])})


def _attractiveness(res: AnalysisResult, region: RegionSel):
    cardinfo = res.facts.get("_card")
    if cardinfo is None:
        return
    g = cardinfo["g"].copy().set_index("bjd")
    g = g[g["amt"] > 0]
    if len(g) < 3:
        return
    stores = res.facts.get("_stores")
    comp = pd.Series(np.nan, index=g.index)
    if stores is not None:
        sc = stores.set_index("bjd")["store_cnt"]
        comp = (g["amt"] / sc.reindex(g.index)).replace([np.inf, -np.inf], np.nan)
    parts = pd.DataFrame(index=g.index)
    parts["시장규모"] = minmax_score(np.log1p(g["amt"]))
    parts["성장성"] = minmax_score(g["growth"].clip(-50, 100).fillna(g["growth"].median())) if g["growth"].notna().any() else 50.0
    parts["점포당 매출"] = minmax_score(np.log1p(comp.fillna(comp.median()))) if comp.notna().any() else 50.0
    parts["관광 수요"] = minmax_score(g["tour_share"].fillna(0))
    w = {"시장규모": 0.3, "성장성": 0.25, "점포당 매출": 0.25, "관광 수요": 0.2}
    parts["score"] = sum(parts[k] * v for k, v in w.items())
    parts["rank"] = parts["score"].rank(ascending=False, method="min")
    parts = parts.reset_index().rename(columns={"index": "bjd"})
    top = parts.sort_values("score", ascending=False).head(8)
    show = pd.concat([top, parts[parts["bjd"].isin(region.bjd)]]).drop_duplicates("bjd").reset_index(drop=True)
    cols = ["시장규모", "성장성", "점포당 매출", "관광 수요", "score", "rank"]
    sup = g["cnt"].reindex(show["bjd"]).values
    res.tables.append(AggTable(
        key="trade.index", title="상권 매력도 지수(0~100, 상위 지역 + 대상 지역)", df=show[["bjd"] + cols],
        dims=["bjd"], values=cols, kind={c: "applied" for c in cols}, sources=list(dict.fromkeys(res.used_sources[:1])),
        unit={c: "점" for c in cols[:-1]} | {"rank": "위"},
        labels={"bjd": "법정동", "score": "종합 매력도", "rank": "순위"},
        support=pd.DataFrame({c: sup for c in cols}) if pd.notna(sup).any() else None,
        note="가중치: 시장규모 30%·성장성 25%·점포당 매출 25%·관광 수요 20%. 각 항목은 업종 내 지역 간 최소-최대 정규화(응용집계·지수).",
    ))
    res.charts.append(ChartSpec("c.index", "trade.index", "barh", "상권 매력도 지수", x="bjd", y=["score"], sort=True,
                                extra={"highlight_rows": region.bjd}))
    res.kpis.append(KPI("상권 매력도 순위", "trade.index", {"bjd": region.bjd[0]}, "rank", "위"))
    res.facts["n_index_regions"] = int(len(parts))


def _re_escape(s: str) -> str:
    import re

    return re.escape(s)


# ---------------------------------------------------------------------------------------- 허브 748(유동인구-카드)
def _population_748(res: AnalysisResult, d: pd.DataFrame, region: RegionSel, ind: IndustrySel,
                    with_trend: bool = True):
    """허브 공개 실데이터 748(월×행정동 방문인구 + 업종 카드매출)로 유동인구와 '방문인구 대비 매출'을 본다.

    - 방문인구(visit_pop)는 월간 시간대별 측정값의 합(연인원) → 해당 월 일수×24로 나눠 '시간당 평균 체류 방문인구'로 환산
    - 방문인구 대비 매출 지수 = (업종 매출 ÷ 방문인구) / 전체 행정동 평균 × 100
    """
    if d is None or d.empty or not region.emd:
        return
    d = d.copy()
    d["ym"] = d["ym"].astype(str)
    months = sorted(d["ym"].unique())
    last = months[-1]
    y, m = int(last[:4]), int(last[5:7])
    last12 = [f"{(y * 12 + m - 1 - k) // 12:04d}-{(y * 12 + m - 1 - k) % 12 + 1:02d}" for k in range(11, -1, -1)]
    last24 = [f"{(y * 12 + m - 1 - k) // 12:04d}-{(y * 12 + m - 1 - k) % 12 + 1:02d}" for k in range(23, -1, -1)]
    names = set(region.emd)
    pop = d.drop_duplicates(["ym", "emd_nm", "sex"])[["ym", "emd_nm", "sex", "visit_pop", "resd_pop", "work_pop"]]
    pop = pop.groupby(["ym", "emd_nm"])[["visit_pop", "resd_pop", "work_pop"]].sum().reset_index()
    tgt = pop[pop["emd_nm"].isin(names) & pop["ym"].isin(last24)].groupby("ym")[["visit_pop", "resd_pop"]].sum().reset_index()
    if tgt.empty:
        res.notes.append(f"허브 748 유동인구 자료에 '{', '.join(region.emd)}' 행이 없습니다.")
        return
    src = "hub:748"
    res.used_sources.append(src)
    days = pd.to_datetime(tgt["ym"] + "-01").dt.days_in_month
    tgt["visit"] = tgt["visit_pop"] / (days * 24)
    tgt["resd"] = tgt["resd_pop"] / (days * 24)
    tgt["label"] = tgt["ym"].str.replace("-", ".", regex=False)
    if with_trend:
      res.tables.append(AggTable(
        key="trade.pop748", title=f"{region.label} 월별 시간당 평균 방문·거주인구({tgt['label'].iloc[0]}~{tgt['label'].iloc[-1]})",
        df=tgt[["label", "visit", "resd"]].reset_index(drop=True), dims=["label"], values=["visit", "resd"],
        kind={"visit": "simple", "resd": "simple"}, sources=[src], unit={"visit": "명", "resd": "명"},
        labels={"label": "년월", "visit": "방문인구(시간당 평균)", "resd": "거주인구(시간당 평균)"},
        note="허브 748: 통신 기반 추정 유동인구의 월간 시간대별 측정 합(연인원)을 그달 일수×24시간으로 나눈 값.",
    ))
      res.charts.append(ChartSpec("c.pop748", "trade.pop748", "line", f"{region.label} 월별 방문인구(시간당 평균)",
                                  x="label", y=["visit"], y_label="명"))
    cur = tgt[tgt["ym"].isin(last12)]["visit"].mean()
    prev = tgt[~tgt["ym"].isin(last12)]["visit"].mean() if (~tgt["ym"].isin(last12)).any() else np.nan
    res.facts["visit748_yoy"] = pct(cur, prev) if np.isfinite(prev) else None

    # 방문인구 대비 업종 매출 지수(최근 12개월, 행정동 비교)
    sales = d[d["ym"].isin(last12) & (d["biz_nm"].astype(str) == ind.name)].groupby("emd_nm")["use_amt"].sum()
    vp = pop[pop["ym"].isin(last12)].groupby("emd_nm")["visit_pop"].sum()
    per = (sales / vp.reindex(sales.index)).replace([np.inf, -np.inf], np.nan).dropna()
    per = per[per > 0]
    if len(per) >= 3 and (per.index.isin(names)).any():
        idx = (per / per.mean() * 100).rename("idx").reset_index()
        idx["rank"] = idx["idx"].rank(ascending=False, method="min")
        idx["emd"] = idx["emd_nm"].str.split(" ").str[-1]
        top = idx.sort_values("idx", ascending=False).head(8)
        show = pd.concat([top, idx[idx["emd_nm"].isin(names)]]).drop_duplicates("emd_nm").reset_index(drop=True)
        res.tables.append(AggTable(
            key="trade.per_visit", title=f"{ind.label or ind.name} 방문인구 대비 매출 지수({last12[0].replace('-', '.')}~{last12[-1].replace('-', '.')}, 행정동 평균=100)",
            df=show[["emd", "idx", "rank"]], dims=["emd"], values=["idx", "rank"],
            kind={"idx": "applied", "rank": "applied"}, sources=[src], unit={"idx": "점", "rank": "위"},
            labels={"emd": "행정동", "idx": "방문인구 대비 매출 지수", "rank": "순위"},
            note="(업종 카드 매출 ÷ 방문인구 연인원)을 업종 매출이 있는 행정동 평균과 비교한 지수. 100보다 크면 방문객 1명이 쓰는 돈이 평균보다 많음.",
        ))
        res.charts.append(ChartSpec("c.per_visit", "trade.per_visit", "barh", "방문인구 대비 매출 지수(평균=100)",
                                    x="emd", y=["idx"], sort=True,
                                    extra={"highlight_rows": [n.split(" ")[-1] for n in names]}))
        mine = idx[idx["emd_nm"].isin(names)].iloc[0]
        res.kpis.append(KPI("방문인구 대비 매출 지수", "trade.per_visit", {"emd": mine["emd"]}, "idx", ""))
        res.facts["per_visit_rank"] = f"{int(mine['rank'])}위/{len(idx)}곳"


# ---------------------------------------------------------------------------------------- 허브 583(Open API, 일별)
def _population_583(res: AnalysisResult, d: pd.DataFrame, region: RegionSel):
    """허브 Open API 583(읍면동 일별 내국인 유동인구)로 '최근' 방문인구 추이·요일·연령 구성을 본다."""
    d = d[d["emd_nm"].astype(str).isin(set(region.emd))].copy()
    if d.empty:
        return
    src = "hub:583"
    res.used_sources.append(src)
    d["date"] = pd.to_datetime(d["base_date"].astype(str), format="%Y%m%d", errors="coerce")
    for c in ("visit_pop_cnt", "resd_pop_cnt"):
        d[c] = num(d[c])
    daily = d.groupby("date")[["visit_pop_cnt", "resd_pop_cnt"]].sum().reset_index()
    end = daily["date"].max()
    last12 = daily[daily["date"] > end - pd.Timedelta(days=365)]
    prev12 = daily[(daily["date"] <= end - pd.Timedelta(days=365)) & (daily["date"] > end - pd.Timedelta(days=730))]
    period = f"{last12['date'].min():%Y.%m.%d}~{end:%Y.%m.%d}"
    res.facts["pop583_period"] = period
    # 월별 일평균 방문인구
    daily["ym"] = daily["date"].dt.strftime("%Y.%m")
    mm = daily.groupby("ym").agg(visit=("visit_pop_cnt", "mean"), n=("date", "nunique")).reset_index()
    mm = mm[mm["n"] >= 20][["ym", "visit"]].reset_index(drop=True)
    res.tables.append(AggTable(
        key="trade.pop583", title=f"{region.label} 월별 일평균 방문인구({mm['ym'].iloc[0]}~{mm['ym'].iloc[-1]})",
        df=mm, dims=["ym"], values=["visit"], kind={"visit": "simple"}, sources=[src], unit={"visit": "명"},
        labels={"ym": "년월", "visit": "일평균 방문인구(추정)"},
        note="허브 Open API 583: 통신 기반 읍면동 일별 내국인 유동인구(성·연령 합계). 20일 미만 자료 달 제외.",
    ))
    res.charts.append(ChartSpec("c.pop583", "trade.pop583", "line", f"{region.label} 월별 일평균 방문인구(최근 2년)",
                                x="ym", y=["visit"], y_label="명"))
    rows = [("최근 12개월 일평균 방문인구(명)", float(last12["visit_pop_cnt"].mean()))]
    if len(prev12) >= 300:
        yoy = pct(last12["visit_pop_cnt"].mean(), prev12["visit_pop_cnt"].mean())
        rows.append(("방문인구 전년 대비 증감률(%)", yoy))
        res.facts["pop583_yoy"] = yoy
    wk = last12[last12["date"].dt.weekday < 5]["visit_pop_cnt"].mean()
    we = last12[last12["date"].dt.weekday >= 5]["visit_pop_cnt"].mean()
    if wk:
        rows.append(("주말/평일 방문인구 비율(%)", we / wk * 100))
    kpi = pd.DataFrame(rows, columns=["지표", "value"])
    res.tables.append(AggTable(
        key="trade.pop583_kpi", title=f"{region.label} 최근 방문인구 지표({period})", df=kpi, dims=["지표"],
        values=["value"], kind={"value": "applied"}, sources=[src], unit={"value": ""}, labels={"value": "값"},
    ))
    res.kpis.append(KPI("최근 방문인구 증감", "trade.pop583_kpi", {"지표": "방문인구 전년 대비 증감률(%)"}, "value", "%±"))
    res.kpis.append(KPI("주말/평일 방문인구", "trade.pop583_kpi", {"지표": "주말/평일 방문인구 비율(%)"}, "value", "%"))
    # 방문객 연령 구성(최근 12개월)
    if "age" in d.columns:
        a = d[d["date"] > end - pd.Timedelta(days=365)]
        ag = a.groupby(a["age"].astype(str).map(AGE_LABEL).fillna("기타"))["visit_pop_cnt"].sum()
        ag = ag[ag.index != "기타"]
        if ag.sum():
            order = [v for v in AGE_LABEL.values() if v in ag.index]
            ag = (ag / ag.sum() * 100).reindex(order).rename("share").reset_index()
            ag.columns = ["age", "share"]
            res.tables.append(AggTable(
                key="trade.pop_age", title=f"{region.label} 방문인구 연령 구성({period})", df=ag, dims=["age"],
                values=["share"], kind={"share": "applied"}, sources=[src], unit={"share": "%"},
                labels={"age": "연령대", "share": "비중"},
            ))
            res.charts.append(ChartSpec("c.pop_age", "trade.pop_age", "bar", "방문인구 연령 구성(최근 12개월)",
                                        x="age", y=["share"], y_label="%"))
            res.facts["pop583_top_age"] = str(ag.sort_values("share", ascending=False).iloc[0]["age"])
