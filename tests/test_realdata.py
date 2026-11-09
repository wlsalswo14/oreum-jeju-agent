"""제주데이터허브 실데이터 경로 검증: 허브 512·583·612·613 변환, 제주 전역 버스 진단, 허브만으로 하는 분석.

- 변환·분석 로직은 작은 고정 데이터로 결정적으로 확인
- 번들된 실데이터(data/public/*.parquet)가 있으면 대표 시나리오 3종이 센터 CSV 없이 끝까지 돌아가는지 확인
"""
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.agent.rule_writer import write_rule
from app.analysis import event_impact, transit
from app.analysis.common import AnalysisResult, RegionSel
from app.guard import export_guard as eg
from app.hub import public_data as P

PUB = Path(__file__).resolve().parents[1] / "data" / "public"


# ------------------------------------------------------------------------------------- 변환
def test_stop_key_normalizes_spacing_and_marks():
    assert P.stop_key("모슬포 남항 여객선 터미널(운진항)") == P.stop_key("모슬포남항여객선터미널(운진항)")
    assert P.stop_key("제주 버스·터미널") == "제주버스터미널"


def test_api_frames_map_to_center_formats():
    pop = P.to_pop_frame(pd.DataFrame([{"baseDate": "20241128", "city": "서귀포시", "emd": "대정읍", "gender": "남",
                                        "ageGroup": "20대", "resdPop": 10, "workPop": 2, "visitPop": 30}]))
    assert list(pop.columns) == ["base_date", "time", "emd_nm", "sex", "age", "resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"]
    assert pop.loc[0, "emd_nm"] == "서귀포시 대정읍" and pop.loc[0, "age"] == "20" and pop.attrs["daily"]
    st = P.to_stops_frame(pd.DataFrame([{"stationId": "1", "stationName": "A", "longitude": "126.1", "latitude": "33.2"}]))
    assert st.loc[0, "longitude"] == pytest.approx(126.1)
    us = P.to_usage_frame(pd.DataFrame([{"baseDate": "20241128", "stationId": "9", "stationName": "A", "moveType": "승차",
                                         "priceType": "일반", "userCount": "7"}]))
    assert us.loc[0, "get_on_off_type"] == "승차" and us.loc[0, "user_count"] == 7


# ------------------------------------------------------------------------------------- 512 제주 전역
def _fake512(days=420, drop_day=None):
    rows = []
    start = date(2024, 9, 1)
    bands = transit.BAND_ORDER
    for i in range(days):
        d = start + timedelta(days=i)
        for b in bands:
            peak = 3.0 if (b == "16시-18시" and d.weekday() < 5) else 1.0
            for ut, base in (("일반", 100), ("경로", 25)):
                v = base * peak * transit.BAND_HOURS[b] / 3
                if d == drop_day:
                    v = 1
                rows.append([pd.Timestamp(d), "월", b, ut, int(v)])
    return pd.DataFrame(rows, columns=["date", "dow", "time_band", "user_type", "user_count"])


def test_island_profile_finds_peak_and_drops_broken_days():
    bad = date(2025, 3, 3)
    res = AnalysisResult(mode="transit", title="t", params={})
    transit.island_profile(res, _fake512(drop_day=bad))
    keys = {t.key for t in res.tables}
    assert {"isle.monthly", "isle.heat", "isle.band", "isle.users", "isle.kpi"} <= keys
    assert res.facts["peak_band"] == "16시-18시"
    assert any("결측" in n or "누락" in n for n in res.notes)          # 비정상적으로 작은 날 제외 안내
    kpi = next(t for t in res.tables if t.key == "isle.kpi").df.set_index("지표")["value"]
    assert kpi["경로 승차 비중(%)"] == pytest.approx(20.0, abs=0.5)       # 25/(100+25)
    assert all(r.ok for r in eg.check_all(res.tables))                  # 허브 공개 데이터 → 반출 가능


def test_transit_uses_512_when_no_stop_data_and_writes_island_text():
    res = transit.analyze({"hub:512": _fake512()}, None, None, None)
    assert res.title.startswith("제주 전역") and any(t.key == "isle.kpi" for t in res.tables)
    kp = [{"label": "일평균 승차", "value": "1,000명"}, {"label": "전년 같은 달 대비", "value": "+1.0%"}]
    out = write_rule("transit", res.title, kp, res.facts, res.notes, False, res.params, {t.key for t in res.tables})
    assert "제주 전역" in out["headline"] and "정류소별 승하차" not in out["summary"]
    assert not any("카드" in x for x in out["limitations"])             # 쓰지 않은 데이터의 한계는 넣지 않음


def test_daily_stop_usage_skips_hourly_heatmap():
    stops = pd.DataFrame({"bus_station_id": ["a", "b"], "bus_station_name": ["A", "B"],
                          "longitude": [126.25, 126.26], "latitude": [33.22, 33.22]})
    rows = []
    for i in range(7):
        d = (date(2025, 10, 13) + timedelta(days=i)).strftime("%Y%m%d")
        rows += [[d, "a", "A", "승차", "일반", 40], [d, "b", "B", "승차", "일반", 3], [d, "a", "A", "환승", "일반", 5]]
    usage = pd.DataFrame(rows, columns=["base_date", "bus_station_id", "bus_station_name", "get_on_off_type",
                                        "user_category", "user_count"])
    region = RegionSel(label="대정읍", bjd=["대정읍"], center=(126.255, 33.222), radius_km=3)
    res = transit.analyze({}, region, stops, usage, src="hub:613", stops_src="hub:612")
    keys = {t.key for t in res.tables}
    assert "transit.top" in keys and "transit.dow" in keys and "transit.heat" not in keys
    low = next(t for t in res.tables if t.key == "transit.low").df
    assert "B" in set(low["bus_station_name"])                           # 일평균 3명 → 점검 후보


