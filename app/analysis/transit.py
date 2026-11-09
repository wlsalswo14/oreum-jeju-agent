"""③ 버스 정류소·노선 진단.

질문 예: "대정읍·모슬포 버스 정류소 이용 진단해줘"
입력: 버스 정류소 정보(3-03) + 정류소별 이용 현황(3-04, 시간 단위) [+ 노선별 이용 현황(3-02)]
      — 모두 '반출 가능' 데이터(티머니). 허브 공개 데이터(정류소별 일일 이용자 613, 일별 시간대별 승차 512)도 지원.
출력: 정류소 순위·저이용/과밀 정류소, 요일×시간 히트맵, 첨두 집중도, 이용자 구성, 노선 순위, 지도
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..guard.export_guard import AggTable
from .common import AnalysisResult, ChartSpec, KPI, RegionSel, haversine_km, num

DOW = ["월", "화", "수", "목", "금", "토", "일"]
LOW_USE = 5.0   # 일평균 승차 5명 미만 = 저이용 정류소(검토 대상)


def analyze(data: dict[str, pd.DataFrame], region: RegionSel | None, stops: pd.DataFrame | None,
            usage: pd.DataFrame | None, src: str = "3-04", stops_src: str = "3-03",
            event_days: dict | None = None) -> AnalysisResult:
    """event_days: {날짜: 행사명} — 지역 행사일은 '평시' 진단에서 제외(행사 효과는 축제 분석에서 따로 본다)."""
    label = region.label if region else "제주 전역"
    res = AnalysisResult(mode="transit", title=f"{label} 버스 정류소·노선 이용 진단", params={"region": label})
    b512 = data.get("hub:512")
    if usage is None or usage.empty:
        if b512 is not None and len(b512):
            # 정류소 단위 자료가 없으면 허브 공개 실데이터(512, 제주 전역 일별·시간대별)로 진단
            res.title = "제주 전역 버스 이용 패턴 진단"
            res.params["region"] = "제주 전역"
            if region is not None and region.label not in ("제주 전역", "제주도", "제주"):
                res.notes.append(f"'{region.label}' 정류소 단위 자료(센터 3-03·3-04 또는 허브 Open API 612·613)가 없어 "
                                 "제주 전역 일별·시간대별 승차(허브 512)로 진단했습니다.")
            island_profile(res, b512)
        else:
            res.notes.append("버스 정류소별 이용 현황(3-04) 데이터가 필요합니다.")
        _routes(res, data)
        return res
    u = usage.copy()
    u["user_count"] = num(u["user_count"])
    u["date"] = pd.to_datetime(u["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    u = latest_block(u, res)
    if event_days:
        hit = sorted({d for d in u["date"].dt.date.dropna().unique() if d in event_days})
        if hit and len(hit) < u["date"].nunique():
            u = u[~u["date"].dt.date.isin(set(hit))]
            names = ", ".join(dict.fromkeys(event_days[d] for d in hit))
            res.notes.append(f"지역 행사일 {len(hit)}일({hit[0]:%m.%d}~{hit[-1]:%m.%d}, {names})은 평시 진단에서 제외했습니다.")
            res.facts["excluded_event"] = names
    sel_ids = None
    if stops is not None and len(stops) and region is not None and region.center:
        s = stops.copy()
        s["longitude"] = num(s["longitude"])
        s["latitude"] = num(s["latitude"])
        s = s.dropna(subset=["longitude", "latitude"]).drop_duplicates("bus_station_id")
        if len(region.bjd) > 3:   # 시·권역 단위 질문: 반경 대신 '가장 가까운 법정동'이 지역에 속하는 정류소
            s = s[_in_area(s, region.bjd)]
        else:
            dist = haversine_km(s["longitude"], s["latitude"], region.center[0], region.center[1])
            s = s[dist <= region.radius_km]
        sel_ids = set(s["bus_station_id"].astype(str))
        for _, r in s.iterrows():
            res.map_points.append({"type": "stop", "lon": float(r["longitude"]), "lat": float(r["latitude"]),
                                   "name": str(r["bus_station_name"]), "id": str(r["bus_station_id"])})
        res.used_sources.append(stops_src)
    elif region is not None:
        label = "제주 전역(정류소 좌표 없음)"
        res.title = f"{label} 버스 정류소·노선 이용 진단"
        res.params["region"] = label
        res.notes.append("정류소 정보(3-03)가 없어 지역을 거르지 못하고, 넣은 파일의 모든 정류소로 분석했습니다. "
                         "지역별 진단을 하려면 「버스 정류소 정보」를 함께 넣어 주세요.")
    if sel_ids is not None:
        u = u[u["bus_station_id"].astype(str).isin(sel_ids)]
    if u.empty:
        res.notes.append(f"'{label}' 정류소의 이용 기록이 없습니다. 정류소 정보와 이용 현황 파일의 지역·기간을 확인해 주세요.")
        return res
    res.used_sources.append(src)
    n_days = max(1, u["date"].nunique())
    period = f"{u['date'].min():%Y.%m.%d}~{u['date'].max():%Y.%m.%d}"
    res.params["period"] = period
    on = u[u["get_on_off_type"].astype(str).isin(["승차", "환승"])]
    off = u[u["get_on_off_type"].astype(str) == "하차"]

    # 정류소별 일평균(정류소마다 기록이 있는 날짜 수로 나눔)
    days_stop = u.groupby("bus_station_id")["date"].nunique()
    g_on = on.groupby(["bus_station_id", "bus_station_name"])["user_count"].sum()
    g_on = g_on / g_on.index.get_level_values(0).map(days_stop).values
    g_off = off.groupby(["bus_station_id", "bus_station_name"])["user_count"].sum()
    g_off = g_off / g_off.index.get_level_values(0).map(days_stop).values
    st = pd.concat([g_on.rename("on"), g_off.rename("off")], axis=1).astype(float).fillna(0.0).reset_index()
    st["total"] = st["on"] + st["off"]
    st = st.sort_values("on", ascending=False).reset_index(drop=True)
    n_stops = len(st)
    has_off = not off.empty                       # 허브 613은 승차·환승만 있음(하차 없음)
    oc = ["on", "off"] if has_off else ["on"]
    top = st.head(15)
    res.facts["stop_days"] = int(n_days)
    if src.startswith("hub:"):
        res.facts["stop_src_hub"] = True
    res.tables.append(AggTable(
        key="transit.top", title=f"{label} 정류소별 일평균 {'승하차' if has_off else '승차'} 상위 15곳({period}, {n_days}일)",
        df=top[["bus_station_name"] + oc], dims=["bus_station_name"], values=oc,
        kind={c: "simple" for c in oc}, sources=[stops_src, src], unit={c: "명" for c in oc},
        labels={"bus_station_name": "정류소", "on": "일평균 승차(환승 포함)", "off": "일평균 하차"},
    ))
    res.charts.append(ChartSpec("c.top", "transit.top", "barh", "정류소별 일평균 승차 상위", x="bus_station_name", y=["on"], sort=True))
    n_low = max(5, min(10, len(st) // 4))
    low = st.sort_values("on").head(n_low).copy()
    if len(low) and len(st) >= 2:
        med = float(st["on"].median()) or 1.0
        low["rel"] = low["on"] / med * 100
        low["flag"] = np.where(low["on"] < LOW_USE, f"{LOW_USE:g}명 미만", np.where(low["rel"] < 25, "중앙값의 25% 미만", "-"))
        res.tables.append(AggTable(
            key="transit.low", title=f"이용 하위 정류소 {len(low)}곳(검토 후보)", df=low[["bus_station_name"] + oc + ["rel", "flag"]],
            dims=["bus_station_name", "flag"], values=oc + ["rel"],
            kind={**{c: "simple" for c in oc}, "rel": "applied"},
            sources=[stops_src, src], unit={**{c: "명" for c in oc}, "rel": "%"},
            labels={"bus_station_name": "정류소", "on": "일평균 승차", "off": "일평균 하차", "rel": "중앙값 대비", "flag": "판정"},
            note="반출 가능 데이터(티머니)이므로 10 미만 수치도 공개 가능. 판정: 일평균 승차 5명 미만 또는 지역 중앙값의 25% 미만.",
        ))
        res.charts.append(ChartSpec("c.low", "transit.low", "barh", "이용 하위 정류소(일평균 승차)", x="bus_station_name",
                                    y=["on"], sort=True))
        res.facts["n_review_stops"] = int((low["flag"] != "-").sum())
    on = on.copy()
    on["day_type"] = np.where(on["date"].dt.weekday >= 5, "주말", "평일")
    dt_days = on.groupby("day_type")["date"].nunique()
    hourly = "base_hour" in on.columns and num(on["base_hour"]).notna().any()
    if hourly:
        # 요일 × 시간 히트맵
        on["dow"] = on["date"].dt.weekday.map(lambda i: DOW[i] if pd.notna(i) else None)
        on["hour"] = num(on["base_hour"]).astype("Int64")
        days_per_dow = on.groupby("dow")["date"].nunique()
        hm = on.groupby(["dow", "hour"])["user_count"].sum().reset_index()
        hm["avg"] = hm["user_count"] / hm["dow"].map(days_per_dow)
        hm["dow"] = pd.Categorical(hm["dow"], DOW, ordered=True)
        hm = hm.sort_values(["dow", "hour"]).reset_index(drop=True)
        hm["dow"] = hm["dow"].astype(str)
        res.tables.append(AggTable(
            key="transit.heat", title="요일×시간대 평균 승차 인원", df=hm[["dow", "hour", "avg"]], dims=["dow", "hour"],
            values=["avg"], kind={"avg": "simple"}, sources=[src], unit={"avg": "명"},
            labels={"dow": "요일", "hour": "시각", "avg": "평균 승차"},
        ))
        res.charts.append(ChartSpec("c.heat", "transit.heat", "heat", "요일×시간대 평균 승차 인원", x="hour", y=["avg"], series="dow"))
        # 평일/주말 시간대 곡선
        hc = on.groupby(["day_type", "hour"])["user_count"].sum().reset_index()
        hc["avg"] = hc["user_count"] / hc["day_type"].map(dt_days)
        res.tables.append(AggTable(
            key="transit.hourly", title="시간대별 평균 승차(평일/주말)", df=hc[["day_type", "hour", "avg"]],
            dims=["day_type", "hour"], values=["avg"], kind={"avg": "simple"}, sources=[src], unit={"avg": "명"},
            labels={"day_type": "구분", "hour": "시각", "avg": "평균 승차"},
        ))
        res.charts.append(ChartSpec("c.hourly", "transit.hourly", "line", "시간대별 평균 승차(평일/주말)", x="hour", y=["avg"],
                                    series="day_type", y_label="명"))
    else:
        # 일 단위 자료(허브 613 등): 요일별 일평균 승차
        on["dow"] = on["date"].dt.weekday.map(lambda i: DOW[i] if pd.notna(i) else None)
        dd = on.groupby(["date", "dow"])["user_count"].sum().reset_index()
        dw = dd.groupby("dow")["user_count"].mean().reindex(DOW).dropna().rename("avg").reset_index()
        res.tables.append(AggTable(
            key="transit.dow", title="요일별 일평균 승차(환승 포함)", df=dw, dims=["dow"], values=["avg"],
            kind={"avg": "simple"}, sources=[src], unit={"avg": "명"}, labels={"dow": "요일", "avg": "일평균 승차"},
        ))
        res.charts.append(ChartSpec("c.dow", "transit.dow", "bar", "요일별 일평균 승차", x="dow", y=["avg"], y_label="명"))
        hc = pd.DataFrame(columns=["day_type", "hour", "avg"])
        b512 = data.get("hub:512")
        if b512 is not None and len(b512):
            island_profile(res, b512, context=True)
    # KPI
    wk = hc[hc["day_type"] == "평일"].set_index("hour")["avg"]
    peak_h = int(wk.idxmax()) if len(wk) else None
    peak_share = float(wk.max() / wk.sum() * 100) if len(wk) and wk.sum() else np.nan
    wkd = float(on[on["day_type"] == "평일"]["user_count"].sum() / max(1, dt_days.get("평일", 0)))
    wke = float(on[on["day_type"] == "주말"]["user_count"].sum() / max(1, dt_days.get("주말", 0))) if "주말" in dt_days else np.nan
    transfer = u[u["get_on_off_type"].astype(str) == "환승"]["user_count"].sum()
    board = on["user_count"].sum()
    kpi = pd.DataFrame({
        "지표": ["분석 정류소 수(곳)", "일평균 승차(환승 포함, 명)", "평일 첨두 1시간 집중률(%)", "주말/평일 승차 비율(%)", "환승 비율(%)",
                f"저이용 정류소 수(일평균 {LOW_USE:g}명 미만)", "점검 후보 정류소 수(5명 미만 또는 중앙값 25% 미만)"],
        "value": [n_stops, board / n_days, peak_share, (wke / wkd * 100) if wkd else np.nan,
                  transfer / board * 100 if board else np.nan, int((st["on"] < LOW_USE).sum()),
                  int(res.facts.get("n_review_stops", 0))],
    })
    kpi = kpi[pd.to_numeric(kpi["value"], errors="coerce").notna()].reset_index(drop=True)
    res.tables.append(AggTable(
        key="transit.kpi", title="버스 이용 핵심 지표", df=kpi, dims=["지표"], values=["value"], kind={"value": "simple"},
        sources=[src], unit={"value": ""}, labels={"value": "값"},
    ))
    res.kpis += [
        KPI("일평균 승차", "transit.kpi", {"지표": "일평균 승차(환승 포함, 명)"}, "value", "명"),
        KPI("평일 첨두 집중률", "transit.kpi", {"지표": "평일 첨두 1시간 집중률(%)"}, "value", "%"),
        KPI("주말/평일 비율", "transit.kpi", {"지표": "주말/평일 승차 비율(%)"}, "value", "%"),
        KPI("점검 후보 정류소", "transit.kpi", {"지표": "점검 후보 정류소 수(5명 미만 또는 중앙값 25% 미만)"}, "value", "곳"),
    ]
    res.facts.update({"peak_hour": peak_h, "n_stops": n_stops,
                      "busiest_stop": str(st.iloc[0]["bus_station_name"]) if len(st) else None})
    # 이용자 구성
    if "user_category" in on.columns:
        uc = on.groupby("user_category")["user_count"].sum()
        uc = (uc / uc.sum() * 100).rename("share").reset_index()
        res.tables.append(AggTable(
            key="transit.users", title="이용자 구분별 승차 비중", df=uc, dims=["user_category"], values=["share"],
            kind={"share": "applied"}, sources=[src], unit={"share": "%"},
            labels={"user_category": "이용자 구분", "share": "승차 비중"},
        ))
        res.charts.append(ChartSpec("c.users", "transit.users", "donut", "이용자 구분별 승차 비중", x="user_category", y=["share"]))
        if len(uc):
            res.facts["elder_share"] = float(uc.set_index("user_category")["share"].get("경로", 0.0))
    # 정류소 지도용 이용량
    use_map = dict(zip(st["bus_station_id"].astype(str), st["on"]))
    for p in res.map_points:
        if p.get("type") == "stop":
            p["value"] = float(use_map.get(p.get("id", ""), 0.0))
    _routes(res, data)
    return res


def latest_block(u: pd.DataFrame, res: AnalysisResult | None = None, date_col: str = "date") -> pd.DataFrame:
    """여러 해가 섞여 있으면 '가장 최근에 연속된 달'만 사용(예: 2023.11~12 + 2024.11~12 → 2024.11~12)."""
    if u.empty or u[date_col].isna().all():
        return u
    ym = (u[date_col].dt.year * 100 + u[date_col].dt.month)
    months = sorted(ym.dropna().astype(int).unique())
    block = [months[-1]]
    for m in reversed(months[:-1]):
        y, mm = divmod(block[0], 100)
        prev = (y - 1) * 100 + 12 if mm == 1 else y * 100 + mm - 1
        if m == prev:
            block.insert(0, m)
        else:
            break
    if len(block) < len(months):
        if res is not None:
            res.notes.append(f"이용 현황에 떨어진 기간이 섞여 있어 가장 최근 연속 기간({block[0] // 100}.{block[0] % 100:02d}~"
                             f"{block[-1] // 100}.{block[-1] % 100:02d})만 분석했습니다.")
        return u[ym.isin(block)]
    return u


def _in_area(s: pd.DataFrame, bjd_names: list[str]) -> pd.Series:
    from ..catalog import regions as R

    cents = {b["name"]: (b["lon"], b["lat"]) for b in R.bjd_list() if b.get("lon") is not None}
    from .common import nearest_assign

    near = nearest_assign(s, cents)
    return near.isin(set(bjd_names))


def _routes(res: AnalysisResult, data: dict[str, pd.DataFrame]):
    if "3-02" not in data:
        return
    r = data["3-02"].copy()
    r["user_count"] = num(r["user_count"])
    r["date"] = pd.to_datetime(r["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")
    r = latest_block(r)
    days_route = r.groupby("bus_route_name")["date"].nunique()
    g = r.groupby("bus_route_name")["user_count"].sum()
    g = (g / g.index.map(days_route).values).sort_values(ascending=False).head(15).reset_index()
    g.columns = ["route", "daily"]
    res.used_sources.append("3-02")
    res.tables.append(AggTable(
        key="transit.routes", title="노선별 일평균 승차 상위 15", df=g, dims=["route"], values=["daily"],
        kind={"daily": "simple"}, sources=["3-02"], unit={"daily": "명"}, labels={"route": "노선", "daily": "일평균 승차"},
    ))
    res.charts.append(ChartSpec("c.routes", "transit.routes", "barh", "노선별 일평균 승차 상위", x="route", y=["daily"], sort=True))


# ---------------------------------------------------------------------------------------- 허브 512(제주 전역)
BAND_ORDER = ["01시-06시", "07시-09시", "10시-12시", "13시-15시", "16시-18시", "19시-21시", "22시-24시"]
BAND_HOURS = {"01시-06시": 6, "07시-09시": 3, "10시-12시": 3, "13시-15시": 3, "16시-18시": 3, "19시-21시": 3, "22시-24시": 3}
USER_GROUP_512 = {"일반": "일반", "청소년": "청소년", "어린이": "어린이", "경로": "경로",
                  "장애 일반": "장애·유공", "장애 동반": "장애·유공", "유공 일반": "장애·유공", "유공 동반": "장애·유공",
                  "기타": "기타"}


def _ym_prev_year(ym: str) -> str:
    return f"{int(ym[:4]) - 1}{ym[4:]}"


def island_profile(res: AnalysisResult, b: pd.DataFrame, context: bool = False) -> None:
    """허브 공개 실데이터 512(일별 시간대별 버스 승차 인원, 제주 전역)로 이용 패턴을 진단한다.

    - 최근 12개월(자료 마지막 날 기준)만 사용. 일 합계가 중앙값의 30% 미만인 날은 집계 누락으로 보고 제외
    - 시간대 길이가 달라(01~06시는 6시간) 시간대 비교는 '시간당 승차'로 환산
    - context=True 이면 정류소 단위 분석의 보조(참고) 표만 추가
    """
    d = b.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["user_count"] = num(d["user_count"])
    d["time_band"] = d["time_band"].astype(str)
    d["user_type"] = d["user_type"].astype(str)
    tot = d.groupby("date")["user_count"].sum()
    med = float(tot.median()) if len(tot) else 0.0
    bad = set(tot[tot < med * 0.3].index)
    if bad:
        res.notes.append(f"허브 512 자료 중 일 합계가 중앙값의 30% 미만인 {len(bad)}일은 집계 누락으로 보고 제외했습니다.")
    d = d[~d["date"].isin(bad)]
    if d.empty:
        return
    end = d["date"].max()
    start = end - pd.Timedelta(days=364)
    cur = d[(d["date"] >= start) & (d["date"] <= end)].copy()
    period = f"{cur['date'].min():%Y.%m.%d}~{cur['date'].max():%Y.%m.%d}"
    src = "hub:512"
    res.used_sources.append(src)
    pre = "ctx." if context else "isle."
    if not context:
        res.params["period"] = period
    cur["dow"] = cur["date"].dt.weekday.map(lambda i: DOW[i])
    cur["day_type"] = np.where(cur["date"].dt.weekday >= 5, "주말", "평일")
    daily = cur.groupby(["date", "dow", "day_type"])["user_count"].sum().reset_index()

    yoy = np.nan
    if not context:
        # 1) 월별 일평균 승차 추이(전체 기간)
        allm = d.groupby("date")["user_count"].sum().reset_index()
        allm["ym"] = allm["date"].dt.strftime("%Y.%m")
        mm = allm.groupby("ym")["user_count"].agg(["mean", "count"]).reset_index()
        mm = mm[mm["count"] >= 20].rename(columns={"mean": "avg"}).reset_index(drop=True)
        if len(mm):
            res.tables.append(AggTable(
                key=pre + "monthly", title=f"제주 전역 월별 일평균 버스 승차({mm['ym'].iloc[0]}~{mm['ym'].iloc[-1]})",
                df=mm[["ym", "avg"]], dims=["ym"], values=["avg"], kind={"avg": "simple"},
                sources=[src], unit={"avg": "명"}, labels={"ym": "년월", "avg": "일평균 승차"},
                note="자료가 20일 미만인 달(자료 시작·끝)은 제외. 집계 누락 의심일 제외 후 평균.",
            ))
            res.charts.append(ChartSpec("c.isle_monthly", pre + "monthly", "line", "제주 전역 월별 일평균 버스 승차",
                                        x="ym", y=["avg"], y_label="명"))
            mi = mm.set_index("ym")["avg"]
            last_m = mm["ym"].iloc[-1]
            same = [m for m in mi.index if m > _ym_prev_year(last_m) and _ym_prev_year(m) in mi.index]
            if same:
                yoy = float(mi[same].mean() / mi[[_ym_prev_year(m) for m in same]].mean() * 100 - 100)
                res.facts["isle_yoy_months"] = f"{same[0]}~{same[-1]}"

    # 2) 요일 × 시간대 시간당 평균 승차
    days_dow = daily.groupby("dow")["date"].nunique()
    hb = cur.groupby(["dow", "time_band"])["user_count"].sum().reset_index()
    hb["avg"] = hb["user_count"] / hb["dow"].map(days_dow) / hb["time_band"].map(BAND_HOURS)
    hb["dow"] = pd.Categorical(hb["dow"], DOW, ordered=True)
    hb["time_band"] = pd.Categorical(hb["time_band"], BAND_ORDER, ordered=True)
    hb = hb.sort_values(["dow", "time_band"]).reset_index(drop=True)
    hb["dow"], hb["time_band"] = hb["dow"].astype(str), hb["time_band"].astype(str)
    res.tables.append(AggTable(
        key=pre + "heat", title=f"제주 전역 요일×시간대 시간당 평균 승차({period})", df=hb[["dow", "time_band", "avg"]],
        dims=["dow", "time_band"], values=["avg"], kind={"avg": "simple"}, sources=[src], unit={"avg": "명"},
        labels={"dow": "요일", "time_band": "시간대", "avg": "시간당 평균 승차"},
        note="시간대 길이가 달라(01~06시 6시간, 그 외 3시간) 시간당 값으로 환산.",
    ))
    if not context:
        res.charts.append(ChartSpec("c.isle_heat", pre + "heat", "heat", "제주 전역 요일×시간대 시간당 평균 승차",
                                    x="time_band", y=["avg"], series="dow"))

    # 3) 평일/주말 시간대 곡선(시간당)
    dt_days = daily.groupby("day_type")["date"].nunique()
    bc = cur.groupby(["day_type", "time_band"])["user_count"].sum().reset_index()
    bc["avg"] = bc["user_count"] / bc["day_type"].map(dt_days) / bc["time_band"].map(BAND_HOURS)
    bc["time_band"] = pd.Categorical(bc["time_band"], BAND_ORDER, ordered=True)
    bc = bc.sort_values(["day_type", "time_band"]).reset_index(drop=True)
    bc["time_band"] = bc["time_band"].astype(str)
    res.tables.append(AggTable(
        key=pre + "band", title=f"제주 전역 시간대별 시간당 평균 승차(평일/주말, {period})",
        df=bc[["day_type", "time_band", "avg"]], dims=["day_type", "time_band"], values=["avg"], kind={"avg": "simple"},
        sources=[src], unit={"avg": "명"}, labels={"day_type": "구분", "time_band": "시간대", "avg": "시간당 평균 승차"},
    ))
    res.charts.append(ChartSpec(f"c.{pre.rstrip('.')}_band", pre + "band", "line",
                                "제주 전역 시간대별 시간당 평균 승차(평일/주말)", x="time_band", y=["avg"],
                                series="day_type", y_label="명/시간"))

    # 4) 이용자 유형 구성
    cur["ug"] = cur["user_type"].map(USER_GROUP_512).fillna("기타")
    ug = cur.groupby("ug")["user_count"].sum()
    ug = (ug / ug.sum() * 100).sort_values(ascending=False).rename("share").reset_index()
    res.tables.append(AggTable(
        key=pre + "users", title=f"제주 전역 이용자 유형별 승차 비중({period})", df=ug, dims=["ug"], values=["share"],
        kind={"share": "applied"}, sources=[src], unit={"share": "%"}, labels={"ug": "이용자 유형", "share": "비중"},
    ))
    if context:
        return
    res.charts.append(ChartSpec("c.isle_users", pre + "users", "donut", "이용자 유형별 승차 비중", x="ug", y=["share"]))

    # 5) 핵심 지표
    wk = bc[bc["day_type"] == "평일"].set_index("time_band")["avg"]
    peak_band = str(wk.idxmax()) if len(wk) else None
    wkd = float(daily[daily["day_type"] == "평일"]["user_count"].mean())
    wke = float(daily[daily["day_type"] == "주말"]["user_count"].mean())
    elder = float(ug.set_index("ug")["share"].get("경로", np.nan))
    peak_ratio = float(wk.max() / wk.mean() * 100) if len(wk) and wk.mean() else np.nan
    rows = [("일평균 승차(제주 전역, 명)", float(daily["user_count"].mean())),
            ("평일 최다 시간대 집중도(시간당, 평일 평균=100)", peak_ratio),
            ("주말/평일 일평균 승차 비율(%)", wke / wkd * 100 if wkd else np.nan),
            ("경로 승차 비중(%)", elder),
            ("최근 승차 전년 같은 달 대비 증감률(%)", yoy)]
    kpi = pd.DataFrame(rows, columns=["지표", "value"])
    kpi = kpi[pd.to_numeric(kpi["value"], errors="coerce").notna()].reset_index(drop=True)
    res.tables.append(AggTable(
        key=pre + "kpi", title=f"제주 전역 버스 이용 핵심 지표({period})", df=kpi, dims=["지표"], values=["value"],
        kind={"value": "applied"}, sources=[src], unit={"value": ""}, labels={"value": "값"},
    ))
    res.kpis += [
        KPI("일평균 승차", pre + "kpi", {"지표": "일평균 승차(제주 전역, 명)"}, "value", "명"),
        KPI("평일 최다 시간대 집중도", pre + "kpi", {"지표": "평일 최다 시간대 집중도(시간당, 평일 평균=100)"}, "value", ""),
        KPI("주말/평일 비율", pre + "kpi", {"지표": "주말/평일 일평균 승차 비율(%)"}, "value", "%"),
        KPI("경로 승차 비중", pre + "kpi", {"지표": "경로 승차 비중(%)"}, "value", "%"),
        KPI("전년 같은 달 대비", pre + "kpi", {"지표": "최근 승차 전년 같은 달 대비 증감률(%)"}, "value", "%±"),
    ]
    res.facts.update({"peak_band": peak_band, "isle_period": period, "isle_days": int(daily["date"].nunique())})
