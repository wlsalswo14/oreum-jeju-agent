"""제주빅데이터센터 데이터 명세서(spec.xlsx) → app/catalog/spec_catalog.json 생성.

사용법:
    .venv/Scripts/python.exe tools/build_catalog.py [--spec PATH] [--out PATH]

- 명세서 시트(목록 + 데이터셋 71종 + 참고표 2종)를 읽어 기계가독 카탈로그를 만든다.
- 컬럼 표·참고표는 시트 구조(No | 컬럼명(영문) | 컬럼명(한글) | Data Type | 데이터형식 | | 비고)를 그대로 파싱한다.
- measures/dims/time_col/grain/family/version_tag/supersedes 는 규칙으로 채우고,
  핵심 데이터셋(1-08,1-12,1-14,1-17,1-19,4-10,4-11,3-01~3-04,5-02 등)은 CORE_OVERRIDES 로 수기 검증값을 덮어쓴다.
- 데이터 값(예시값)은 컬럼 정의의 'example' 로만 보관한다(명세서 공개 정보).
- 제주데이터허브 공개 데이터(hub:512/746/748/761/793, API 583/612/613/614)의 헤더 서명도 함께 기록한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = ROOT.parent / "_research" / "spec.xlsx"
DEFAULT_OUT = ROOT / "app" / "catalog" / "spec_catalog.json"

# ----------------------------------------------------------------------------------------------
# 규칙 테이블
# ----------------------------------------------------------------------------------------------
PARTITION_COLS = ["pt_year", "pt_month"]
TIME_COL_PRIORITY = ["base_year_month", "base_date", "base_ymd"]

FAMILY = {
    "1-01": "card_kr_legacy", "1-02": "card_kr_tour_biz", "1-03": "card_kr_theme", "1-04": "card_cn_legacy",
    "1-05": "card_cn_tour_biz", "1-06": "card_cn_theme", "1-07": "card_bjd", "1-08": "card_bjd",
    "1-09": "card_grid1km", "1-10": "card_grid500m", "1-11": "card_week", "1-12": "card_week",
    "1-13": "card_time", "1-14": "card_time", "1-15": "card_golf", "1-16": "card_golf", "1-17": "card_store",
    "1-18": "pos_biz", "1-19": "pos_biz_emd", "1-20": "pos_biz_grid", "1-21": "pos_cust", "1-22": "pos_cust_emd",
    "1-23": "pos_cust_grid", "1-24": "pos_menu_emd", "1-25": "pos_menu_grid", "1-26": "pos_menu_large",
    "1-27": "pos_menu_mid", "1-28": "pos_menu_small", "1-29": "pos_menu_sub", "1-30": "card_online_resident",
    "1-31": "card_online_firm", "1-32": "tamna_currency",
    "2-01": "firm_trade_buy", "2-02": "firm_trade_sell", "2-03": "firm_info", "2-04": "firm_credit",
    "2-05": "firm_fin_annual", "2-06": "firm_fin_quarter", "2-07": "firm_employees", "2-08": "firm_closure",
    "3-01": "bus_route", "3-02": "bus_route_usage", "3-03": "bus_stop", "3-04": "bus_stop_usage",
    "3-05": "bus_passenger", "3-06": "bus_card_tx", "3-07": "road_link", "3-08": "road_node",
    "3-09": "traffic_daily", "3-10": "traffic_hourly",
    "4-01": "wifi_ap_fixed", "4-02": "wifi_ap_fixed_old", "4-03": "wifi_ap_mobile", "4-04": "wifi_usage",
    "4-05": "wifi_ap_usage", "4-06": "wifi_daily_users", "4-07": "pop_kr_grid", "4-08": "pop_long_grid",
    "4-09": "pop_short_grid", "4-10": "pop_kr_emd", "4-11": "pop_kr_emd", "4-12": "pop_long_emd",
    "4-13": "pop_long_emd", "4-14": "pop_short_emd", "4-15": "pop_short_emd",
    "5-01": "weather_station", "5-02": "weather_daily", "5-03": "waste_bag", "5-04": "waste_bulky_jeju",
    "5-05": "food_waste_jeju", "5-06": "food_waste_seogwipo",
}
# 마스터(기준정보) 테이블 → grain=static
STATIC_TABLES = {"2-01", "2-02", "2-03", "2-04", "2-05", "2-06", "2-07", "2-08", "3-01", "3-03", "3-07", "3-08",
                 "4-01", "4-02", "4-03", "5-01"}
# 개인/개체 단위 원장(행 = 개별 거래/개체) — 집계 전 원본
RECORD_TABLES = {"3-05", "3-06", "4-04", "4-05", "5-04", "5-05", "5-06", "1-32"}

ID_COLS = {
    "serial_number", "user_id", "user_card_id", "mac_address", "kedcd", "ked_code", "biz_num", "corporation_num",
    "trans_biz_num", "tag_gno", "device_no", "recode_num", "user_trans_id", "vhc_id", "driver_id", "trans_cmp_id",
    "manage_no", "link_id", "node_id", "start_node_id", "end_node_id", "bus_route_id", "route_id",
    "bus_station_id", "geton_station_id", "getoff_station_id", "user_geton_station_id", "user_getoff_station_id",
    "observatory_code", "goods_code", "zip_code", "card_cd", "grid", "grid_1km", "grid_500m", "grid_500",
}
NON_MEASURE_NUMERIC = {
    "longitude", "latitude", "ap_latitude", "ap_longitude", "geton_station_longitude", "geton_station_latitude",
    "getoff_station_longitude", "getoff_station_latitude", "sea_level_altitude", "lane_count", "quarter",
    "maximum_wind_speed_direction", "road_rating", "connect_code", "maximum_speed_limit", "vehicle_restricted",
    "weight_restricted", "height_restricted", "road_type", "road_in_use", "multi_linked", "user_transfer_count",
    "trans_type_cd", "penalty_cd", "user_distance", "wastebag_liter",
}
MEASURE_NAME_RE = re.compile(
    r"(_cnt$|_amt$|_count$|^amount$|^cnt$|_cost$|_amount$|^upload$|^download$|^session_time$|_pop_cnt$|"
    r"_fare$|^em_g$|^em_amt$|^count$|^fee_amount$|_tot$|^sale_amt$|^net_income$|^operate_profit$|"
    r"^penson_join_cnt$|^monthly_employees$|^monthly_retirement$|^employees_cnt$|_count_\d$|^user_count_\d$|"
    r"^registration_amt$|^overdue_amt$|^equity_caputal$|^trans_supplyprice_tot$|_price$|_rate$|"
    r"^amount_per_|^average_|_temperature$|^daily_rainfall$|^maximum_wind_speed$|^average_wind_speed$)"
)
# 합산 불가(평균·단가·비율) 측정치
NON_ADDITIVE_RE = re.compile(
    r"(_price$|_rate$|^amount_per_|^average_|_temperature$|^maximum_wind_speed$|^store_sale_amt$|"
    r"^store_sale_cnt$|^unit_price$|^credit_grade$)"
)
# 시점 측정(stock) 값: 차원 간 합산은 가능하나 기간(시간) 축은 평균을 써야 함
STOCK_MEASURES = {"resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt", "store_cnt", "penson_join_cnt",
                  "employees_cnt", "bus_station_count", "bus_route_length"}

REGION_COL_RULES = {
    "bjd": "법정동", "bjd_nm": "법정동", "bjd_cd": "법정동코드", "dstrct_se": "권역(7개)", "city": "시군구",
    "city_nm": "시군구", "city_name": "시군구", "city_cd": "시군구코드", "gungu": "시군구", "emd_cd": "행정동코드",
    "emd_nm": "행정동", "emd_name": "행정동", "emd": "읍면동", "province": "시도", "sido": "시도",
    "grid": "격자(50m)", "grid_1km": "격자(1km)", "grid_500m": "격자(500m)", "grid_500": "격자(500m)",
    "address_emd": "법정동", "address_dong": "주소(동·리)", "legal_dongri_code": "법정동리코드",
    "zip_code": "우편번호", "bus_station_id": "정류소", "longitude": "경도", "latitude": "위도",
    "ap_latitude": "위도", "ap_longitude": "경도",
}

# 정체성(필수) 컬럼: 이 컬럼이 없으면 해당 데이터셋일 가능성이 낮다(헤더 인식 감점).
MUST_COLS = {
    "1-09": ["grid_1km"], "1-10": ["grid_500m"], "1-11": ["week"], "1-12": ["week"], "1-13": ["time"],
    "1-14": ["time"], "1-15": ["week", "time"], "1-16": ["week", "time"], "1-17": ["store_cnt"],
    "1-20": ["grid_500"], "1-21": ["week", "time"], "1-22": ["week", "time"], "1-23": ["grid_500", "week", "time"],
    "1-25": ["grid_500"], "1-26": ["menu_large_sale_amt"], "1-27": ["menu_mid_sale_amt"],
    "1-28": ["menu_small_sale_amt"], "1-29": ["menu_sub_sale_amt"], "4-07": ["grid"], "4-08": ["grid", "nationality"],
    "4-09": ["grid", "nationality"], "4-12": ["nationality"], "4-13": ["nationality"], "4-14": ["nationality"],
    "4-15": ["nationality"],
}
KEY_COLS_EXTRA = {
    "4-07": ["grid", "resd_pop_cnt"], "4-08": ["grid", "nationality", "resd_pop_cnt"],
    "4-09": ["grid", "nationality", "visit_pop_cnt"], "4-12": ["nationality", "emd_cd", "resd_pop_cnt"],
    "4-13": ["dstrct_se", "nationality", "emd_cd"], "4-14": ["nationality", "emd_cd", "visit_pop_cnt"],
    "4-15": ["dstrct_se", "nationality", "emd_cd"],
}

# 핵심 데이터셋 수기 검증값 (규칙 결과를 덮어씀)
CARD_DIMS_TAIL = ["biz_cd", "biz_nm", "user_type", "tour_type"]
CORE_OVERRIDES: dict[str, dict] = {
    "1-07": dict(grain="month", time_col="base_year_month",
                 dims=["city", "area_type", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "age", "sex"],
                 measures=["user_cnt", "use_cnt", "use_amt"],
                 region_cols={"bjd": "법정동", "city": "시군구", "area_type": "도심구분"},
                 key_cols=["bjd", "biz_cd", "use_amt"],
                 notes=["v02(1-08)와 지역구분(dstrct_se) 컬럼 유무만 다르며 겹치는 기간 값은 동일(공식 답변). 추이는 v02 권장."]),
    "1-08": dict(grain="month", time_col="base_year_month",
                 dims=["dstrct_se", "city", "area_type", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "age", "sex"],
                 measures=["user_cnt", "use_cnt", "use_amt"],
                 region_cols={"bjd": "법정동", "dstrct_se": "권역(7개)", "city": "시군구", "area_type": "도심구분"},
                 key_cols=["dstrct_se", "bjd", "biz_cd", "use_amt"],
                 notes=["법정동 73개(월평동은 제주시·서귀포시 두 곳 → city/dstrct_se 로 구분).",
                        "tour_type·age 의 'NA'는 해당없음(법인·외국인 등) 범주이며 로더에서 결측으로 변환됨 → groupby 시 dropna=False 권장.",
                        "user_cnt(이용자수)는 차원 간 단순 합산 시 중복 가능(근사치)."]),
    "1-11": dict(grain="month", time_col="base_year_month",
                 dims=["area_type", "biz_cd", "biz_nm", "user_type", "tour_type", "week", "age", "sex"],
                 measures=["user_cnt", "use_cnt", "use_amt"], region_cols={"area_type": "권역(v01 6개)"},
                 key_cols=["week", "area_type", "biz_cd"],
                 notes=["week='공휴일' 행은 해당 날짜의 원래 요일에도 함께 집계됨(공식 답변) → 합계 시 공휴일 행 제외."]),
    "1-12": dict(grain="month", time_col="base_year_month",
                 dims=["dstrct_se", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "week", "age", "sex"],
                 measures=["user_cnt", "use_cnt", "use_amt"],
                 region_cols={"bjd": "법정동", "dstrct_se": "권역(7개)"}, key_cols=["week", "bjd", "biz_cd"],
                 notes=["week='공휴일' 행은 해당 날짜의 원래 요일에도 함께 집계됨(공식 답변) → 월 합계 시 공휴일 행 제외.",
                        "bjd 는 1-08 과 동일한 법정동(공식 답변).", "시군구·도심구분 컬럼 없음(dstrct_se 로 시 구분)."]),
    "1-13": dict(grain="month", time_col="base_year_month",
                 dims=["area_type", "biz_cd", "biz_nm", "user_type", "tour_type", "time", "age", "sex"],
                 measures=["user_cnt", "use_cnt", "use_amt"], region_cols={"area_type": "권역(v01 6개)"},
                 key_cols=["time", "area_type", "biz_cd"],
                 notes=["time 은 시간대 구간(오전 07~11, 점심 11~14, 오후 14~18, 저녁 18~22, 밤 22~07)."]),
    "1-14": dict(grain="month", time_col="base_year_month",
                 dims=["dstrct_se", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "time", "age", "sex"],
                 measures=["user_cnt", "use_cnt", "use_amt"],
                 region_cols={"bjd": "법정동", "dstrct_se": "권역(7개)"}, key_cols=["time", "bjd", "biz_cd"],
                 notes=["time 은 시간대 구간(오전 07~11, 점심 11~14, 오후 14~18, 저녁 18~22, 밤 22~07). 구간 길이가 다르므로 시간당 환산 권장."]),
    "1-15": dict(grain="month", key_cols=["week", "time", "area_type"]),
    "1-16": dict(grain="month", key_cols=["week", "time", "dstrct_se"]),
    "1-17": dict(grain="month", time_col="base_year_month", dims=["city", "owner_gb", "biz_cd", "biz_nm"],
                 measures=["store_cnt"], region_cols={"city": "시군구"}, key_cols=["owner_gb", "store_cnt"],
                 measure_agg={"store_cnt": "sum_dims_mean_time"},
                 notes=["시군구 단위 매장 수(법정동 없음) → 법정동별 경쟁 점포는 허브 상가정보(793) 활용.",
                        "매장 수는 시점(stock) 값: 월 간 합산 금지(평균/말월 사용)."]),
    "1-18": dict(grain="month", key_cols=["biz_small_cd", "sale_amt"]),
    "1-19": dict(grain="month", time_col="base_year_month",
                 dims=["city_cd", "city_nm", "bjd_cd", "bjd_nm", "biz_mid_cd", "biz_mid_nm", "biz_small_cd", "biz_small_nm"],
                 measures=["sale_amt", "store_cnt", "use_cnt"],
                 region_cols={"bjd_nm": "법정동", "bjd_cd": "법정동코드", "city_nm": "시군구", "city_cd": "시군구코드"},
                 key_cols=["bjd_cd", "biz_small_cd", "sale_amt"], measure_agg={"store_cnt": "sum_dims_mean_time"},
                 notes=["POS 업종코드(Q01~Q15)는 참고표1 업종코드표 참조 — BC 42업종과 체계가 다름.",
                        "법정동별 합산값은 k<3 값이 제외되어 전체 합계(1-18)와 다를 수 있음(명세서 주석).",
                        "store_cnt 는 POS 표본 점포 수."]),
    "3-01": dict(grain="static", time_col="base_date", dims=["bus_route_id", "bus_route_number", "bus_route_name",
                 "start_date", "bus_transferability"], measures=[], key_cols=["bus_route_number", "bus_route_length"],
                 measure_agg={}, notes=["일 단위 스냅샷 누적(변경 이력). 최신 base_date 1일치만 사용 권장."]),
    "3-02": dict(grain="hour", time_col="base_date",
                 dims=["day_of_week", "base_hour", "bus_route_id", "bus_route_name", "get_on_off_type", "user_category", "pay_type"],
                 measures=["user_count"], key_cols=["bus_route_id", "base_hour", "user_count"],
                 notes=["get_on_off_type: 승차·환승. base_hour 00~23."]),
    "3-03": dict(grain="static", time_col="base_date",
                 dims=["bus_station_id", "bus_station_name", "bus_city_type"], measures=[],
                 region_cols={"bus_station_id": "정류소", "longitude": "경도", "latitude": "위도"},
                 key_cols=["bus_station_id", "bus_city_type", "longitude"], measure_agg={},
                 notes=["일 단위 스냅샷 누적(변경 이력). 같은 정류소가 날짜별로 반복되므로 bus_station_id 기준 중복 제거."]),
    "3-04": dict(grain="hour", time_col="base_date",
                 dims=["day_of_week", "base_hour", "bus_station_id", "bus_station_name", "get_on_off_type", "user_category", "pay_type"],
                 measures=["user_count"], region_cols={"bus_station_id": "정류소"},
                 key_cols=["bus_station_id", "base_hour", "get_on_off_type"],
                 notes=["get_on_off_type: 승차·환승·하차. 이용(탑승) = 승차+환승.", "base_hour 00~23(정수 변환 권장).",
                        "좌표는 3-03(정류소 정보)와 bus_station_id 로 결합."]),
    "4-10": dict(grain="hour", time_col="base_date",
                 dims=["time", "emd_cd", "emd_nm", "sex", "age", "resd"],
                 measures=["resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"],
                 region_cols={"emd_cd": "행정동코드", "emd_nm": "행정동"}, key_cols=["resd_pop_cnt", "emd_cd", "time"],
                 measure_agg={c: "sum_dims_mean_time" for c in ["resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"]},
                 notes=["값은 해당 시각 정각에 측정한 인구(명, 소수). 하루 값을 24시간 합산하면 '연인원(인·시간)' → 일평균 체류인구는 시간 평균 사용.",
                        "emd_nm 은 '제주시 한림읍' 형식(행정동 43개). 카드 데이터의 법정동과 매핑 필요(regions.py).",
                        "2022.07 이후 age 는 SKT 5세 구간 코드(0009,1014,…,7000).",
                        "resd(거주지): 제주/그외 — '그외'는 도외 거주자(관광객 등) 근사."]),
    "4-11": dict(grain="hour", time_col="base_date",
                 dims=["time", "dstrct_se", "emd_cd", "emd_nm", "sex", "age", "resd"],
                 measures=["resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"],
                 region_cols={"emd_cd": "행정동코드", "emd_nm": "행정동", "dstrct_se": "권역(7개)"},
                 key_cols=["resd_pop_cnt", "dstrct_se", "emd_cd"],
                 measure_agg={c: "sum_dims_mean_time" for c in ["resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"]},
                 notes=["v01(4-10)에 지역구분(dstrct_se) 컬럼이 추가된 버전(2025.05~).",
                        "값은 해당 시각 정각에 측정한 인구(명, 소수). 일평균은 시간 평균 사용."]),
    "5-01": dict(grain="static", time_col=None, dims=["observatory_code", "observatory_name", "management_office",
                 "legal_dongri_code"], measures=[], measure_agg={}, key_cols=["observatory_name", "sea_level_altitude"],
                 notes=["명세서상 longitude/latitude 예시값이 뒤바뀌어 있음(경도 33.x, 위도 126.x) — 값 범위로 판별 권장."]),
    "5-02": dict(grain="day", time_col="base_date", dims=["observatory_code"],
                 measures=["daily_rainfall"],
                 rate_measures=["average_temperature", "lowest_temperature", "highest_temperature",
                                "maximum_wind_speed", "average_wind_speed", "maximum_wind_speed_direction"],
                 measure_agg={"average_temperature": "mean", "lowest_temperature": "min", "highest_temperature": "max",
                              "daily_rainfall": "sum", "maximum_wind_speed": "max", "average_wind_speed": "mean",
                              "maximum_wind_speed_direction": "mean"},
                 key_cols=["observatory_code", "average_temperature", "daily_rainfall"],
                 notes=["관측소 코드는 5-01(날씨 관측소 정보)과 결합. 지역 평균은 관측소 평균 사용.",
                        "*_time 컬럼은 hhmi 문자열."]),
}

# 허브 공개 데이터(파일/API) 헤더 서명 — 로더가 허브 파일도 인식하도록 카탈로그에 함께 기록
HUB_DATASETS = {
    "hub:512": dict(name="일별 시간대별 버스 승차 인원 수 현황", provider="제주데이터허브(제주빅데이터센터)",
                    columns=[("base_date", "기준일"), ("day_of_week", "요일"), ("base_hour", "시간대"),
                             ("user_type", "이용자 유형"), ("user_count", "이용자 수")],
                    grain="day", time_col="base_date", measures=["user_count"],
                    dims=["day_of_week", "base_hour", "user_type"], period="2018-06-03 ~ 2025-10-19",
                    notes=["base_hour 는 7개 시간대 구간(01시-06시, 07시-09시, …). 제주 전체 합계(정류소 구분 없음)."]),
    "hub:746": dict(name="제주 예비창업자를 위한 업종별 카드 매출과 매장수 데이터 활용_매쉬업결과",
                    provider="제주특별자치도(제주데이터허브)",
                    columns=[("년월", "년월"), ("시도명", "시군구"), ("지역구분", "도심구분"), ("읍면동명", "법정동"),
                             ("업종코드", "업종코드"), ("업종명", "업종명"), ("이용자 구분", "이용자구분"),
                             ("관광구분", "관광구분"), ("연령대", "연령대"), ("성별", "성별"), ("이용자수", "이용자수"),
                             ("이용건수", "이용건수"), ("이용금액", "이용금액"), ("매장수", "매장수"),
                             ("업종명 대분류", "업종 대분류"), ("데이터기준일자", "데이터기준일자")],
                    optional=["이용자수", "이용건수"],
                    grain="month", time_col="년월", measures=["이용금액", "이용건수", "이용자수"],
                    dims=["시도명", "지역구분", "읍면동명", "업종코드", "업종명", "이용자 구분", "관광구분", "연령대", "성별"],
                    period="2017-01 ~ 2021-05",
                    notes=["카드 이용 현황(읍면동)_v01(1-07)과 같은 체계의 공개 매쉬업(2017년 파일만 이용자수·이용건수 포함).",
                           "매장수는 업종×시군구 단위 값이 행마다 반복됨(합산 금지)."]),
    "hub:748": dict(name="상권분석을 위한 유동인구에 따른 카드 매출액 변화 데이터 활용_매쉬업결과",
                    provider="제주특별자치도(제주데이터허브)",
                    columns=[("년월", "년월"), ("시도명", "시군구"), ("읍면동명", "행정동"), ("업종명", "업종명"),
                             ("성별", "성별"), ("이용자수", "이용자수"), ("이용금액", "이용금액"),
                             ("거주인구", "거주인구"), ("근무인구", "근무인구"), ("방문인구", "방문인구"),
                             ("데이터기준일자", "데이터기준일자")],
                    grain="month", time_col="년월", measures=["이용자수", "이용금액", "거주인구", "근무인구", "방문인구"],
                    dims=["시도명", "읍면동명", "업종명", "성별"], period="2017-01 ~ 2021-05",
                    notes=["인구 값은 월간 시간대별 합(연인원)으로 업종 행마다 반복됨(합산 금지)."]),
    "hub:761": dict(name="제주관광공사_제주관광정보시스템(VISIT JEJU)_축제행사콘텐츠", provider="제주관광공사(제주데이터허브)",
                    columns=[("콘텐츠명", "콘텐츠명"), ("시작일", "시작일"), ("종료일", "종료일"), ("시작시간", "시작시간"),
                             ("종료시간", "종료시간"), ("요금", "요금"), ("주관", "주관"), ("주최", "주최"), ("후원", "후원")],
                    grain="static", time_col="시작일", measures=[], dims=["콘텐츠명"], period="2017 ~ 2025"),
    "hub:793": dict(name="제주특별자치도 상가(상권) 정보", provider="소상공인시장진흥공단(제주데이터허브)",
                    columns=[("상호명", "상호명"), ("상권업종대분류명", "대분류"), ("상권업종중분류명", "중분류"),
                             ("상권업종소분류명", "소분류"), ("표준산업분류명", "표준산업분류명"), ("시도명", "시도"),
                             ("시군구명", "시군구"), ("행정동명", "행정동"), ("법정동명", "법정동"), ("행정동코드", "행정동코드"),
                             ("법정동코드", "법정동코드"), ("지번주소", "지번주소"), ("도로명주소", "도로명주소"),
                             ("신우편번호", "우편번호"), ("경도", "경도"), ("위도", "위도")],
                    grain="static", time_col=None, measures=[], dims=["상권업종소분류명", "법정동명", "행정동명"],
                    period="2025-09 기준"),
    "hub:583": dict(name="읍면동 단위 내국인 유동인구(API)", provider="제주데이터허브 Open API",
                    columns=[("baseDate", "날짜"), ("city", "시군구"), ("emd", "읍면동"), ("gender", "성별"),
                             ("ageGroup", "연령대"), ("resdPop", "거주인구"), ("workPop", "근무인구"), ("visitPop", "방문자수")],
                    grain="day", time_col="baseDate", measures=["resdPop", "workPop", "visitPop"],
                    dims=["city", "emd", "gender", "ageGroup"], period="API"),
    "hub:612": dict(name="버스 정류소 기본 정보(API)", provider="제주데이터허브 Open API",
                    columns=[("stationId", "정류소ID"), ("stationType", "정류소타입"), ("stationName", "정류소명"),
                             ("stationAddress", "정류소주소"), ("direction", "방향"), ("longitude", "경도"),
                             ("latitude", "위도"), ("localInfo", "장소정보")],
                    grain="static", time_col=None, measures=[], dims=["stationId", "stationName"], period="API"),
    "hub:613": dict(name="일일 정류소별 버스 이용자 정보(API)", provider="제주데이터허브 Open API",
                    columns=[("baseDate", "날짜"), ("stationId", "정류소ID"), ("stationName", "정류소명"),
                             ("moveType", "탑승구분"), ("priceType", "요금제"), ("payType", "지불방법"), ("userCount", "이용자수")],
                    grain="day", time_col="baseDate", measures=["userCount"],
                    dims=["stationId", "stationName", "moveType", "priceType", "payType"], period="API"),
    "hub:614": dict(name="일일 노선별 버스 이용자 정보(API)", provider="제주데이터허브 Open API",
                    columns=[("baseDate", "날짜"), ("routeName", "노선이름"), ("routeSubName", "노선하위명칭"),
                             ("moveType", "탑승구분"), ("priceType", "요금제"), ("payType", "지불방법"), ("userCount", "이용자수")],
                    grain="day", time_col="baseDate", measures=["userCount"],
                    dims=["routeName", "routeSubName", "moveType", "priceType", "payType"], period="API"),
}

# 서귀포시 법정동(동 지역 22개 + 읍면 5개). 그 외는 제주시. (허브 793 상가정보로 검증)
SEOGWIPO_BJD = {"강정동", "대포동", "도순동", "동홍동", "법환동", "보목동", "상예동", "상효동", "색달동", "서귀동", "서호동",
                "서홍동", "신효동", "영남동", "월평동", "중문동", "토평동", "하예동", "하원동", "하효동", "호근동", "회수동",
                "대정읍", "남원읍", "성산읍", "안덕면", "표선면"}


# ----------------------------------------------------------------------------------------------
# 파서
# ----------------------------------------------------------------------------------------------
def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return str(v).replace("\r", "").strip()


def read_rows(ws) -> list[list[str]]:
    rows = []
    for row in ws.iter_rows(values_only=True):
        r = [_s(c) for c in row]
        while r and r[-1] == "":
            r.pop()
        rows.append(r)
    return rows


def cell(rows, r, c) -> str:
    if r < 0 or r >= len(rows) or c >= len(rows[r]):
        return ""
    return rows[r][c]


def is_int(s: str) -> bool:
    return bool(re.fullmatch(r"\d+", s or ""))


def parse_reference_tables(rows) -> list[dict]:
    """'[참고..] 제목' 앵커 아래의 표들을 (나란히/위아래 배치 모두) 파싱."""
    tables = []
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if not v.startswith("[참고"):
                continue
            title = v
            hr = r + 1
            if not cell(rows, hr, c) and cell(rows, hr, c + 1):  # 2-xx 시트처럼 한 칸 들여쓴 경우
                continue
            header = []
            cc = c
            while cell(rows, hr, cc):
                header.append(cell(rows, hr, cc))
                cc += 1
            if not header:
                continue
            width = len(header)
            data = []
            rr = hr + 1
            while rr < len(rows) and is_int(cell(rows, rr, c)):
                vals = [cell(rows, rr, c + k) for k in range(width)]
                data.append(vals)
                rr += 1
            tables.append({"title": title, "header": header, "rows": data})
    return tables


def parse_dataset_sheet(rows) -> dict:
    meta: dict = {"columns": [], "references": []}
    col_hdr = None
    for r, row in enumerate(rows):
        first = row[0].strip() if row else ""
        if first == "카테고리":
            meta["category"] = cell(rows, r, 2).strip()
        elif first.startswith("데이터명(한글)"):
            meta["name_sheet"] = cell(rows, r, 2).strip()
        elif first.startswith("데이터명(영문)"):
            meta["name_en"] = cell(rows, r, 2).strip()
        elif first == "설명":
            meta["description_raw"] = cell(rows, r, 2)
        elif first == "등록일자":
            meta["registered"] = cell(rows, r, 2)[:10]
            meta["export_text"] = cell(rows, r, 4)
            meta["provider_sheet"] = cell(rows, r, 7)
        elif first == "No" and len(row) > 1 and ("컬럼명" in row[1] or "Column" in row[1]):
            col_hdr = r
            break
    if col_hdr is not None:
        hdr = rows[col_hdr]
        note_idx = 6 if len(hdr) > 6 else len(hdr) - 1
        r = col_hdr + 1
        while r < len(rows) and is_int(cell(rows, r, 0)) and cell(rows, r, 1):
            name = cell(rows, r, 1).strip()
            meta["columns"].append({
                "no": int(cell(rows, r, 0)),
                "name": name,
                "kor": cell(rows, r, 2).strip(),
                "type": cell(rows, r, 3).strip().upper(),
                "example": cell(rows, r, 4).strip(),
                "note": " ".join(cell(rows, r, k) for k in range(note_idx, len(rows[r]))).strip(),
            })
            r += 1
        # 명세서 하단 주석(* ...)
        while r < len(rows):
            if rows[r] and rows[r][0].startswith("*"):
                meta.setdefault("footnotes", []).append(rows[r][0].lstrip("* ").strip())
            r += 1
    meta["references"] = parse_reference_tables(rows)
    return meta


def clean_description(raw: str) -> tuple[str, str, str]:
    lines = [ln.strip().lstrip("-").strip() for ln in re.split(r"\n|/", raw or "") if ln.strip()]
    lines = [ln for ln in lines if ln]
    desc = " ".join(lines)
    period = ""
    for ln in lines:
        m = re.search(r"기간\s*[:：]\s*(.+)", ln)
        if m:
            period = (period + " / " if period else "") + m.group(1).strip()
    if not period:
        m = re.search(r"(\d{4}[.년]\s*\d{1,2}[.월]?\s*\d{0,2}일?\s*(?:부터|~|기준)[^.]*)", desc)
        if m:
            period = m.group(1).strip()
    update = ""
    if re.search(r"월\s*기준|월별 기준", desc):
        update = "월"
    elif re.search(r"일일\s*기준|일 단위|일일기준", desc):
        update = "일"
    elif "비정기" in desc:
        update = "비정기"
    return desc, period, update


# ----------------------------------------------------------------------------------------------
# 규칙 기반 분류
# ----------------------------------------------------------------------------------------------
def classify(ds_id: str, cols: list[dict]) -> dict:
    names = [c["name"] for c in cols]
    by = {c["name"]: c for c in cols}
    time_col = next((t for t in TIME_COL_PRIORITY if t in names), None)
    hour_col = None
    for c in cols:
        if c["name"] in ("time", "base_hour"):
            note = c["note"] + " " + c["example"]
            if re.search(r"\d{2}\s*~\s*\d{2}\s*시|00\s*~\s*23|01\s*~\s*24", note) and not re.search(r"오전|00~06,", note):
                hour_col = c["name"]
    if ds_id in STATIC_TABLES:
        grain = "static"
    elif time_col == "base_year_month":
        grain = "month"
    elif time_col in ("base_date", "base_ymd"):
        grain = "hour" if hour_col else "day"
    else:
        grain = "static"
    measures, measure_agg, dims, ids = [], {}, [], []
    for c in cols:
        n = c["name"]
        if n in PARTITION_COLS or n == time_col:
            continue
        numeric_type = c["type"].startswith(("INT", "BIGINT", "DOUBLE", "DECIMAL"))
        if n in NON_MEASURE_NUMERIC or n in ID_COLS:
            is_measure = False
        elif NON_ADDITIVE_RE.search(n) and (numeric_type or MEASURE_NAME_RE.search(n)):
            is_measure = True
        else:
            is_measure = (MEASURE_NAME_RE.search(n) is not None or numeric_type) \
                and not n.endswith(("_date", "_dt", "_cd", "_code", "_id")) \
                and not (n.endswith("_time") and not numeric_type) \
                and n not in ("age", "report_year", "account_year", "finance_year", "time", "base_hour")
        if is_measure:
            measures.append(n)
            if NON_ADDITIVE_RE.search(n):
                measure_agg[n] = "mean"
            elif n in STOCK_MEASURES:
                measure_agg[n] = "sum_dims_mean_time"
            else:
                measure_agg[n] = "sum"
        else:
            if n in ID_COLS:
                ids.append(n)
            if n not in NON_MEASURE_NUMERIC or n in ("longitude", "latitude"):
                if n not in ("longitude", "latitude", "ap_latitude", "ap_longitude"):
                    dims.append(n)
    additive = [m for m in measures if measure_agg.get(m) != "mean"]
    region_cols = {}
    for n in names:
        if n in REGION_COL_RULES:
            region_cols[n] = REGION_COL_RULES[n]
        if n == "area_type":
            note = by[n]["note"]
            region_cols[n] = "권역(v01 6개)" if "동부" in note else "도심구분"
    return dict(time_col=time_col, grain=grain, measures=additive, rate_measures=[m for m in measures if m not in additive],
                measure_agg=measure_agg, dims=dims, id_cols=ids, region_cols=region_cols,
                partition_cols=[p for p in PARTITION_COLS if p in names])


def version_info(ds_id: str, name: str, all_names: dict[str, str]) -> tuple[str | None, str | None, str | None]:
    m = re.search(r"_v(\d+)\s*$", name)
    if not m:
        return None, None, None
    tag = f"v{int(m.group(1)):02d}"
    fam = FAMILY.get(ds_id)
    sibs = sorted([k for k, v in FAMILY.items() if v == fam and k != ds_id])
    sup = sup_by = None
    for s in sibs:
        sm = re.search(r"_v(\d+)\s*$", all_names.get(s, ""))
        if not sm:
            continue
        if int(sm.group(1)) < int(m.group(1)):
            sup = s
        elif int(sm.group(1)) > int(m.group(1)):
            sup_by = s
    return tag, sup, sup_by


# ----------------------------------------------------------------------------------------------
# 참고표(reference) 수집
# ----------------------------------------------------------------------------------------------
def find_ref(sheets_meta: dict, ds_id: str, keyword: str) -> dict | None:
    for t in sheets_meta.get(ds_id, {}).get("references", []):
        if keyword in t["title"]:
            return t
    return None


def build_reference(sheets_meta: dict, pos_rows: list[list[str]], menu_rows: list[list[str]]) -> dict:
    ref: dict = {}
    t = find_ref(sheets_meta, "1-08", "지역구분")
    ref["districts7"] = [r[1] for r in t["rows"]] if t else []
    t = find_ref(sheets_meta, "1-11", "지역구분")
    ref["districts6_v01"] = [r[1] for r in t["rows"]] if t else []
    t = find_ref(sheets_meta, "1-08", "법정동")
    bjd = []
    for r in (t["rows"] if t else []):
        name, note = r[1], (r[2] if len(r) > 2 else "")
        city = "서귀포시" if name in SEOGWIPO_BJD else "제주시"
        if name == "월평동":
            city = "서귀포시" if "서귀포" in note else "제주시"
        bjd.append({"name": name, "city": city, "key": f"{name}@{city}",
                    "area_type": "읍면" if name.endswith(("읍", "면")) else "도심", "note": note})
    ref["bjd73"] = bjd
    ref["bjd73_note"] = ("법정동 이름은 73개(고유)이며 월평동이 제주시(제주시 중부(동))·서귀포시(서귀포시 중부) 두 곳에 있어 "
                         "목록은 74행(key=이름@시군구). 시군구 구분은 1-08/1-12/1-14의 city 또는 dstrct_se 로 한다.")
    t = find_ref(sheets_meta, "1-08", "업종코드")
    ref["biz42"] = [{"code": r[1], "name": r[2]} for r in (t["rows"] if t else [])]
    t = find_ref(sheets_meta, "1-08", "연령대")
    ref["age_bands"] = {r[1]: r[2] for r in (t["rows"] if t else [])}
    ref["age_bands"]["NA"] = "해당없음/알수없음(법인·외국인 등)"
    t = find_ref(sheets_meta, "1-14", "시간대")
    ref["card_time_bands"] = {r[1]: r[2] for r in (t["rows"] if t else [])}
    t = find_ref(sheets_meta, "1-12", "이용자구분")
    ref["card_user_types"] = [r[1] for r in (t["rows"] if t else [])]
    ref["card_tour_types"] = ["도민", "개별", "단체", "NA"]
    ref["card_week"] = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"]
    ref["card_sex"] = ["남성", "여성", "알수없음"]
    t = find_ref(sheets_meta, "4-11", "행정동")
    ref["emd43"] = [{"code": r[1], "name": r[2], "city": r[2].split()[0], "short": r[2].split()[-1]}
                    for r in (t["rows"] if t else [])]
    t = find_ref(sheets_meta, "4-10", "연령대")
    ref["pop_age_bands"] = [{"age": r[1], "label": r[2], "telco": r[3] if len(r) > 3 else ""} for r in (t["rows"] if t else [])]
    t = find_ref(sheets_meta, "4-08", "국적")
    ref["foreign_nationality31"] = {r[1]: r[2] for r in (t["rows"] if t else [])}
    t = find_ref(sheets_meta, "3-04", "이용자구분")
    ref["bus_user_categories"] = [r[1] for r in (t["rows"] if t else [])]
    ref["bus_get_on_off_types"] = ["승차", "환승", "하차"]
    t = find_ref(sheets_meta, "3-06", "교통수단코드")
    ref["bus_trans_type_codes"] = {r[1]: r[2] for r in (t["rows"] if t else [])}
    # 참고표1: POS 외식업 업종코드표
    pos = []
    for r in pos_rows:
        if len(r) >= 4 and re.fullmatch(r"Q\d{2}", r[0]) and re.fullmatch(r"Q\d{5}", r[2]):
            pos.append({"mid_cd": r[0], "mid_nm": r[1], "small_cd": r[2], "small_nm": r[3]})
    ref["pos_biz_codes"] = pos
    menu = []
    for r in menu_rows:
        if len(r) >= 8 and re.fullmatch(r"[A-Z]", r[0]):
            menu.append(dict(zip(["large_cd", "large_nm", "mid_cd", "mid_nm", "small_cd", "small_nm", "sub_cd", "sub_nm"], r[:8])))
    ref["pos_menu_codes"] = menu
    return ref


# ----------------------------------------------------------------------------------------------
def build(spec_path: Path) -> dict:
    import openpyxl  # tools 전용 의존성

    wb = openpyxl.load_workbook(spec_path, read_only=True, data_only=True)
    sheets = {name: read_rows(wb[name]) for name in wb.sheetnames}
    list_rows = sheets["목록"]
    version = ""
    for r in list_rows[:6]:
        for v in r:
            m = re.search(r"\((v\d+),\s*([\d.]+)\)", v)
            if m:
                version = f"{m.group(1)} ({m.group(2)})"
    entries = []
    cat = ""
    for r in list_rows:
        if len(r) >= 5 and re.fullmatch(r"\d-\d{2}", r[0].strip()):
            if r[1]:
                cat = r[1].split("\n")[0].split("/")[0].strip()
            entries.append({"id": r[0].strip(), "category": cat, "name": r[2].strip(),
                            "exportable": r[3].strip() == "가능", "provider": r[4].strip()})
    sheet_by_id = {}
    for name in wb.sheetnames:
        m = re.match(r"(\d-\d{2})\s", name)
        if m:
            sheet_by_id[m.group(1)] = name
    sheets_meta = {i: parse_dataset_sheet(sheets[s]) for i, s in sheet_by_id.items()}
    all_names = {e["id"]: e["name"] for e in entries}

    datasets = {}
    for e in entries:
        meta = sheets_meta.get(e["id"], {"columns": []})
        desc, period, update = clean_description(meta.get("description_raw", ""))
        info = classify(e["id"], meta["columns"])
        tag, sup, sup_by = version_info(e["id"], e["name"], all_names)
        export_sheet = meta.get("export_text", "")
        ds = {
            "id": e["id"], "name": e["name"], "category": e["category"],
            "exportable": e["exportable"],
            "exportable_sheet_text": export_sheet,
            "provider": e["provider"], "description": desc, "period": period, "update": update,
            "registered": meta.get("registered", ""), "name_en": meta.get("name_en", ""),
            "table_kind": "master" if e["id"] in STATIC_TABLES else ("record" if e["id"] in RECORD_TABLES else "aggregate"),
            "columns": [{k: c[k] for k in ("name", "kor", "type", "example", "note")} for c in meta["columns"]],
            **info,
            "optional_cols": [p for p in PARTITION_COLS if p in [c["name"] for c in meta["columns"]]],
            "family": FAMILY.get(e["id"]), "version_tag": tag, "supersedes": sup, "superseded_by": sup_by,
            "key_cols": list(KEY_COLS_EXTRA.get(e["id"], [])), "must_cols": list(MUST_COLS.get(e["id"], [])),
            "notes": list(meta.get("footnotes", [])), "source": "center",
            "verified": False,
        }
        ov = CORE_OVERRIDES.get(e["id"])
        if ov:
            notes = ov.get("notes", [])
            for k, v in ov.items():
                if k != "notes":
                    ds[k] = v
            ds["notes"] = ds["notes"] + notes
            ds["verified"] = True
            if "measure_agg" not in ov:
                ds["measure_agg"] = {m: ds["measure_agg"].get(m, "sum") for m in ds["measures"]}
            ds["rate_measures"] = [m for m in ds.get("rate_measures", []) if m not in ds["measures"]]
        # 정합성 검사: 지정한 컬럼은 반드시 명세서 컬럼이어야 함
        cn = {c["name"] for c in ds["columns"]}
        for k in ("dims", "measures", "key_cols", "must_cols"):
            bad = [x for x in ds[k] if x not in cn]
            if bad:
                raise SystemExit(f"[{e['id']}] {k} 에 명세서에 없는 컬럼: {bad}")
        datasets[e["id"]] = ds

    pos_sheet = next((s for s in wb.sheetnames if "참고표1" in s), None)
    menu_sheet = next((s for s in wb.sheetnames if "참고표2" in s), None)
    reference = build_reference(sheets_meta, sheets.get(pos_sheet, []), sheets.get(menu_sheet, []))

    hub = {}
    for hid, h in HUB_DATASETS.items():
        did = hid.split(":")[1]
        hub[hid] = {
            "id": hid, "name": h["name"], "category": "제주데이터허브", "exportable": True, "provider": h["provider"],
            "description": h["name"], "period": h.get("period", ""), "update": "",
            "table_kind": "master" if h["grain"] == "static" else "aggregate",
            "columns": [{"name": n, "kor": k, "type": "", "example": "", "note": ""} for n, k in h["columns"]],
            "time_col": h["time_col"], "grain": h["grain"], "measures": h["measures"],
            "measure_agg": {m: "sum" for m in h["measures"]}, "rate_measures": [], "dims": h["dims"], "id_cols": [],
            "region_cols": {}, "partition_cols": [], "optional_cols": h.get("optional", []),
            "family": f"hub_{did}", "version_tag": None, "supersedes": None, "superseded_by": None,
            "key_cols": [], "must_cols": [], "notes": h.get("notes", []), "source": "hub",
            "source_url": f"https://jejudatahub.net/data/view/data/{did}", "verified": True,
        }
    out = {
        "version": version,
        "source_file": spec_path.name,
        "generated_by": "tools/build_catalog.py",
        "counts": {"datasets": len(datasets), "hub_datasets": len(hub), "sheets": len(wb.sheetnames)},
        "datasets": datasets,
        "hub_datasets": hub,
        "reference": reference,
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if not a.spec.exists():
        print(f"명세서 파일을 찾을 수 없습니다: {a.spec}", file=sys.stderr)
        return 1
    cat = build(a.spec)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    menu = cat["reference"].pop("pos_menu_codes")
    cat["reference"]["pos_menu_codes_file"] = "pos_menu_codes.json"
    cat["reference"]["pos_menu_codes_count"] = len(menu)
    a.out.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.out.parent / "pos_menu_codes.json").write_text(
        json.dumps(menu, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    r = cat["reference"]
    print(f"OK {a.out} datasets={len(cat['datasets'])} hub={len(cat['hub_datasets'])} "
          f"bjd={len(r['bjd73'])} emd={len(r['emd43'])} biz={len(r['biz42'])} pos={len(r['pos_biz_codes'])} "
          f"menu={r['pos_menu_codes_count']} districts={len(r['districts7'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
