"""번들된 제주데이터허브 공개 데이터 로더(data/public/*.parquet, tools/prepare_public.py 생성).

모든 함수는 DataFrame 을 반환하며 `df.attrs` 에 source_id('hub:793' 등)·source_label(출처 표기)·source_url 을 담는다.
범주형(category)은 일반 문자열로 바꿔서 반환한다(groupby 시 관측되지 않은 조합 폭증 방지).
허브 공개 데이터는 원본 포함 반출 가능 자료이다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

PUBLIC_DIR = Path(__file__).resolve().parents[2] / "data" / "public"


class PublicDataMissing(FileNotFoundError):
    pass


@lru_cache(maxsize=1)
def provenance() -> dict:
    p = PUBLIC_DIR / "PROVENANCE.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"datasets": {}}


def source_label(data_id: str) -> str:
    """출처 표기: '제주데이터허브, <데이터명>(기간)'."""
    d = provenance().get("datasets", {}).get(str(data_id).replace("hub:", ""), {})
    title = d.get("title", f"데이터 {data_id}")
    period = d.get("period", "")
    return f"제주데이터허브, {title}({period})" if period else f"제주데이터허브, {title}"


def _read(name: str, data_id: str) -> pd.DataFrame:
    p = PUBLIC_DIR / name
    if not p.exists():
        raise PublicDataMissing(f"{p} 가 없습니다. `python tools/prepare_public.py` 로 생성하세요.")
    df = pd.read_parquet(p)
    for c in df.columns:
        if isinstance(df[c].dtype, pd.CategoricalDtype):
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    d = provenance().get("datasets", {}).get(data_id, {})
    df.attrs.update(source_id=f"hub:{data_id}", source_label=source_label(data_id),
                    source_url=d.get("source_url", f"https://jejudatahub.net/data/view/data/{data_id}"))
    return df


def _out(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.attrs = dict(df.attrs)
    return out


# ---------------------------------------------------------------------------------- 793 상가
@lru_cache(maxsize=1)
def _stores() -> pd.DataFrame:
    from ..catalog.industries import classify_stores

    df = _read("stores.parquet", "793")
    df["bc_biz_cd"] = classify_stores(df)
    df["bc_biz_cd_related"] = classify_stores(df, include_related=True)
    # 분석 코드 호환용 영문 별칭
    df["bjd"] = df["법정동명"]
    df["hjd"] = df["행정동명"]
    df["city"] = df["시군구명"]
    df["lon"] = df["경도"]
    df["lat"] = df["위도"]
    df["large"] = df["상권업종대분류명"]
    df["mid"] = df["상권업종중분류명"]
    df["sub"] = df["상권업종소분류명"]
    df["std"] = df["표준산업분류명"]
    return df


def stores() -> pd.DataFrame:
    """[793] 상가(상권) 정보: 원본 한글 컬럼(상호명·업종 대/중/소·표준산업분류명·시군구·행정동·법정동·리·코드·경도·위도)
    + bc_biz_cd(BC 42업종 매핑, 근사 포함은 bc_biz_cd_related) + 영문 별칭(bjd, hjd, city, lon, lat, large, mid, sub, std)."""
    return _out(_stores())


# ---------------------------------------------------------------------------------- 746 카드 매쉬업
@lru_cache(maxsize=1)
def _card_mashup() -> pd.DataFrame:
    df = _read("card_mashup.parquet", "746")
    df["base_year_month"] = df["ym"].str.replace("-", "", regex=False)
    df["tour_type"] = df["tour_type"].replace({"NA": None})
    return df


def card_mashup() -> pd.DataFrame:
    """[746] 업종별 카드 매출 매쉬업(2017.01~2021.05): 월×시군구×도심구분×법정동×업종×이용자구분×관광구분 이용금액 합계.
    컬럼명은 카드 이용 현황(1-07/1-08) 명세와 동일(base_year_month, bjd, biz_cd, user_type, tour_type, use_amt …).
    use_cnt·user_cnt 는 2017년만 값이 있다."""
    return _out(_card_mashup())


def card_mashup_stores() -> pd.DataFrame:
    """[746] 월×시군구×업종 매장수(BC 가맹점 기준)."""
    df = _read("card_mashup_stores.parquet", "746")
    df["base_year_month"] = df["ym"].str.replace("-", "", regex=False)
    return df


def card_mashup_demo() -> pd.DataFrame:
    """[746] 연도×법정동×업종×이용자구분×관광구분×연령대×성별 이용금액(연간 합계, n_months=포함 개월 수)."""
    df = _read("card_mashup_demo.parquet", "746")
    df["tour_type"] = df["tour_type"].replace({"NA": None})
    df["age"] = df["age"].replace({"NA": None})
    return df


# ---------------------------------------------------------------------------------- 748 유동인구-카드
@lru_cache(maxsize=1)
def _card_pop() -> pd.DataFrame:
    s = _read("card_pop_sales.parquet", "748")
    p = _read("card_pop_population.parquet", "748")
    df = s.merge(p, on=["ym", "city", "emd", "sex"], how="left")
    df["base_year_month"] = df["ym"].str.replace("-", "", regex=False)
    df["emd_nm"] = df["city"] + " " + df["emd"]
    df.attrs = dict(s.attrs)
    return df


def card_pop_mashup() -> pd.DataFrame:
    """[748] 유동인구-카드매출 매쉬업(2017.01~2021.05): 월×행정동×업종×성별 이용자수·이용금액 + 같은 월×행정동×성별의
    거주·근무·방문인구(월간 시간대별 측정 합 = 연인원). emd_nm 은 유동인구(4-10) 형식('제주시 아라동')."""
    return _out(_card_pop())


def card_pop_population() -> pd.DataFrame:
    """[748] 월×행정동×성별 거주·근무·방문인구(연인원)."""
    df = _read("card_pop_population.parquet", "748")
    df["emd_nm"] = df["city"] + " " + df["emd"]
    return df


# ---------------------------------------------------------------------------------- 761 축제
def festival_events() -> pd.DataFrame:
    """[761] VISIT JEJU 축제행사(정리본): name, start, end, start_time, end_time, fee, organizer, host, sponsor,
    cancelled, days."""
    return _read("festivals.parquet", "761")


def festivals() -> pd.DataFrame:
    """[761] 축제행사 + 장소 큐레이션(app/catalog/festivals.py): curated_id, venue, bjd, hjd, lon, lat, confidence."""
    from ..catalog.festivals import attach_locations

    df = festival_events()
    out = attach_locations(df)
    out.attrs = dict(df.attrs)
    return out


# ---------------------------------------------------------------------------------- 512 버스
def bus_daily_timeband() -> pd.DataFrame:
    """[512] 일별 시간대별 버스 승차 인원(제주 전체): date, dow, time_band('07시-09시' 등 7구간), user_type, user_count."""
    return _read("bus_daily_timeband.parquet", "512")


def public_frames(keys: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """분석 엔진 입력용 {'hub:793': stores(), 'hub:746': card_mashup(), …} (없는 파일은 건너뜀)."""
    funcs = {"hub:793": stores, "hub:746": card_mashup, "hub:748": card_pop_mashup, "hub:761": festivals,
             "hub:512": bus_daily_timeband}
    out = {}
    for k, f in funcs.items():
        if keys and k not in keys:
            continue
        try:
            out[k] = f()
        except PublicDataMissing:
            continue
    return out


# ---------------------------------------------------------------------------------- Open API 스냅샷(583·612·613)
AGE583 = {"10세미만": "10", "10대": "10", "20대": "20", "30대": "30", "40대": "40", "50대": "50", "60대": "60",
          "70대": "70", "70세이상": "70", "70대이상": "70", "80대": "70", "80세이상": "70"}


def to_pop_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """583 응답(baseDate·city·emd·gender·ageGroup·resdPop·workPop·visitPop) → 유동인구(4-11) 분석 형식.

    일 단위 자료이므로 time 은 0 하나만 둔다(분석기는 time 이 하나뿐이면 시간대 분석을 건너뛴다).
    """
    d = rows.copy()
    out = pd.DataFrame({
        "base_date": d["baseDate"].astype(str),
        "time": 0,
        "emd_nm": d["city"].astype(str) + " " + d["emd"].astype(str),
        "sex": d["gender"].astype(str),
        "age": d["ageGroup"].astype(str).map(AGE583).fillna("NA"),
        "resd_pop_cnt": pd.to_numeric(d["resdPop"], errors="coerce"),
        "work_pop_cnt": pd.to_numeric(d["workPop"], errors="coerce"),
        "visit_pop_cnt": pd.to_numeric(d["visitPop"], errors="coerce"),
    })
    out.attrs["daily"] = True
    return out


@lru_cache(maxsize=1)
def _pop583() -> pd.DataFrame:
    return _read("pop583_daily.parquet", "583")


def pop583() -> pd.DataFrame:
    """[583 Open API 스냅샷] 읍면동 일별 내국인 유동인구 → 4-11 형식(base_date, emd_nm, age, *_pop_cnt)."""
    raw = _pop583()
    out = to_pop_frame(raw)
    out.attrs.update(raw.attrs)
    out.attrs["daily"] = True
    return out


def to_stops_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """612 응답 → 정류소 정보(3-03) 형식."""
    return pd.DataFrame({
        "bus_station_id": rows["stationId"].astype(str),
        "bus_station_name": rows["stationName"].astype(str),
        "longitude": pd.to_numeric(rows["longitude"], errors="coerce"),
        "latitude": pd.to_numeric(rows["latitude"], errors="coerce"),
        "address": rows.get("stationAddress", pd.Series("", index=rows.index)).astype(str),
    })


@lru_cache(maxsize=1)
def _stops612() -> pd.DataFrame:
    return _read("stops612.parquet", "612")


def stop_key(name) -> str:
    """정류소 이름 정규화 키. 613(카드 단말 ID)과 612(BIS ID)는 정류소 ID 체계가 달라 이름으로 연결한다."""
    import re

    return re.sub(r"[\s·.\-]", "", str(name))


def stops612() -> pd.DataFrame:
    """[612 Open API 스냅샷] 버스 정류소 → 3-03 형식. 613과 연결하려고 '이름 키' 단위(양방향 합)로 묶는다.

    bus_station_id = 이름 키, 좌표 = 같은 이름 정류소들의 평균(양방향 정류소는 수십 m 거리).
    """
    raw = _stops612()
    f = to_stops_frame(raw)
    f["key"] = f["bus_station_name"].map(stop_key)
    out = (f.groupby("key").agg(bus_station_name=("bus_station_name", "first"), longitude=("longitude", "mean"),
                                latitude=("latitude", "mean"), n_dir=("bus_station_id", "nunique"))
           .reset_index().rename(columns={"key": "bus_station_id"}))
    out.attrs.update(raw.attrs)
    return out


def to_usage_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """613 응답 → 정류소별 이용 현황(3-04) 형식(일 단위: base_hour 없음)."""
    return pd.DataFrame({
        "base_date": rows["baseDate"].astype(str),
        "bus_station_id": rows["stationId"].astype(str),
        "bus_station_name": rows["stationName"].astype(str),
        "get_on_off_type": rows["moveType"].astype(str),
        "user_category": rows["priceType"].astype(str),
        "user_count": pd.to_numeric(rows["userCount"], errors="coerce").fillna(0),
    })


@lru_cache(maxsize=1)
def _usage613() -> pd.DataFrame:
    return _read("stop_usage613.parquet", "613")


def usage613() -> pd.DataFrame:
    """[613 Open API 스냅샷] 일일 정류소별 버스 이용자 → 3-04 형식(일 단위, 승차·환승). bus_station_id = 이름 키."""
    raw = _usage613()
    out = to_usage_frame(raw)
    out["bus_station_id"] = out["bus_station_name"].map(stop_key)
    out.attrs.update(raw.attrs)
    return out
