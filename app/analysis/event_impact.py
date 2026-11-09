"""② 축제·행사 효과 분석.

질문 예: "2024 최남단 방어축제(대정읍) 지역경제 효과는?"
방법
- 유동인구(일 단위): 행사 기간 일평균 방문인구 vs 직전 3주 같은 요일 평균(기준선) → 증가율
  + 비교지역 동일 계산 → 이중차분(DiD, %p)
- 버스(일 단위): 행사장 반경 내 정류소 승차 인원 증가율
- 카드(월 단위 한계): 행사월 업종별 매출 전년 동월 대비 증감률, 대상지역 − 비교지역 = DiD(%p),
  추정 추가 소비액 = 전년 동월 매출 × DiD (모델링 추정, 응용집계)
- 날씨: 행사일 강수·기온(반출 가능 데이터)
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..guard.export_guard import AggTable, Rollup
from .common import (
    AGE_LABEL, AnalysisResult, ChartSpec, KPI, RegionSel, haversine_km, num, pct, ym_add, ym_label,
)
from .trade_area import TOURIST_TYPES, card_frame

FOOD_LODGING = {
    "I56111": "한식 음식점업", "I56112": "중식 음식점업", "I56113": "일식 음식점업", "I56114": "서양식 음식점업",
    "I56219": "기타 주점업", "I56220": "비알콜 음료점업", "I55111": "호텔업", "I55112": "여관업",
    "I55113": "휴양콘도 운영업", "G47213": "수산물 소매업", "G47122": "체인화 편의점", "G47842": "관광 민예품 및 선물용품 소매업",
}


def analyze(data: dict[str, pd.DataFrame], region: RegionSel, start: date, end: date, event_name: str,
            control: RegionSel | None = None, stops: pd.DataFrame | None = None,
            bus_usage: pd.DataFrame | None = None, venue: tuple[float, float] | None = None,
            bus_src: str = "3-04", stops_src: str = "3-03") -> AnalysisResult:
    res = AnalysisResult(
        mode="event_impact", title=f"{event_name} 지역경제 효과 분석",
        params={"event": event_name, "start": start.isoformat(), "end": end.isoformat(), "region": region.label,
                "control": control.label if control else None},
    )
    n_days = (end - start).days + 1
    res.facts["event_days"] = n_days
    pop_src = next((k for k in ("4-11", "4-10", "hub:583") if k in data), None)
    if pop_src:
        _population(res, data[pop_src], pop_src, region, control, start, end)
    else:
        res.notes.append("일 단위 유동인구(센터 4-10/4-11 또는 허브 Open API 583)가 없어 방문객 증가 분석을 생략했습니다.")
    if stops is not None and bus_usage is not None and len(stops) and len(bus_usage):
        _bus(res, stops, bus_usage, venue or region.center, start, end, bus_src, region, stops_src)
    else:
        miss = [n for n, d in (("버스 정류소 정보(3-03)", stops), ("버스 정류소별 이용 현황(3-04)", bus_usage)) if d is None or not len(d)]
        res.notes.append(f"{'·'.join(miss)}가 없어 대중교통 분석을 생략했습니다.")
    card, csrc = card_frame(data)
    if card is not None:
        _card(res, card, csrc, region, control, start, end)
    else:
        res.notes.append("카드 이용 현황(읍면동) 데이터가 없어 소비 효과 분석을 생략했습니다.")
    if "5-02" in data:
        _weather(res, data["5-02"], start, end)
    _summary_kpis(res)
    return res


# ---------------------------------------------------------------------------------------
def _daily_pop(df: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    d = df[df["emd_nm"].astype(str).isin(set(names))].copy()
    if d.empty:
        return pd.DataFrame(columns=["date", "visit", "resd", "work"])
    for c in ("resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"):
        d[c] = num(d[c])
    d["date"] = pd.to_datetime(d["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    hourly = d.groupby(["date", "time"])[["visit_pop_cnt", "resd_pop_cnt", "work_pop_cnt"]].sum().reset_index()
    daily = hourly.groupby("date")[["visit_pop_cnt", "resd_pop_cnt", "work_pop_cnt"]].mean().reset_index()
    return daily.rename(columns={"visit_pop_cnt": "visit", "resd_pop_cnt": "resd", "work_pop_cnt": "work"})


def _baseline(daily: pd.DataFrame, start: date, end: date, weeks: int = 3) -> tuple[float, float, list]:
    """행사일과 같은 요일의 직전 n주 평균을 기준선으로."""
    s = daily.set_index(daily["date"].dt.date)["visit"]
    ev, base = [], []
    d = start
    while d <= end:
        if d in s.index:
            ev.append(s[d])
        for k in range(1, weeks + 1):
            b = d - timedelta(days=7 * k)
            if b in s.index and not (start <= b <= end):
                base.append(s[b])
        d += timedelta(days=1)
    return (float(np.mean(ev)) if ev else np.nan, float(np.mean(base)) if base else np.nan, ev)


def _population(res, df, src, region, control, start, end):
    res.used_sources.append(src)
    daily = _daily_pop(df, region.emd)
    if daily.empty:
        res.notes.append(f"유동인구 데이터에 '{', '.join(region.emd)}' 행이 없습니다.")
        return
    lo, hi = pd.Timestamp(start - timedelta(days=21)), pd.Timestamp(end + timedelta(days=7))
    win = daily[(daily["date"] >= lo) & (daily["date"] <= hi)].copy()
    win["label"] = win["date"].dt.strftime("%m.%d")
    win["is_event"] = win["date"].dt.date.map(lambda x: start <= x <= end)
    is_daily = bool(df.attrs.get("daily")) or pd.Series(df["time"]).nunique() <= 1
    res.tables.append(AggTable(
        key="event.daily", title=f"{region.label} 일별 방문인구(행사 전 3주~행사 후 1주)",
        df=win[["label", "visit", "resd"]].reset_index(drop=True), dims=["label"], values=["visit", "resd"],
        kind={"visit": "simple", "resd": "simple"}, sources=[src], unit={"visit": "명", "resd": "명"},
        labels={"label": "날짜", "visit": "방문인구" + ("(일 추정)" if is_daily else "(시간당 평균)"),
                "resd": "거주인구" + ("(일 추정)" if is_daily else "(시간당 평균)")},
        note=("허브 Open API 583 읍면동 일별 내국인 유동인구(성·연령 합계). 방문인구 = 해당 지역 비거주·비근무 체류 인구."
              if is_daily else "해당 일 01~24시 정각 측정 인구의 평균. 방문인구 = 해당 지역 비거주·비근무 체류 인구."),
    ))
    ev_lab = [d.strftime("%m.%d") for d in pd.date_range(start, end)]
    res.charts.append(ChartSpec("c.daily", "event.daily", "line", f"{region.label} 일별 방문인구", x="label", y=["visit"],
                                highlight=(ev_lab[0], ev_lab[-1]), highlight_label="행사 기간", y_label="명"))
    ev, base, _ = _baseline(daily, start, end)
    lift = pct(ev, base)
    rows = [("행사기간 방문인구 증가율(직전 3주 같은 요일 대비)", lift)]
    res.facts["visit_lift"] = lift
    # 전년 동기: 날짜가 아니라 '같은 요일'(52주 전)끼리 비교(요일 차이로 생기는 착시 방지)
    ly_s, ly_e = start - timedelta(days=364), end - timedelta(days=364)
    ly = daily[(daily["date"].dt.date >= ly_s) & (daily["date"].dt.date <= ly_e)]
    if len(ly):
        rows.append((f"전년 같은 요일({ly_s:%Y.%m.%d}~{ly_e:%m.%d}) 대비 방문인구 증감률", pct(ev, ly["visit"].mean())))
        res.facts["ly_period"] = f"{ly_s:%Y.%m.%d}~{ly_e:%m.%d}"
    if control and control.emd:
        cd = _daily_pop(df, control.emd)
        if not cd.empty:
            cev, cbase, _ = _baseline(cd, start, end)
            clift = pct(cev, cbase)
            rows.append((f"비교지역({control.label}) 같은 기간 증가율", clift))
            rows.append(("순효과(이중차분, %p)", lift - clift if np.isfinite(lift) and np.isfinite(clift) else np.nan))
            res.facts["visit_did"] = lift - clift if np.isfinite(lift) and np.isfinite(clift) else None
            cw = cd[(cd["date"] >= lo) & (cd["date"] <= hi)].copy()
            if len(cw):
                both = win[["date", "label", "visit"]].merge(cw[["date", "visit"]], on="date", suffixes=("", "_c"))
                b_t = both["visit"].iloc[: min(14, len(both))].mean()
                b_c = both["visit_c"].iloc[: min(14, len(both))].mean()
                both["tgt_idx"] = both["visit"] / b_t * 100 if b_t else np.nan
                both["ctl_idx"] = both["visit_c"] / b_c * 100 if b_c else np.nan
                res.tables.append(AggTable(
                    key="event.did", title=f"방문인구 지수 비교: {region.label} vs {control.label}(행사 전 2주 평균=100)",
                    df=both[["label", "tgt_idx", "ctl_idx"]], dims=["label"], values=["tgt_idx", "ctl_idx"],
                    kind={"tgt_idx": "applied", "ctl_idx": "applied"}, sources=[src], unit={"tgt_idx": "점", "ctl_idx": "점"},
                    labels={"label": "날짜", "tgt_idx": f"{region.label}", "ctl_idx": f"{control.label}(비교)"},
                ))
                res.charts.append(ChartSpec("c.did", "event.did", "line", "방문인구 지수: 행사지역 vs 비교지역", x="label",
                                            y=["tgt_idx", "ctl_idx"], highlight=(ev_lab[0], ev_lab[-1]),
                                            highlight_label="행사 기간", y_label="지수"))
    lift_df = pd.DataFrame(rows, columns=["지표", "value"])
    res.tables.append(AggTable(
        key="event.lift", title="방문인구 효과 지표", df=lift_df, dims=["지표"], values=["value"],
        kind={"value": "applied"}, sources=[src], unit={"value": "%"}, labels={"value": "값"},
    ))
    res.kpis.append(KPI("방문인구 증가율", "event.lift", {"지표": rows[0][0]}, "value", "%±"))
    if any(r[0].startswith("순효과") for r in rows):
        res.kpis.append(KPI("순효과(이중차분)", "event.lift", {"지표": "순효과(이중차분, %p)"}, "value", "%p"))

    # 시간대 패턴: 행사일 vs 기준선(같은 요일) 시간대별 지수
    d = df[df["emd_nm"].astype(str).isin(set(region.emd))].copy()
    d["visit_pop_cnt"] = num(d["visit_pop_cnt"])
    d["date"] = pd.to_datetime(d["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce").dt.date
    d["hour"] = num(d["time"]).astype("Int64")
    hh = d.groupby(["date", "hour"])["visit_pop_cnt"].sum().reset_index()
    if hh["hour"].nunique() <= 1:      # 일 단위 자료: 시간대 분석 생략
        hh = hh.iloc[0:0]
    evd = hh[hh["date"].map(lambda x: start <= x <= end)]
    n_ev = (end - start).days + 1
    base_days = {start + timedelta(days=i) - timedelta(days=7 * k) for i in range(n_ev) for k in (1, 2, 3)}
    bd = hh[hh["date"].isin(base_days)]
    if len(evd) and len(bd):
        e = evd.groupby("hour")["visit_pop_cnt"].mean()
        b = bd.groupby("hour")["visit_pop_cnt"].mean()
        hx = pd.DataFrame({"hour": e.index.astype(int), "ratio": (e / b.reindex(e.index)).values * 100 - 100})
        res.tables.append(AggTable(
            key="event.hourly", title="행사기간 시간대별 방문인구 증감률(평시 같은 요일 대비)", df=hx, dims=["hour"],
            values=["ratio"], kind={"ratio": "applied"}, sources=[src], unit={"ratio": "%"},
            labels={"hour": "시각", "ratio": "증감률"},
        ))
        res.charts.append(ChartSpec("c.hourly", "event.hourly", "bar", "시간대별 방문인구 증감률(행사기간)", x="hour",
                                    y=["ratio"], y_label="%"))
        res.facts["peak_lift_hour"] = int(hx.sort_values("ratio", ascending=False).iloc[0]["hour"])

    # 방문객 구성(연령) 변화
    if "age" in df.columns:
        a = df[df["emd_nm"].astype(str).isin(set(region.emd))].copy()
        a["visit_pop_cnt"] = num(a["visit_pop_cnt"])
        a["date"] = pd.to_datetime(a["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce").dt.date
        a["age_g"] = a["age"].astype(str).map(_age_group)
        ea = a[a["date"].map(lambda x: start <= x <= end)].groupby("age_g")["visit_pop_cnt"].sum()
        ba = a[a["date"].isin(base_days)].groupby("age_g")["visit_pop_cnt"].sum()
        if ea.sum() and ba.sum():
            comp = pd.DataFrame({"event": ea / ea.sum() * 100, "base": ba / ba.sum() * 100}).fillna(0)
            comp["diff"] = comp["event"] - comp["base"]
            comp = comp.reset_index().rename(columns={"index": "age_g"})
            comp = comp.rename(columns={comp.columns[0]: "age_g"})
            res.tables.append(AggTable(
                key="event.age", title="방문인구 연령 구성 변화(행사기간 vs 평시)", df=comp, dims=["age_g"],
                values=["event", "base", "diff"], kind={"event": "applied", "base": "applied", "diff": "applied"},
                sources=[src], unit={"event": "%", "base": "%", "diff": "%p"},
                labels={"age_g": "연령대", "event": "행사기간 비중", "base": "평시 비중", "diff": "차이"}, margins=["age_g"],
            ))
            top = comp.sort_values("diff", ascending=False).iloc[0]
            res.facts["age_up"] = str(top["age_g"])


def _age_group(a: str) -> str:
    a = str(a).replace(".0", "")
    if len(a) == 4 and a.isdigit():  # SKT: 2024 → 20대
        a = a[:1] + "0" if a != "7000" else "70"
        if a == "00":
            a = "10"
    try:
        v = int(a)
    except ValueError:
        return "기타"
    if v < 20:
        return "10대 이하"
    if v >= 60:
        return "60대 이상"
    return f"{v}대"


def _bus(res, stops, usage, center, start, end, src, region, stops_src="3-03"):
    if not center:
        return
    s = stops.copy()
    s["longitude"] = num(s["longitude"])
    s["latitude"] = num(s["latitude"])
    s = s.dropna(subset=["longitude", "latitude"]).drop_duplicates("bus_station_id")
    dist = haversine_km(s["longitude"], s["latitude"], center[0], center[1])
    radius = max(1.5, min(region.radius_km, 3.0))
    s = s[dist <= radius]
    if s.empty:
        res.notes.append("행사장 반경 내 버스 정류소가 없어 대중교통 분석을 생략했습니다.")
        return
    u = usage[usage["bus_station_id"].astype(str).isin(s["bus_station_id"].astype(str))].copy()
    u = u[u["get_on_off_type"].astype(str).isin(["승차", "환승"])]
    if u.empty:
        return
    res.used_sources += [stops_src, src]
    u["user_count"] = num(u["user_count"])
    u["date"] = pd.to_datetime(u["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    daily = u.groupby("date")["user_count"].sum().reset_index()
    lo, hi = pd.Timestamp(start - timedelta(days=21)), pd.Timestamp(end + timedelta(days=7))
    win = daily[(daily["date"] >= lo) & (daily["date"] <= hi)].copy()
    if win.empty:
        res.notes.append("행사 기간 전후의 버스 이용 데이터가 없습니다.")
        return
    win["label"] = win["date"].dt.strftime("%m.%d")
    res.tables.append(AggTable(
        key="event.bus", title=f"행사장 반경 {radius:g}km 정류소({len(s)}곳) 일별 승차 인원", df=win[["label", "user_count"]],
        dims=["label"], values=["user_count"], kind={"user_count": "simple"}, sources=[stops_src, src],
        unit={"user_count": "명"}, labels={"label": "날짜", "user_count": "승차(환승 포함)"},
        note="자료가 있는 날짜만 표시(허브 Open API 스냅샷은 행사일·같은 요일 기준선 날짜만 조회)." if src.startswith("hub:") else "",
    ))
    ev_lab = [d.strftime("%m.%d") for d in pd.date_range(start, end)]
    res.charts.append(ChartSpec("c.bus", "event.bus", "bar", "행사장 인근 정류소 일별 승차", x="label", y=["user_count"],
                                highlight=(ev_lab[0], ev_lab[-1]), highlight_label="행사 기간", y_label="명"))
    tmp = daily.rename(columns={"user_count": "visit"})
    ev, base, _ = _baseline(tmp, start, end)
    lift = pct(ev, base)
    res.facts["bus_lift"] = lift
    res.tables.append(AggTable(
        key="event.bus_lift", title="행사장 인근 버스 승차 증가율", df=pd.DataFrame({"지표": ["버스 승차 증가율(직전 3주 같은 요일 대비)"], "value": [lift]}),
        dims=["지표"], values=["value"], kind={"value": "applied"}, sources=[stops_src, src], unit={"value": "%"},
        labels={"value": "값"},
    ))
    res.kpis.append(KPI("인근 버스 승차 증가율", "event.bus_lift", {"지표": "버스 승차 증가율(직전 3주 같은 요일 대비)"}, "value", "%±"))
    # 행사일 시간대별 승차 증가(셔틀·증편 판단용)
    if "base_hour" in u.columns:
        u["hour"] = num(u["base_hour"]).astype("Int64")
        evh = u[u["date"].dt.date.map(lambda x: start <= x <= end)].groupby("hour")["user_count"].sum() / ((end - start).days + 1)
        bdays = {start + timedelta(days=i) - timedelta(days=7 * k) for i in range((end - start).days + 1) for k in (1, 2, 3)}
        bh = u[u["date"].dt.date.isin(bdays)].groupby("hour")["user_count"].sum() / max(1, len(bdays))
        hx = pd.DataFrame({"hour": evh.index.astype(int), "event": evh.values, "base": bh.reindex(evh.index).values})
        hx["extra"] = hx["event"] - hx["base"]
        res.tables.append(AggTable(
            key="event.bus_hour", title="행사일 시간대별 평균 승차 vs 평시(같은 요일)", df=hx, dims=["hour"],
            values=["event", "base", "extra"], kind={"event": "simple", "base": "simple", "extra": "simple"},
            sources=[stops_src, src], unit={"event": "명", "base": "명", "extra": "명"},
            labels={"hour": "시각", "event": "행사일 평균", "base": "평시 평균", "extra": "추가 승차"},
        ))
        if len(hx):
            res.facts["bus_peak_hour"] = int(hx.sort_values("extra", ascending=False).iloc[0]["hour"])
    for _, r in s.iterrows():
        res.map_points.append({"type": "stop", "lon": float(r["longitude"]), "lat": float(r["latitude"]),
                               "name": str(r["bus_station_name"])})
    res.map_points.append({"type": "venue", "lon": float(center[0]), "lat": float(center[1]), "name": "행사장(추정)"})


def _card(res, card, src, region, control, start, end):
    ev_months = sorted({int(f"{d.year}{d.month:02d}") for d in pd.date_range(start, end)})
    months = set(card["ym"].dropna().astype(int).unique().tolist())
    have = [m for m in ev_months if m in months and ym_add(m, -12) in months]
    if not have:
        res.notes.append("행사월과 전년 동월 카드 데이터가 모두 있어야 소비 효과를 계산할 수 있습니다.")
        return
    res.used_sources.append(src)
    prev = [ym_add(m, -12) for m in have]
    c = card[card["biz_cd"].isin(FOOD_LODGING)]
    t_cur = c[c["bjd"].isin(region.bjd) & c["ym"].isin(have)].groupby("biz_cd")[["use_amt", "use_cnt"]].sum()
    t_prev = c[c["bjd"].isin(region.bjd) & c["ym"].isin(prev)].groupby("biz_cd")[["use_amt", "use_cnt"]].sum()
    rows = pd.DataFrame({"cur": t_cur["use_amt"], "prev": t_prev["use_amt"].reindex(t_cur.index),
                         "cnt": t_cur["use_cnt"], "pcnt": t_prev["use_cnt"].reindex(t_cur.index)})
    rows["g_t"] = [pct(a, b) for a, b in zip(rows["cur"], rows["prev"])]
    if control and control.bjd:
        c_cur = c[c["bjd"].isin(control.bjd) & c["ym"].isin(have)].groupby("biz_cd")["use_amt"].sum()
        c_prev = c[c["bjd"].isin(control.bjd) & c["ym"].isin(prev)].groupby("biz_cd")["use_amt"].sum()
        rows["g_c"] = [pct(c_cur.get(k), c_prev.get(k)) for k in rows.index]
    else:
        tot_cur = c[c["ym"].isin(have)].groupby("biz_cd")["use_amt"].sum()
        tot_prev = c[c["ym"].isin(prev)].groupby("biz_cd")["use_amt"].sum()
        rows["g_c"] = [pct(tot_cur.get(k), tot_prev.get(k)) for k in rows.index]
    rows["did"] = rows["g_t"] - rows["g_c"]
    rows["extra"] = rows["prev"] * rows["did"] / 100.0
    rows = rows.reset_index()
    rows["biz"] = rows["biz_cd"].map(FOOD_LODGING)
    rows = rows.sort_values("did", ascending=False).reset_index(drop=True)
    sup_n = rows[["cnt", "pcnt"]].min(axis=1)
    ctl_label = control.label if control else "제주 전체"
    res.tables.append(AggTable(
        key="event.card", title=f"행사월({', '.join(ym_label(m) for m in have)}) 업종별 매출 증감률: {region.label} vs {ctl_label}",
        df=rows[["biz", "g_t", "g_c", "did"]], dims=["biz"], values=["g_t", "g_c", "did"],
        kind={"g_t": "applied", "g_c": "applied", "did": "applied"}, sources=[src],
        unit={"g_t": "%", "g_c": "%", "did": "%p"},
        labels={"biz": "업종", "g_t": f"{region.label} 전년동월 대비", "g_c": f"{ctl_label} 전년동월 대비", "did": "순효과(%p)"},
        support=pd.DataFrame({"g_t": sup_n.values, "g_c": sup_n.values, "did": sup_n.values}),
        note="카드 데이터는 월 단위 → 행사 며칠의 효과가 한 달 전체에 희석됨(보수적 추정).",
    ))
    res.charts.append(ChartSpec("c.card", "event.card", "barh", "업종별 매출 순효과(전년동월 대비 증감률 차, %p)",
                                x="biz", y=["did"], sort=True))
    valid = rows[np.isfinite(rows["extra"]) & (sup_n >= 10)]
    extra_total = float(valid["extra"].clip(lower=0).sum())
    res.tables.append(AggTable(
        key="event.card_kpi", title="카드 소비 효과(모델링 추정)",
        df=pd.DataFrame({"지표": ["추정 추가 소비액(음식·숙박·관련 소매, 비씨카드 기준)"], "value": [extra_total]}),
        dims=["지표"], values=["value"], kind={"value": "applied"}, sources=[src], unit={"value": "원"},
        labels={"value": "값"},
        note="추정 추가 소비액 = Σ(전년 동월 매출 × 순효과%p). 증가 업종만 합산, 비씨카드 결제분 한정(전체 소비 아님).",
    ))
    res.kpis.append(KPI("추정 추가 소비액(비씨카드)", "event.card_kpi",
                        {"지표": "추정 추가 소비액(음식·숙박·관련 소매, 비씨카드 기준)"}, "value", "원"))
    if len(rows):
        res.facts["card_top_biz"] = str(rows.iloc[0]["biz"])
    # 이용자 구분(국적 포함)별 행사월 이용건수 — 외국인 국적 셀이 작아 반출 기준 처리가 필요한 표
    tcu = card[card["bjd"].isin(region.bjd) & card["biz_cd"].isin(FOOD_LODGING)]
    order = ["제주도민", "내국인", "법인", "중국", "일본", "동남아", "기타외국"]
    cu = tcu[tcu["ym"].isin(have)].groupby("user_type")["use_cnt"].sum().reindex(order)
    pu = tcu[tcu["ym"].isin(prev)].groupby("user_type")["use_cnt"].sum().reindex(order)
    ut = pd.DataFrame({"user_type": order, "cur_cnt": cu.values, "prev_cnt": pu.values})
    ut = ut[ut[["cur_cnt", "prev_cnt"]].notna().any(axis=1)].fillna(0).reset_index(drop=True)
    if len(ut):
        def _recalc(d):
            d = d.copy()
            d["growth"] = (d["cur_cnt"] / d["prev_cnt"].replace(0, np.nan) - 1) * 100
            return d

        ut = _recalc(ut)
        foreign = {"중국": "외국인", "일본": "외국인", "동남아": "외국인", "기타외국": "외국인"}
        res.tables.append(AggTable(
            key="event.card_users", title=f"{region.label} 행사월 이용자 구분별 이용건수(음식·숙박·관련 소매) vs 전년 동월",
            df=ut[["user_type", "cur_cnt", "prev_cnt", "growth"]], dims=["user_type"],
            values=["cur_cnt", "prev_cnt", "growth"], kind={"cur_cnt": "simple", "prev_cnt": "simple", "growth": "applied"},
            sources=[src], unit={"cur_cnt": "건", "prev_cnt": "건", "growth": "%"},
            labels={"user_type": "이용자 구분", "cur_cnt": "행사월", "prev_cnt": "전년 동월", "growth": "증감률"},
            derived={"growth": ["cur_cnt", "prev_cnt"]}, margins=["user_type"],
            rollups=[Rollup("user_type", {"동남아": "동남아·기타외국", "기타외국": "동남아·기타외국"}, "국적: 동남아·기타외국 통합"),
                     Rollup("user_type", lambda v: foreign.get(v, v) if v != "동남아·기타외국" else "외국인", "국적 → 외국인 합계")],
            recompute=_recalc,
            note="외국인 국적별 건수는 작아서 반출 기준(10 미만)에 따라 국적을 묶어 표시합니다.",
        ))
    # 관광객 비중 변화
    tc = card[card["bjd"].isin(region.bjd)]
    def tshare(ms):
        x = tc[tc["ym"].isin(ms)]
        tot = x["use_amt"].sum()
        tour = x[x["user_type"].isin(TOURIST_TYPES) & (x["tour_type"] != "도민")]["use_amt"].sum()
        return tour / tot * 100 if tot else np.nan
    ts = pd.DataFrame({"구분": ["행사월", "전년 동월"], "value": [tshare(have), tshare(prev)]})
    res.tables.append(AggTable(
        key="event.tourist", title=f"{region.label} 관광객 결제 비중(행사월 vs 전년 동월)", df=ts, dims=["구분"],
        values=["value"], kind={"value": "applied"}, sources=[src], unit={"value": "%"}, labels={"value": "관광객 결제 비중"},
    ))


def _weather(res, df, start, end):
    d = df.copy()
    d["date"] = pd.to_datetime(d["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce").dt.date
    d = d[d["date"].map(lambda x: start <= x <= end if pd.notna(x) else False)]
    if d.empty:
        return
    res.used_sources.append("5-02")
    for c in ("average_temperature", "daily_rainfall", "average_wind_speed"):
        d[c] = num(d[c])
    w = d.groupby("date")[["average_temperature", "daily_rainfall", "average_wind_speed"]].mean().reset_index()
    w["date"] = w["date"].map(lambda x: x.strftime("%m.%d"))
    res.tables.append(AggTable(
        key="event.weather", title="행사 기간 날씨(관측소 평균)", df=w, dims=["date"],
        values=["average_temperature", "daily_rainfall", "average_wind_speed"],
        kind={"average_temperature": "simple", "daily_rainfall": "simple", "average_wind_speed": "simple"},
        sources=["5-02"], unit={"average_temperature": "℃", "daily_rainfall": "mm", "average_wind_speed": "m/s"},
        labels={"date": "날짜", "average_temperature": "평균기온", "daily_rainfall": "강수량", "average_wind_speed": "평균풍속"},
    ))
    rainy = int((w["daily_rainfall"] >= 5).sum())
    res.facts["rainy_days"] = rainy


def _summary_kpis(res: AnalysisResult):
    pass
