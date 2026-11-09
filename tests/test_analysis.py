"""분석 도구 3종 검증: 가상 데이터에 심어 둔 효과를 분석이 찾아내는지 확인(신뢰성·재현성)."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analysis import event_impact, trade_area, transit
from app.analysis.common import IndustrySel, RegionSel
from app.guard import export_guard as eg

BJDS = ["애월읍", "한림읍", "노형동", "대정읍", "안덕면", "한경면"]


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(7)
    months = [y * 100 + m for y in (2023, 2024) for m in range(1, 13)]
    rows = []
    for ym in months:
        for b in BJDS:
            for code, nm in [("I56220", "비알콜 음료점업"), ("I56111", "한식 음식점업")]:
                for ut, tt in [("내국인", "개별"), ("제주도민", "도민"), ("중국", "개별")]:
                    for age in ["20", "30", "50"]:
                        for sex in ["남성", "여성"]:
                            base = 40 if (b == "애월읍" and code == "I56220") else 15
                            if ym // 100 == 2024 and b == "애월읍":
                                base *= 1.2          # 애월 성장 +20%
                            if ym in (202411, 202412) and b == "대정읍" and code == "I56111":
                                base *= 1.5          # 축제월 대정 한식 +50%
                            cnt = int(rng.poisson(base))
                            if cnt:
                                rows.append([str(ym), b, code, nm, ut, tt, age, sex, cnt, cnt, cnt * 10000])
    c108 = pd.DataFrame(rows, columns=["base_year_month", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "age",
                                       "sex", "user_cnt", "use_cnt", "use_amt"])
    wk = [[str(ym), "애월읍", "I56220", w, c, c * 9000] for ym in months[-12:]
          for w, c in zip(["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"],
                          [40, 40, 40, 40, 50, 90, 85, 30])]
    c112 = pd.DataFrame(wk, columns=["base_year_month", "bjd", "biz_cd", "week", "use_cnt", "use_amt"])
    pop = []
    for i in range(70):
        d = date(2024, 10, 20) + timedelta(days=i)
        for emd in ["서귀포시 대정읍", "서귀포시 안덕면"]:
            for h in range(1, 25):
                ev = emd.endswith("대정읍") and date(2024, 11, 28) <= d <= date(2024, 12, 1)
                pop.append([d.strftime("%Y%m%d"), str(h), emd, "남성", "2024", 300.0, 50.0, 100.0 * (1.6 if ev else 1.0)])
    p410 = pd.DataFrame(pop, columns=["base_date", "time", "emd_nm", "sex", "age", "resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"])
    stops = pd.DataFrame({"bus_station_id": ["1", "2", "3"], "bus_station_name": ["모슬포항", "대정읍사무소", "외딴정류소"],
                          "longitude": [126.252, 126.256, 126.262], "latitude": [33.214, 33.226, 33.230]})
    bu = []
    for i in range(70):
        d = date(2024, 10, 20) + timedelta(days=i)
        for sid, nm, base in [("1", "모슬포항", 8), ("2", "대정읍사무소", 6), ("3", "외딴정류소", 0.1)]:
            for h in range(6, 22):
                mult = 2.0 if sid in ("1", "2") and date(2024, 11, 28) <= d <= date(2024, 12, 1) else 1.0
                bu.append([d.strftime("%Y%m%d"), f"{h:02d}", sid, nm, "승차", "일반", int(base * mult)])
    b304 = pd.DataFrame(bu, columns=["base_date", "base_hour", "bus_station_id", "bus_station_name", "get_on_off_type",
                                     "user_category", "user_count"])
    return {"1-08": c108, "1-12": c112, "4-10": p410}, stops, b304


def test_trade_area_detects_growth_and_weekend(data):
    d, stops, bus = data
    r = trade_area.analyze(d, RegionSel("애월읍", ["애월읍"], ["제주시 애월읍"], (126.33, 33.46)),
                           IndustrySel("I56220", "비알콜 음료점업", "카페"))
    g = {x.table.key: x for x in eg.check_all(r.tables)}
    kpi = g["trade.kpi_a"].table.df.set_index("지표")["value"]
    assert 10 < kpi["전년 대비 증감률(%)"] < 30            # 심어 둔 +20%
    assert kpi["업종 내 매출 순위(위)"] == 1
    wk = g["trade.week"].table.df.set_index("week")["idx"]
    assert wk["토요일"] > 130 and wk["월요일"] < 100
    assert r.facts["peak_week"] == "토요일"
    assert all(x.ok for x in g.values())


def test_event_impact_detects_festival(data):
    d, stops, bus = data
    r = event_impact.analyze(d, RegionSel("대정읍", ["대정읍"], ["서귀포시 대정읍"], (126.25, 33.22)),
                             date(2024, 11, 28), date(2024, 12, 1), "최남단 방어축제",
                             control=RegionSel("안덕면", ["안덕면"], ["서귀포시 안덕면"]), stops=stops, bus_usage=bus)
    assert 50 < r.facts["visit_lift"] < 70                 # 심어 둔 +60%
    assert 50 < r.facts["visit_did"] < 70
    assert 80 < r.facts["bus_lift"] < 120                  # 심어 둔 2배
    g = {x.table.key: x for x in eg.check_all(r.tables)}
    card = g["event.card"].table.df.set_index("biz")
    assert card.loc["한식 음식점업", "did"] > 30            # 축제월 한식 +50%


def test_transit_finds_low_use_stop(data):
    d, stops, bus = data
    r = transit.analyze({}, RegionSel("대정읍", ["대정읍"], center=(126.25, 33.22), radius_km=3), stops, bus)
    g = {x.table.key: x for x in eg.check_all(r.tables)}
    assert "외딴정류소" in set(g["transit.low"].table.df["bus_station_name"])
    assert r.facts["busiest_stop"] == "모슬포항"
    assert all(x.status == "pass" for x in g.values())    # 교통 데이터는 반출 가능


def test_deterministic(data):
    d, stops, bus = data
    sel = RegionSel("애월읍", ["애월읍"], ["제주시 애월읍"], (126.33, 33.46))
    ind = IndustrySel("I56220", "비알콜 음료점업")
    a = trade_area.analyze(d, sel, ind)
    b = trade_area.analyze(d, sel, ind)
    for x, y in zip(a.tables, b.tables):
        pd.testing.assert_frame_equal(x.df, y.df)
