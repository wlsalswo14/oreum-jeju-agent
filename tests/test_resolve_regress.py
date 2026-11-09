"""QA에서 발견된 문제의 회귀 테스트(입력 해석·실행 차단·비교지역·버스 일평균)."""
from datetime import date, timedelta

import pandas as pd

from app.agent import resolve
from app.agent.planner import rule_plan
from app.analysis import transit
from app.analysis.common import RegionSel


def _rv(q):
    p = rule_plan(q)
    return p, resolve.resolve_plan(p, q)


def test_missing_industry_or_region_is_blocking_issue():
    _, rv = _rv("애월읍 상권 분석해줘")
    assert any("업종" in i for i in rv["issues"])
    _, rv = _rv("카페 창업하려는데 어디가 좋을까?")
    assert any("지역" in i for i in rv["issues"])


def test_festival_year_without_dates_offers_other_editions():
    _, rv = _rv("2023 최남단 방어축제 효과는?")
    assert rv["issues"] and rv["event"]["start"] == ""
    assert any(o["start"] == "2024-11-28" for o in rv.get("event_options", []))
    assert rv["event"]["from_catalog"] is False


def test_catalog_dates_win_over_llm_guess():
    p = rule_plan("2024 최남단 방어축제 효과")
    p.source, p.event_start, p.event_end = "llm", "2024-11-01", "2024-11-03"   # LLM 추정 날짜
    rv = resolve.resolve_plan(p, "2024 최남단 방어축제 효과")
    assert rv["event"]["start"] == "2024-11-28"


def test_control_regions_exclude_venue():
    _, rv = _rv("제63회 탐라문화제 효과")
    treated = set(rv["region_sel"].bjd)
    assert {"건입동", "일도일동", "삼도이동"} <= treated
    assert rv["control_sel"] is not None and not (set(rv["control_sel"].bjd) & treated)


def test_multi_region_control_text():
    s = resolve.multi_region_sel("안덕면·한경면")
    assert s.bjd == ["안덕면", "한경면"] and len(s.emd) == 2


def test_bus_daily_average_uses_days_per_stop_and_latest_block():
    rows = []
    d0 = date(2024, 11, 1)
    for i in range(30):  # 2024.11: 두 정류소 모두 매일 10명
        for sid in ("A", "B"):
            rows.append([(d0 + timedelta(days=i)).strftime("%Y%m%d"), "08", sid, sid, "승차", "일반", 10])
    for i in range(30):  # 2023.11: A만 기록(떨어진 기간 → 제외되어야 함)
        rows.append([(date(2023, 11, 1) + timedelta(days=i)).strftime("%Y%m%d"), "08", "A", "A", "승차", "일반", 999])
    for i in range(10):  # 2024.11 중 C는 10일만 운행, 하루 20명
        rows.append([(d0 + timedelta(days=i)).strftime("%Y%m%d"), "08", "C", "C", "승차", "일반", 20])
    u = pd.DataFrame(rows, columns=["base_date", "base_hour", "bus_station_id", "bus_station_name", "get_on_off_type",
                                    "user_category", "user_count"])
    r = transit.analyze({}, None, None, u)
    top = r.tables[0].df.set_index("bus_station_name")["on"]
    assert top["A"] == 10 and top["B"] == 10 and top["C"] == 20   # C는 운행한 10일 기준 하루 20명
    assert any("최근 연속 기간" in n for n in r.notes)