# ------------------------------------------------------------------------------------- 축제(일 단위 유동인구)
def test_event_with_daily_population_has_no_hourly_table_and_finds_lift():
    rows = []
    s, e = date(2024, 11, 28), date(2024, 12, 1)
    for i in range(-25, 10):
        d = s + timedelta(days=i)
        for emd, boost in (("서귀포시 대정읍", 2.0), ("서귀포시 안덕면", 1.0)):
            v = 1000 * (boost if s <= d <= e else 1.0)
            rows.append({"baseDate": d.strftime("%Y%m%d"), "city": "서귀포시", "emd": emd.split()[-1], "gender": "남",
                         "ageGroup": "20대", "resdPop": 500, "workPop": 50, "visitPop": v})
    pop = P.to_pop_frame(pd.DataFrame(rows))
    region = RegionSel(label="대정읍", bjd=["대정읍"], emd=["서귀포시 대정읍"], center=(126.25, 33.22))
    control = RegionSel(label="안덕면", bjd=["안덕면"], emd=["서귀포시 안덕면"])
    res = event_impact.analyze({"hub:583": pop}, region, s, e, "방어축제", control=control)
    keys = {t.key for t in res.tables}
    assert "event.daily" in keys and "event.hourly" not in keys
    lift = next(t for t in res.tables if t.key == "event.lift").df.set_index("지표")["value"]
    assert lift.iloc[0] == pytest.approx(100.0, abs=0.1)                  # 2배 → +100%
    assert lift["순효과(이중차분, %p)"] == pytest.approx(100.0, abs=0.1)


# ------------------------------------------------------------------------------------- 번들 실데이터(있을 때)
need_bundle = pytest.mark.skipif(not (PUB / "card_mashup.parquet").exists(), reason="허브 공개 데이터 번들 없음")


@need_bundle
@pytest.mark.parametrize("question,mode", [
    ("애월읍에서 카페를 창업하려는데 상권 어때?", "trade_area"),
    ("제주 버스 이용 패턴을 진단해 주세요", "transit"),
])
def test_scenarios_run_on_real_hub_data_without_center_csv(tmp_path, monkeypatch, question, mode):
    from app.agent.orchestrator import Agent
    from app.config import settings
    from app.session import SessionStore

    monkeypatch.setattr(settings, "hub_project_key", "")          # 키 없이도(스냅샷·내장 데이터만으로) 동작
    monkeypatch.setattr(settings, "llm_enabled", False)
    import app.report.builder as B

    monkeypatch.setattr(B, "html_to_pdf", lambda *a, **k: None)     # 테스트에서는 브라우저 인쇄 생략
    s = SessionStore(tmp_path).create()
    a = Agent(s)
    st = a.plan(question)
    assert st["plan"]["mode"] == mode
    st = a.run()
    assert not st.get("error"), st.get("error")
    r = st["result"]
    assert r["kpis"] and not r["synthetic"]
    assert all(src["id"].startswith("hub:") for src in r["sources"])


def test_event_days_are_excluded_from_normal_bus_diagnosis_and_no_alighting_column():
    stops = pd.DataFrame({"bus_station_id": ["a", "b"], "bus_station_name": ["A", "B"],
                          "longitude": [126.25, 126.26], "latitude": [33.22, 33.22]})
    rows = []
    for i in range(10):
        d = date(2024, 11, 20) + timedelta(days=i)
        boost = 10 if d >= date(2024, 11, 28) else 1          # 행사일에는 승차 10배
        rows += [[d.strftime("%Y%m%d"), "a", "A", "승차", "일반", 40 * boost],
                 [d.strftime("%Y%m%d"), "b", "B", "승차", "일반", 20]]
    usage = pd.DataFrame(rows, columns=["base_date", "bus_station_id", "bus_station_name", "get_on_off_type",
                                        "user_category", "user_count"])
    region = RegionSel(label="대정읍", bjd=["대정읍"], center=(126.255, 33.222), radius_km=3)
    ev = {date(2024, 11, 28) + timedelta(days=k): "방어축제" for k in range(4)}
    res = transit.analyze({}, region, stops, usage, src="hub:613", stops_src="hub:612", event_days=ev)
    top = next(t for t in res.tables if t.key == "transit.top").df.set_index("bus_station_name")
    assert top.loc["A", "on"] == pytest.approx(40.0)                       # 행사일 제외 → 평시 값
    assert "off" not in top.columns                                         # 하차 자료 없으면 하차 열 없음
    assert any("행사일" in n and "제외" in n for n in res.notes)


def test_number_check_reads_compound_korean_amounts():
    from app.agent.narrator import unverified_numbers

    ev = "| 애월읍 | 822,717,055원 |\n| 한림읍 | 250,383,390원 |"
    assert unverified_numbers("애월읍 8억 2,272만 원, 한림읍 2억 5,038만 원", ev) == []
    assert unverified_numbers("애월읍 8억 3,000만 원", ev) == ["8억 3,000만"]     # 틀린 수치는 여전히 잡아냄
