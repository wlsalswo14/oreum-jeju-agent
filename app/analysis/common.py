"""분석 공통 유틸리티와 결과 구조."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..guard.export_guard import AggTable, Rollup

WEEK_ORDER = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일", "공휴일"]
WEEK_SHORT = {"월": "월요일", "화": "화요일", "수": "수요일", "목": "목요일", "금": "금요일", "토": "토요일", "일": "일요일"}
TIME_ORDER = ["오전", "점심", "오후", "저녁", "밤"]
TIME_DESC = {"오전": "07~11시", "점심": "11~14시", "오후": "14~18시", "저녁": "18~22시", "밤": "22~07시"}
AGE_LABEL = {"10": "10대 이하", "20": "20대", "30": "30대", "40": "40대", "50": "50대", "60": "60대", "70": "70대 이상"}
AGE_ROLLUP = {"10": "10~20대", "20": "10~20대", "30": "30~40대", "40": "30~40대", "50": "50대 이상",
              "60": "50대 이상", "70": "50대 이상"}
USER_GROUP = {"내국인": "내국인 관광객", "제주도민": "제주도민", "법인": "법인",
              "중국": "외국인", "일본": "외국인", "동남아": "외국인", "기타외국": "외국인"}


@dataclass
class RegionSel:
    label: str                                   # 화면 표시명 (예: 애월읍)
    bjd: list[str]                               # 카드 데이터의 법정동 이름들
    emd: list[str] = field(default_factory=list)  # 유동인구 데이터의 행정동 이름들(예: '제주시 애월읍')
    center: tuple[float, float] | None = None    # (경도, 위도)
    radius_km: float = 2.5
    city: str | None = None
    peer_bjd: list[str] = field(default_factory=list)   # 비교 지역(비어 있으면 전체)
    district: str | None = None                  # 7개 권역


@dataclass
class IndustrySel:
    code: str                                    # BC 42업종 코드 (예: I56220)
    name: str                                    # 업종명
    label: str = ""                              # 이용자 표현(예: 카페)
    store_keywords: list[str] = field(default_factory=list)  # 허브 상가정보 매칭 키워드


@dataclass
class ChartSpec:
    key: str
    table_key: str
    kind: str                     # line | bar | barh | stacked | heat | map | donut
    title: str
    x: str | None = None
    y: list[str] = field(default_factory=list)
    series: str | None = None     # long-format 시리즈 컬럼
    highlight: tuple[Any, Any] | None = None   # 강조 구간(x 값 범위) 예: 행사 기간
    highlight_label: str = ""
    note: str = ""
    y_label: str = ""
    sort: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class KPI:
    label: str
    table_key: str
    row: dict                     # 행 선택자 {dim: value}
    col: str
    hint: str = ""


@dataclass
class AnalysisResult:
    mode: str
    title: str
    params: dict
    tables: list[AggTable] = field(default_factory=list)
    charts: list[ChartSpec] = field(default_factory=list)
    kpis: list[KPI] = field(default_factory=list)
    map_points: list[dict] = field(default_factory=list)      # 반출 가능/공개 데이터만(정류소·상가 좌표)
    notes: list[str] = field(default_factory=list)            # 한계·누락 데이터 안내
    facts: dict = field(default_factory=dict)                 # 서술용 범주형 사실(피크 요일 등) — 수치 아님
    used_sources: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
def num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def ym_int(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace("-", "").str.slice(0, 6), errors="coerce").astype("Int64")


def ym_label(v) -> str:
    v = int(v)
    return f"{v // 100}.{v % 100:02d}"


def ym_add(v: int, months: int) -> int:
    y, m = divmod(int(v), 100)
    idx = y * 12 + (m - 1) + months
    return (idx // 12) * 100 + (idx % 12) + 1


def quarter_of(v) -> str:
    v = int(v)
    return f"{v // 100}Q{(v % 100 - 1) // 3 + 1}"


def to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce")


def pct(a, b) -> float:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return float("nan")
    if b == 0 or math.isnan(a) or math.isnan(b):
        return float("nan")
    return (a / b - 1.0) * 100.0


def share_table(df: pd.DataFrame, by: str, val: str, order: list | None = None) -> pd.DataFrame:
    g = df.groupby(by, dropna=False)[val].sum()
    tot = g.sum()
    out = (g / tot * 100.0 if tot else g * np.nan).rename("share").reset_index()
    if order:
        out[by] = pd.Categorical(out[by], categories=[o for o in order if o in set(out[by])] +
                                 [x for x in out[by] if x not in order], ordered=True)
        out = out.sort_values(by).reset_index(drop=True)
        out[by] = out[by].astype(str)
    return out


def month_rollup() -> Rollup:
    return Rollup("ym", lambda v: quarter_of(v) if str(v).isdigit() else v, "월 → 분기")


def nearest_assign(points: pd.DataFrame, centroids: dict[str, tuple[float, float]],
                   lon: str = "longitude", lat: str = "latitude") -> pd.Series:
    """좌표를 가장 가까운 지역 중심점으로 배정(경계 파일 없이 근사)."""
    if not centroids:
        return pd.Series([None] * len(points), index=points.index)
    names = list(centroids)
    c = np.array([centroids[n] for n in names], dtype=float)  # (k,2) lon,lat
    p = points[[lon, lat]].astype(float).values
    coslat = math.cos(math.radians(33.4))
    d = ((p[:, None, 0] - c[None, :, 0]) * coslat) ** 2 + (p[:, None, 1] - c[None, :, 1]) ** 2
    return pd.Series([names[i] for i in d.argmin(axis=1)], index=points.index)


def haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def minmax_score(s: pd.Series, invert: bool = False) -> pd.Series:
    s = s.astype(float)
    lo, hi = s.min(), s.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        out = pd.Series(50.0, index=s.index)
    else:
        out = (s - lo) / (hi - lo) * 100.0
    return 100.0 - out if invert else out
