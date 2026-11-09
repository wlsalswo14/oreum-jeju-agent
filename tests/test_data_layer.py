"""데이터 계층 테스트: 카탈로그 인식·로더·지역/업종/축제 인식·허브 공개데이터·허브 API 클라이언트(목)·가상 데이터.

실행: .venv/Scripts/python.exe -m pytest tests/test_data_layer.py -q
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import festivals as F  # noqa: E402
from app.catalog import industries as I  # noqa: E402
from app.catalog import regions as R  # noqa: E402
from app.catalog.catalog import load_catalog  # noqa: E402
from app.hub import client as HC  # noqa: E402
from app.hub import public_data as P  # noqa: E402
from app.ingest.loader import inspect_file, load_files  # noqa: E402

DEMO = ROOT / "data" / "demo_center"
CORE = ["1-08", "1-12", "1-14", "1-17", "1-19", "4-10", "4-11", "3-01", "3-02", "3-03", "3-04", "5-02"]


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def demo_files() -> list[Path]:
    return sorted(DEMO.glob("SYNTHETIC_*.csv"))


needs_demo = pytest.mark.skipif(not demo_files(), reason="data/demo_center 없음(tools/make_demo_data.py 실행 필요)")


# ============================================================================== 카탈로그
def test_catalog_contents(cat):
    assert len(cat.datasets) == 71                      # 명세서 v13: 데이터셋 71종(+목록·참고표 2 = 74시트)
    assert cat.version.startswith("v13")
    ref = cat.reference
    assert len(ref["districts7"]) == 7
    assert len(ref["bjd73"]) == 74 and len({b["name"] for b in ref["bjd73"]}) == 73
    assert {b["city"] for b in ref["bjd73"] if b["name"] == "월평동"} == {"제주시", "서귀포시"}
    assert len(ref["emd43"]) == 43 and ref["emd43"][0] == {"code": "50110250", "name": "제주시 한림읍",
                                                         "city": "제주시", "short": "한림읍"}
    assert len(ref["biz42"]) == 42 and {"code": "I56220", "name": "비알콜 음료점업"} in ref["biz42"]
    assert ref["card_time_bands"]["오후"] == "14~18시"
    assert len(ref["pos_biz_codes"]) > 100
    for i in CORE:
        assert cat.get(i).verified, i
    d = cat.get("1-08")
    assert d.measures == ["user_cnt", "use_cnt", "use_amt"] and d.time_col == "base_year_month"
    assert d.grain == "month" and d.version_tag == "v02" and d.supersedes == "1-07" and d.family == "card_bjd"
    assert d.region_cols["bjd"] == "법정동" and not d.exportable
    assert cat.get("4-11").supersedes == "4-10" and cat.get("4-10").grain == "hour"
    assert cat.exportable("3-04") and not cat.exportable("4-10") and cat.exportable("hub:793")
    assert cat.get("1-8").id == "1-08" and cat.get("746").id == "hub:746"


def test_describe_and_search(cat):
    txt = cat.describe_for_llm(["1-12", "4-10"])
    assert "week" in txt and "공휴일" in txt and "resd_pop_cnt" in txt
    assert "365160" not in cat.describe_for_llm(["1-08"])     # 예시 값은 넣지 않음
    ids = [d.id for d in cat.search("버스 정류장 이용")]
    assert ids[0] == "3-04"
    assert [d.id for d in cat.search("유동인구")][0] == "4-11"
    assert "1-07" not in [d.id for d in cat.search("카드 읍면동")]  # 구버전은 기본 제외


@pytest.mark.parametrize("ds_id", [k for k in json.loads((ROOT / "app/catalog/spec_catalog.json").read_text(
    encoding="utf-8"))["datasets"] if k not in ("1-05", "1-06", "2-02", "5-06")])
def test_detect_full_header_self(cat, ds_id):
    cols = cat.get(ds_id).column_names
    assert cat.detect(cols).dataset_id == ds_id
    shuffled = cols[:]
    random.Random(1).shuffle(shuffled)
    assert cat.detect(shuffled).dataset_id == ds_id


def test_detect_identical_headers_need_values(cat):
    # 헤더가 완전히 같은 쌍은 값으로 구분
    assert cat.detect(cat.get("1-05").column_names, values={"user_type": ["중국인관광객"]}).dataset_id == "1-05"
    assert cat.detect(cat.get("1-02").column_names, values={"user_type": ["내국인관광객"]}).dataset_id == "1-02"
    assert cat.detect(cat.get("2-02").column_names, values={"sale_buy_code": ["판매"]}).dataset_id == "2-02"


@pytest.mark.parametrize("cols,values,expected", [
    # 공식 답변 예시 행(파티션 컬럼 없음) → 요일별 v02
    (["base_year_month", "dstrct_se", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "week", "age", "sex",
      "user_cnt", "use_cnt", "use_amt"], None, "1-12"),
    (["base_year_month", "bjd", "biz_cd", "week", "use_amt"], None, "1-12"),
    (["use_amt", "time", "biz_cd", "bjd", "base_year_month"], None, "1-14"),
    (["base_year_month", "area_type", "biz_cd", "week", "use_amt"], {"area_type": ["제주시 동부"]}, "1-11"),
    (["base_year_month", "area_type", "biz_cd", "time", "use_cnt"], None, "1-13"),
    (["base_year_month", "bjd", "biz_cd", "use_amt"], {"base_year_month": ["202411"]}, "1-08"),
    (["base_year_month", "bjd", "biz_cd", "use_amt"], {"base_year_month": ["201905"]}, "1-07"),
    (["base_year_month", "city", "owner_gb", "biz_cd", "store_cnt"], None, "1-17"),
    (["base_date", "time", "emd_cd", "emd_nm", "resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt", "resd"],
     {"base_date": ["20241128"]}, "4-10"),
    (["base_date", "time", "emd_cd", "emd_nm", "visit_pop_cnt"], {"base_date": ["20250601"]}, "4-11"),
    (["base_date", "time", "dstrct_se", "emd_nm", "visit_pop_cnt"], None, "4-11"),
    (["base_date", "time", "nationality", "emd_nm", "visit_pop_cnt"], {"base_date": ["20241128"]}, "4-14"),
    (["base_date", "base_hour", "bus_station_id", "get_on_off_type", "user_count"], None, "3-04"),
    (["base_date", "base_hour", "bus_route_id", "bus_route_name", "user_count"], None, "3-02"),
    (["bus_station_id", "bus_station_name", "longitude", "latitude"], None, "3-03"),
    (["base_date", "observatory_code", "average_temperature", "daily_rainfall"], None, "5-02"),
    (["base_year_month", "bjd_nm", "biz_small_cd", "sale_amt"], None, "1-19"),
    (["base_date", "day_of_week", "base_hour", "user_type", "user_count"], None, "hub:512"),
    (["년월", "읍면동명", "업종코드", "이용금액"], None, "hub:746"),
])
def test_detect_subsets(cat, cols, values, expected):
    r = cat.detect(cols, values=values)
    assert r.dataset_id == expected, [(c.dataset_id, c.raw_score) for c in r.candidates]
    assert 0 < r.score <= 1


@pytest.mark.parametrize("v1,v2", [("4-10", "4-11"), ("1-07", "1-08"), ("1-11", "1-12"), ("1-13", "1-14")])
def test_detect_v01_v02_full(cat, v1, v2):
    assert cat.detect(cat.get(v1).column_names).dataset_id == v1
    assert cat.detect(cat.get(v2).column_names).dataset_id == v2
    r = cat.detect(cat.get(v1).column_names)
    assert v2 in [c.dataset_id for c in r.candidates]
    assert set(r.missing_cols) == set()


def test_detect_reports_missing_extra(cat):
    cols = [c for c in cat.get("1-08").column_names if c not in ("sex", "age")] + ["memo"]
    r = cat.detect(cols)
    assert r.dataset_id == "1-08" and set(r.missing_cols) == {"sex", "age"} and r.extra_cols == ["memo"]
    assert cat.detect(["foo", "bar"]).dataset_id is None


# ============================================================================== 지역
def test_regions_tables():
    assert len(R.bjd_list()) == 74 and len(R.hjd_list()) == 43
    assert all(b["lon"] and b["hjd"] for b in R.bjd_list())
    assert R.bjd_to_hjd("이도이동") == "이도2동" and R.bjd_to_hjd("아라일동") == "아라동"
    assert R.bjd_to_hjd("월평동", "서귀포시") == "대천동" and R.bjd_to_hjd("월평동", "제주시") == "아라동"
    assert set(R.hjd_to_bjd("제주시 용담2동")) == {"용담이동", "용담삼동"}
    assert R.card_bjd_to_pop_emd("일도일동") == {"emd_cd": "50110510", "emd_nm": "제주시 일도1동", "share": 1.0}
    assert "애월읍" in R.pop_emd_to_card_bjd("50110253")
    assert R.bjd_to_dstrct("노형동") == ("제주시 중부(서)", False)          # 명세서 예시로 확정
    assert R.bjd_to_dstrct("대정읍") == ("서귀포시 서부", False)            # 공식 답변 예시로 확정
    d, est = R.bjd_to_dstrct("애월읍")
    assert d == "제주시 서부" and est is True
    assert [n["name"] for n in R.neighbors("대정읍", 2)] == ["안덕면", "한경면"]


@pytest.mark.parametrize("text,bjd,level", [
    ("애월읍에서 카페 창업하려는데 상권 어때?", "애월읍", "bjd"), ("애월 카페", "애월읍", "bjd"),
    ("모슬포 버스 정류소", "대정읍", "alias"), ("중문 호텔", "중문동", "alias"), ("성산 일출", "성산읍", "alias"),
    ("제주시청 근처 술집", "이도이동", "alias"), ("서귀포 시내 한식", "서귀동", "alias"),
    ("일도1동 유동인구", "일도일동", "hjd"), ("협재 카페", "한림읍", "alias"), ("하모리", "대정읍", "ri"),
    ("노형 치킨", "노형동", "bjd"), ("함덕", "조천읍", "alias"),
])
def test_resolve_region(text, bjd, level):
    m = R.resolve_region(text)[0]
    assert bjd in m.bjd and m.level == level
    assert m.emd_nm and m.lon and m.lat
    kw = m.region_sel_kwargs()
    assert kw["bjd"] and kw["emd"]


def test_resolve_region_ambiguous_and_district():
    ms = R.resolve_region("서귀포 월평동")
    assert ms[0].city == "서귀포시" and len(ms) == 2
    m = R.resolve_region("제주시 서부 권역")[0]
    assert m.level == "dstrct" and {"애월읍", "한림읍", "한경면"} <= set(m.bjd)
    assert R.resolve_region("서귀포시")[0].level == "city"


def test_learn_dstrct_overrides():
    df = pd.DataFrame({"bjd": ["추자면"], "dstrct_se": ["제주시 동부"], "city": ["제주시"]})
    try:
        R.learn_dstrct_from_frame(df)
        assert R.bjd_to_dstrct("추자면") == ("제주시 동부", False)
    finally:
        R.reload()
    assert R.bjd_to_dstrct("추자면")[1] is True


# ============================================================================== 업종
@pytest.mark.parametrize("text,code", [
    ("애월읍에서 카페를 창업하려고 하는데", "I56220"), ("커피숍", "I56220"), ("한식당", "I56111"),
    ("흑돼지 고깃집", "I56111"), ("횟집", "I56111"), ("숙소", "I55111"), ("호텔", "I55111"), ("펜션", "I55112"),
    ("렌터카", "L69110"), ("편의점", "G47122"), ("빵집", "G47215"), ("베이커리", "G47215"), ("술집", "I56219"),
    ("호프", "I56219"), ("치킨집", "I56192"), ("비알콜 음료점업", "I56220"), ("I56113", "I56113"),
])
def test_resolve_industry(text, code):
    m = I.resolve_industry(text)[0]
    assert m.code == code and m.name and m.label and isinstance(m.store_keywords, list)
    if text in ("횟집", "치킨집", "펜션"):
        assert m.note


def test_industry_store_mapping_and_pos():
    s = P.stores()
    bc = I.classify_stores(s)
    assert (bc == "I56220").sum() > 2000 and (bc == "I56111").sum() > 5000
    g = I.count_competitors("I56220", stores=s)
    assert {"법정동명", "store_cnt"} <= set(g.columns) and g["store_cnt"].sum() == (bc == "I56220").sum()
    assert any(p["small_cd"] == "Q10020" for p in I.pos_lookup("횟집"))
    assert {p["mid_cd"] for p in I.pos_codes_for("I56220")} == {"Q13"}
    for code, rule in I.STORE_RULES.items():   # 매핑 규칙의 표준산업분류명은 실제 793 값이어야 함
        for k in rule.get("ksic", []) + rule.get("related", []):
            if k not in ("기타 대형 종합 소매업", "백화점", "면세점", "골프장 운영업", "기타 갬블링 및 베팅업"):
                assert (s["표준산업분류명"] == k).any(), (code, k)


# ============================================================================== 축제
def test_search_festival_sentence():
    m = F.search_festival("2024 최남단 방어축제가 대정읍 지역경제에 준 효과를 알고 싶어요")[0]
    assert m.name == "최남단 방어축제" and (m.start, m.end) == ("2024-11-28", "2024-12-01")
    assert m.bjd == ["대정읍"] and m.region == "대정읍" and m.confidence == "high"
    assert m.emd_nm == ["서귀포시 대정읍"] and abs(m.lon - 126.25) < 0.02 and abs(m.lat - 33.22) < 0.02


@pytest.mark.parametrize("text,year,name,start", [
    ("제63회 탐라문화제", None, "탐라문화제", "2024-10-05"),
    ("탐라문화제", 2024, "탐라문화제", "2024-10-05"),
    ("서귀포 칠십리축제", 2024, "서귀포 칠십리축제", "2024-10-18"),
    ("방어 축제", 2024, "최남단 방어축제", "2024-11-28"),
    ("한림공원 튤립축제", 2024, "한림공원 꽃축제", "2024-03-29"),
    ("왕벚꽃", 2024, "제주왕벚꽃축제", "2024-03-22"),
])
def test_search_festival_names(text, year, name, start):
    m = F.search_festival(text, year=year)[0]
    assert m.name == name and m.start == start and m.bjd


def test_search_festival_missing_year_keeps_location():
    m = F.search_festival("최남단 방어축제", year=2023)[0]
    assert m.start is None and m.bjd == ["대정읍"] and "2023" in m.note
    assert F.normalize_name("제 24회 2024 최남단 방어축제(2024년 취소)") == "최남단방어축제"
    voc = F.festival_vocab()
    assert voc[0] == "최남단 방어축제" and len(voc) > 100


# ============================================================================== 허브 공개 데이터
def test_public_data():
    s = P.stores()
    for c in ["상호명", "상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "표준산업분류명", "법정동명", "행정동명",
              "경도", "위도", "bc_biz_cd", "bjd", "lon", "lat"]:
        assert c in s.columns
    assert len(s) > 50000 and s.attrs["source_id"] == "hub:793"
    c = P.card_mashup()
    assert {"base_year_month", "bjd", "biz_cd", "user_type", "tour_type", "use_amt"} <= set(c.columns)
    assert c["base_year_month"].min() == "201701" and c["base_year_month"].max() == "202105"
    assert not isinstance(c["bjd"].dtype, pd.CategoricalDtype)
    pop = P.card_pop_mashup()
    assert pop["emd_nm"].str.contains(r"^(?:제주시|서귀포시) ").all()
    f = P.festivals()
    assert {"name", "start", "end", "bjd", "confidence"} <= set(f.columns)
    assert (f["end"] >= f["start"]).all()
    assert len(P.bus_daily_timeband()) > 30000
    prov = json.loads((ROOT / "data/public/PROVENANCE.json").read_text(encoding="utf-8"))
    assert set(prov["datasets"]) == {"512", "583", "612", "613", "746", "748", "761", "793"}
    assert all(d["source_url"].startswith("https://jejudatahub.net/data/view/data/") for d in prov["datasets"].values())
    assert sum(p.stat().st_size for p in (ROOT / "data/public").glob("*")) < 15e6


# ============================================================================== 로더
def _pick(prefix: str, n: int) -> list[Path]:
    fs = [f for f in demo_files() if f.name.startswith(prefix)]
    return fs[:n]


@needs_demo
def test_detect_every_synthetic_file(cat):
    for f in demo_files():
        expect = f.name.split("_")[1]
        fi = inspect_file(f, cat)
        assert fi.dataset_id == expect, (f.name, fi.candidates)


@needs_demo
def test_loader_merge_months_and_duplicates(tmp_path):
    a, b = _pick("SYNTHETIC_1-08_", 2)
    shutil.copy(a, tmp_path / a.name)
    shutil.copy(b, tmp_path / b.name)
    shutil.copy(a, tmp_path / "renamed_copy_of_first.csv")         # 같은 파일 두 번 업로드
    # 같은 달 일부 행만 다시 담은 파일(내용 겹침) → 겹치는 행 제거
    lines = a.read_text(encoding="utf-8").splitlines()
    (tmp_path / "partial_overlap.csv").write_text("\n".join(lines[:200]) + "\n", encoding="utf-8")
    rep = load_files(sorted(tmp_path.glob("*.csv")))
    d = rep.datasets["1-08"]
    n_a = len(pd.read_csv(a, dtype=str))
    n_b = len(pd.read_csv(b, dtype=str))
    assert d.row_count == n_a + n_b
    assert len(rep.duplicates) == 1 and d.duplicates_removed == 199
    assert d.months == ["2023-01", "2023-02"] and d.missing_periods == []
    assert d.coverage_text == "2023.01~2023.02"
    assert d.source_label.startswith("제주빅데이터센터, 카드 이용 현황(읍면동)_v02(2023.01~2023.02)")
    assert d.is_synthetic
    assert str(d.df["use_amt"].dtype) in ("int64", "Int64") and d.df["biz_cd"].map(type).eq(str).all()
    assert d.df["age"].isna().any()                                   # 'NA' → 결측
    assert "df" not in repr(d) or "DataFrame" not in repr(d)          # 행 데이터 노출 없음
    assert "rows" in d.summary() and "df" not in d.summary()


@needs_demo
def test_loader_gaps_encoding_and_subset(tmp_path):
    fs = _pick("SYNTHETIC_1-17_", 3)
    shutil.copy(fs[0], tmp_path / "jan.csv")
    shutil.copy(fs[2], tmp_path / "mar.csv")
    # CP949 로 저장한 파일도 인식
    (tmp_path / "x_cp949.csv").write_bytes(fs[1].read_text(encoding="utf-8").encode("cp949"))
    rep = load_files([tmp_path])
    d = rep.datasets["1-17"]
    assert d.months == ["2024-01", "2024-02", "2024-03"]
    assert {f.encoding for f in rep.files} == {"utf-8", "cp949"}
    # 2월 제외 → 누락 기간
    rep2 = load_files([tmp_path / "jan.csv", tmp_path / "mar.csv"])
    assert rep2.datasets["1-17"].missing_periods == ["2024-02"]
    # 4-10 가상 데이터는 성별·연령 미선택(부분 헤더) → 4-10 인식 + 누락 컬럼 보고
    f410 = _pick("SYNTHETIC_4-10_", 1)[0]
    r = load_files([f410])
    d4 = r.datasets["4-10"]
    assert set(d4.missing_cols) == {"sex", "age"} and d4.df["visit_pop_cnt"].dtype.kind == "f"
    assert d4.coverage_text == "2023.11.01~2023.11.30" and len(d4.days) == 30


def test_loader_board_example_and_unrecognized(tmp_path):
    hdr = "base_year_month,dstrct_se,bjd,biz_cd,biz_nm,user_type,tour_type,week,age,sex,user_cnt,use_cnt,use_amt\n"
    row = '202607,"서귀포시 서부","대정읍","I56111","한식 음식점업","내국인","단체","공휴일","60","여성","4","1","1000000"\n'
    row2 = '202607,"서귀포시 서부","대정읍","I56111","한식 음식점업","법인",NA,"월요일",NA,"알수없음","12","13","1,250,000"\n'
    (tmp_path / "q.csv").write_text(hdr + row + row2, encoding="utf-8-sig")
    (tmp_path / "junk.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    rep = load_files([tmp_path / "q.csv", tmp_path / "junk.csv"])
    d = rep.datasets["1-12"]
    assert d.df["use_amt"].tolist() == [1000000, 1250000] and d.df["tour_type"].isna().tolist() == [False, True]
    assert d.df["age"].tolist()[0] == "60" and not d.is_synthetic
    assert [f.name for f in rep.unrecognized] == ["junk.csv"] and rep.unrecognized[0].path.name == "junk.csv"


def test_loader_hub_public_file(tmp_path):
    p = tmp_path / "746_sample.csv"
    p.write_text("년월,시도명,지역구분,읍면동명,업종코드,업종명,이용자 구분,관광구분,연령대,성별,이용금액,매장수,업종명 대분류,데이터기준일자\n"
                 "2021-01,제주시,읍면,애월읍,I56220,비알콜 음료점업,내국인,개별,30.0,여성,150000,573,음식점업,2021-07-26\n",
                 encoding="cp949")
    d = load_files([p]).datasets["hub:746"]
    assert d.source.startswith("hub") or d.id == "hub:746"
    assert d.months == ["2021-01"] and d.source_label.startswith("제주데이터허브,")
    assert d.df["이용금액"].iloc[0] == 150000


# ============================================================================== 허브 API 클라이언트(목)
class FakeHub:
    """number/limit 으로 페이지를 돌려주는 가짜 서버."""

    def __init__(self, total=250, ignore_number=False, status=200, has_more_field=True):
        self.total, self.ignore, self.status, self.hm = total, ignore_number, status, has_more_field
        self.calls: list[str] = []
        self.rows = [{"baseDate": "20241128", "emd": "대정읍", "visitPop": i} for i in range(total)]

    def __call__(self, url, timeout):
        self.calls.append(url)
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        n = 1 if self.ignore else int(q["number"][0])
        lim = int(q["limit"][0])
        if self.status != 200:
            return self.status, b'{"message":"denied"}'
        data = self.rows[(n - 1) * lim: n * lim]
        body = {"totCnt": self.total, "data": data}
        if self.hm:
            body["hasMore"] = n * lim < self.total
        return 200, json.dumps(body).encode("utf-8")


def _client(tmp_path, fake, **kw):
    from datetime import date
    return HC.HubClient("TESTKEY123", cache_dir=tmp_path, transport=fake, sleep=lambda s: None,
                        today=lambda: date(2026, 11, 8), **kw)


def test_hub_pagination_and_cache(tmp_path):
    fake = FakeHub(total=250)
    c = _client(tmp_path, fake)
    r = c.fetch("583", {"startDate": "20241128", "endDate": "20241201", "emd": "대정읍"})
    assert len(r.records) == 250 and r.pages == 3 and r.api_calls == 3 and r.complete and r.total == 250
    assert "number=1" in fake.calls[0] and "limit=100" in fake.calls[0]
    assert "/Daaa1t3at3tt8a8DD3t55538t35Dab1t/TESTKEY123?" in fake.calls[0]
    r2 = c.fetch("population", {"startDate": "2024-11-28", "endDate": "2024-12-01", "emd": "대정읍"})
    assert r2.api_calls == 0 and r2.from_cache == 3 and len(r2.records) == 250    # 과거 기간 → 영구 캐시
    assert c.quota_status("583")["used"] == 3
    df = r.to_frame()
    assert list(df.columns)[:3] == ["baseDate", "city", "emd"] and "2024.11.28~2024.12.01" in df.attrs["source_label"]


def test_hub_stop_conditions(tmp_path):
    fake = FakeHub(total=250, ignore_number=True)        # 서버가 페이지 번호를 무시 → 무한루프 방지
    r = _client(tmp_path, fake).fetch("612", {}, use_cache=False)
    assert r.pages == 2 and len(r.records) == 100 and r.warnings
    fake = FakeHub(total=1000)
    r = _client(tmp_path / "b", fake).fetch("612", max_pages=3, use_cache=False)
    assert r.pages == 3 and not r.complete and len(r.records) == 300 and r.warnings
    fake = FakeHub(total=150, has_more_field=False)      # hasMore 없으면 totCnt 로 종료
    r = _client(tmp_path / "c", fake).fetch("613", {"startDate": "20241101", "endDate": "20241101"}, use_cache=False)
    assert r.complete and len(r.records) == 150 and r.api_calls == 2


def test_hub_errors_and_quota(tmp_path):
    with pytest.raises(HC.HubKeyMissing):
        HC.HubClient("", cache_dir=tmp_path, transport=FakeHub()).fetch("612")
    with pytest.raises(HC.HubError):
        _client(tmp_path, FakeHub()).fetch("583", {"startDate": "20241101"})            # endDate 누락
    with pytest.raises(HC.HubError):
        _client(tmp_path, FakeHub()).fetch("612", {"foo": "bar"})
    with pytest.raises(HC.HubAuthError) as e:
        _client(tmp_path, FakeHub(status=401)).fetch("612", use_cache=False)
    assert "TESTKEY123" not in str(e.value)
    with pytest.raises(HC.HubHTTPError) as e:
        _client(tmp_path, FakeHub(status=500), retries=1).fetch("612", use_cache=False)
    assert "TESTKEY123" not in str(e.value)
    c = _client(tmp_path / "q", FakeHub(total=10_000), daily_quota=5, quota_reserve=1)
    with pytest.raises(HC.HubQuotaExceeded):
        c.fetch("612", max_pages=50, use_cache=False)
    assert c.quota_status("612")["used"] == 4


# ============================================================================== 가상 데이터
@needs_demo
def test_demo_manifest_and_format():
    m = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))
    assert m["synthetic"] is True and m["total_bytes"] < 62e6
    assert {"1-08", "1-12", "1-14", "1-17", "4-10", "3-02", "3-03", "3-04", "5-02"} <= set(m["datasets"])
    assert m["datasets"]["1-08"]["periods"][0] == "202301" and m["datasets"]["1-08"]["periods"][-1] == "202412"
    for f in m["files"]:
        assert f["file"].startswith(f"SYNTHETIC_{f['dataset_id']}_") and f["file"].endswith(f"_{f['period']}.csv")
    f = _pick("SYNTHETIC_1-12_", 1)[0]
    raw = f.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    hdr, first = raw.decode("utf-8").splitlines()[:2]
    assert hdr == ",".join(load_catalog().get("1-12").column_names)
    assert first.startswith("202401,") and first.split(",")[1] == '"2024"' and first.endswith('"')


@needs_demo
def test_demo_scenario_signals():
    rep = load_files([DEMO])
    c = rep.datasets["1-08"].df
    amt = lambda b, z, yms: c[(c.bjd == b) & (c.biz_cd == z) & c.base_year_month.isin(yms)]["use_amt"].sum()  # noqa
    y23 = [f"2023{m:02d}" for m in range(1, 13)]
    y24 = [f"2024{m:02d}" for m in range(1, 13)]
    g_aw = amt("애월읍", "I56220", y24) / amt("애월읍", "I56220", y23) - 1
    g_nb = sum(amt(b, "I56220", y24) for b in ("한림읍", "조천읍", "구좌읍")) / \
        sum(amt(b, "I56220", y23) for b in ("한림읍", "조천읍", "구좌읍")) - 1
    assert g_aw > 0.10 and g_aw - g_nb > 0.07
    did = (amt("대정읍", "I56111", ["202411"]) / amt("대정읍", "I56111", ["202311"])) - \
        (amt("안덕면", "I56111", ["202411"]) + amt("한경면", "I56111", ["202411"])) / \
        (amt("안덕면", "I56111", ["202311"]) + amt("한경면", "I56111", ["202311"]))
    assert did > 0.12
    assert ((c["use_cnt"] < 10).mean()) > 0.15                     # 10 미만 셀이 충분히 존재
    w = rep.datasets["1-12"].df
    aw = w[(w.bjd == "애월읍") & (w.biz_cd == "I56220")].groupby("week")["use_amt"].sum()
    assert aw["토요일"] > 1.5 * aw["화요일"]
    t = rep.datasets["1-14"].df
    at = t[(t.bjd == "애월읍") & (t.biz_cd == "I56220")].groupby("time")["use_amt"].sum()
    assert at.idxmax() == "오후"
    p = rep.datasets["4-10"].df
    dj = p[p.emd_nm == "서귀포시 대정읍"].groupby("base_date")["visit_pop_cnt"].sum()
    assert dj["20241130"] > 2.5 * dj["20241121"]
    assert dj["20231130"] < 1.3 * dj["20231121"]                   # 2023년 동기에는 효과 없음
    u = rep.datasets["3-04"].df
    on = u[u.get_on_off_type.isin(["승차", "환승"])]
    daily = on.groupby(["bus_station_name", "base_date"])["user_count"].sum()
    assert daily["모슬포항"]["20241130"] > 2 * daily["모슬포항"]["20241121"]
    avg = on[on.base_date.str.startswith("2024")].groupby("bus_station_name")["user_count"].sum() / 61
    assert (avg < 5).sum() >= 2 and avg.idxmax() == "모슬포항"
    peak = on[on.bus_station_name == "모슬포항"].groupby(["base_date", "base_hour"])["user_count"].sum()
    assert peak.max() >= 100
    stops = rep.datasets["3-03"].df
    lon, lat = pd.to_numeric(stops.longitude), pd.to_numeric(stops.latitude)
    near_dj = ((lon - 126.25).abs() < 0.04) & ((lat - 33.22).abs() < 0.035)
    near_aw = ((lon - 126.38).abs() < 0.05) & ((lat - 33.46).abs() < 0.04)
    assert near_dj.sum() >= 10 and near_aw.sum() >= 4 and len(stops) > 150
    # 모슬포(126.25, 33.22) 반경 3km 안에도 저이용(일평균 승차 5명 미만) 정류소가 2곳 이상
    km = ((lon - 126.25) * 93.0) ** 2 + ((lat - 33.22) * 111.0) ** 2
    within3 = set(stops.loc[km <= 9, "bus_station_name"])
    assert len([n for n, v in avg.items() if v < 5 and n in within3]) >= 2


@needs_demo
def test_demo_deterministic(tmp_path):
    import subprocess
    out = tmp_path / "demo"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_demo_data.py"), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    m = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))
    for f in m["files"]:
        assert hashlib.sha1((out / f["file"]).read_bytes()).hexdigest() == f["sha1"], f["file"]
