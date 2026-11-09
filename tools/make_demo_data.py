"""가상(SYNTHETIC) 센터 CSV 생성 → data/demo_center/ (+ manifest.json).

사용법:
    .venv/Scripts/python.exe tools/make_demo_data.py [--out DIR]

- 형식: 제주빅데이터센터 다운로드 CSV와 동일(UTF-8, 헤더=명세서 영문 컬럼명·명세서 순서, 구분자 ',', 결측 NA,
  첫 컬럼만 따옴표 없음·나머지 값은 숫자 포함 큰따옴표 — 공식 답변 예시 행 형식). 데이터셋×월 1파일.
- 파일명: SYNTHETIC_<id>_<데이터셋명>_<YYYYMM>.csv  (모든 값은 가상이며 실제 센터 데이터가 아님)
- 규모 보정: 허브 공개 데이터(data/public: 746 카드 매출 2019년 셀 구조·금액, 748 유동인구 수준, 793 상가 위치,
  512 버스 시간대 분포, 746 매장수)를 기준으로 2023~2024년 수준으로 환산. 고정 seed → 재실행 시 바이트 단위 동일.
- 시나리오 신호(manifest.json 의 scenarios 참고)
  A 애월읍 카페(I56220): 주말·공휴일·오후 집중, 내국인(개별) 비중 높음, 여름 성수기, 2024 전년比 성장(+18%, 인접 읍 +4%)
  B 최남단 방어축제(대정읍, 2024-11-28~12-01): 방문인구·모슬포 정류소 승하차 급증, 대정읍 한식·수산물·주점·숙박 매출 증가
    (2023년 같은 기간과 비교지역 안덕면·한경면 대비). 2023년 동기에는 축제 효과를 넣지 않았다(비교 기준 명확화).
  C 버스: 시간대·요일 패턴, 저이용 정류소(영락리·구억리 등), 첨두 과밀 정류소(모슬포항)
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.catalog import regions as R  # noqa: E402
from app.catalog.catalog import load_catalog  # noqa: E402
from app.hub import public_data as P  # noqa: E402

SEED = 20261120
OUT = ROOT / "data" / "demo_center"
GEN_VERSION = "1.0"

HOLIDAYS = {
    2023: ["0101", "0121", "0122", "0123", "0124", "0301", "0505", "0527", "0529", "0606", "0815",
           "0928", "0929", "0930", "1002", "1003", "1009", "1225"],
    2024: ["0101", "0209", "0210", "0211", "0212", "0301", "0410", "0505", "0506", "0515", "0606",
           "0815", "0916", "0917", "0918", "1001", "1003", "1009", "1225"],
}
HOL = {date(y, int(s[:2]), int(s[2:])) for y, v in HOLIDAYS.items() for s in v}
WEEK_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
DOW_SHORT = ["월", "화", "수", "목", "금", "토", "일"]

FEST_START, FEST_END = date(2024, 11, 28), date(2024, 12, 1)
# 축제 일자 효과(대정읍): 전날 준비·당일·다음날 잔여
FEST_DAY = {date(2024, 11, 27): 1.2, date(2024, 11, 28): 2.2, date(2024, 11, 29): 2.6, date(2024, 11, 30): 3.2,
            date(2024, 12, 1): 2.8, date(2024, 12, 2): 1.15}
# 업종별 축제 민감도 k (일자 효과 = 1 + (F-1)·k)
FEST_BIZ = {"I56111": 1.0, "G47213": 1.4, "I56219": 1.2, "I56211": 0.8, "I55112": 1.3, "I55111": 1.1,
            "G47122": 0.6, "I56220": 0.5, "I56112": 0.6, "I56113": 0.8, "I56114": 0.5, "G47842": 0.7}

WEEK_PROFILES = {  # 월~일, 공휴일
    "cafe_tour": [0.82, 0.78, 0.80, 0.85, 1.08, 1.55, 1.45, 1.55],
    "cafe_local": [0.90, 0.88, 0.90, 0.92, 1.05, 1.30, 1.25, 1.30],
    "food": [0.95, 0.95, 0.97, 1.00, 1.12, 1.12, 0.98, 1.05],
    "bar": [0.75, 0.80, 0.85, 0.95, 1.35, 1.40, 0.80, 1.00],
    "lodging": [0.85, 0.80, 0.80, 0.85, 1.15, 1.45, 0.90, 1.30],
    "retail": [1.00, 0.98, 0.98, 1.00, 1.05, 1.05, 0.95, 0.95],
}
TIME_BANDS = ["오전", "점심", "오후", "저녁", "밤"]
TIME_PROFILES = {
    "cafe_tour": [0.13, 0.24, 0.44, 0.16, 0.03], "cafe_local": [0.18, 0.28, 0.34, 0.17, 0.03],
    "food": [0.07, 0.37, 0.13, 0.37, 0.06], "bar": [0.01, 0.03, 0.06, 0.45, 0.45],
    "lodging": [0.12, 0.08, 0.38, 0.27, 0.15], "retail": [0.20, 0.22, 0.25, 0.23, 0.10],
}
TIME_FEST = {"food": [0.05, 0.33, 0.22, 0.35, 0.05], "bar": [0.0, 0.05, 0.15, 0.50, 0.30],
             "retail": [0.10, 0.20, 0.32, 0.30, 0.08]}
# 2017년 허브 746(이용건수 포함) 기준 건당 금액(원) — 2019→2024 물가 반영 ×1.15
TICKET = {"G47119": 53617, "G47121": 27370, "G47122": 8170, "G47129": 39755, "G47130": 69617, "G47190": 72376,
          "G47212": 78735, "G47213": 90453, "G47214": 82941, "G47215": 14463, "G47216": 173577, "G47711": 54967,
          "G47712": 55354, "G47813": 40895, "G47842": 36242, "H49212": 300000, "H49231": 20000, "H50121": 30179,
          "H51100": 163118, "I55111": 331196, "I55112": 110772, "I55113": 105499, "I56111": 42962, "I56112": 30289,
          "I56113": 80527, "I56114": 24341, "I56119": 38750, "I56192": 13651, "I56211": 219886, "I56219": 188179,
          "I56220": 10811, "L69110": 93444, "L69210": 110611, "N75211": 215276, "N75992": 38091, "R91121": 162887,
          "R91139": 66442, "R91239": 183328, "R91249": 300000, "R91299": 51131, "S96121": 22939, "S96122": 125239}
USER_TICKET = {"제주도민": 0.85, "내국인": 1.0, "법인": 1.9, "중국": 1.6, "일본": 1.6, "동남아": 1.6, "기타외국": 1.6}

FOCUS_BJD = ["애월읍", "대정읍"]
NEIGH_A = ["한림읍", "조천읍", "구좌읍"]
A_BIZ = ["I56220", "I56111", "I56114", "G47215", "I55112", "G47122"]
CTRL_B = ["안덕면", "한경면"]
B_BIZ = ["I56111", "G47213", "I56219", "I56211", "I55112", "I55111", "G47122", "I56220"]
CONTEXT_BIZ = ["I56220", "I56111"]
SUBSET_112 = [(b, "I56220") for b in ["애월읍"] + NEIGH_A] + \
             [(b, z) for b in ["대정읍"] + CTRL_B for z in ["I56111", "G47213", "I56219", "I55112", "G47122"]]
POP_EMD = ["제주시 한림읍", "제주시 애월읍", "제주시 구좌읍", "제주시 조천읍", "제주시 한경면", "서귀포시 대정읍",
           "서귀포시 성산읍", "서귀포시 안덕면", "제주시 연동", "제주시 노형동", "제주시 일도1동", "서귀포시 중문동"]
VISIT_TOUR_SHARE = {"애월읍": 0.45, "성산읍": 0.50, "중문동": 0.55, "안덕면": 0.45, "구좌읍": 0.40, "조천읍": 0.35,
                    "한림읍": 0.40, "표선면": 0.35, "남원읍": 0.30, "대정읍": 0.30, "한경면": 0.25, "연동": 0.35,
                    "노형동": 0.25, "일도1동": 0.25, "천지동": 0.35}


def months(a: str, b: str) -> list[tuple[int, int]]:
    y, m = int(a[:4]), int(a[4:])
    out = []
    while (y, m) <= (int(b[:4]), int(b[4:])):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def days_of(y: int, m: int) -> list[date]:
    return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def rng_for(*keys) -> np.random.Generator:
    return np.random.default_rng([SEED] + [int(hashlib.md5(str(k).encode()).hexdigest()[:8], 16) for k in keys])


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "", s).strip()
    return re.sub(r"\s+", "_", s)


# ------------------------------------------------------------------------------------------- CSV 쓰기
def write_center_csv(path: Path, df: pd.DataFrame, cols: list[str]) -> dict:
    """첫 컬럼은 따옴표 없이, 나머지는 큰따옴표로(숫자 포함). 결측은 NA(따옴표 없음). UTF-8, LF."""
    out = df[cols[0]].astype(str)
    for c in cols[1:]:
        s = df[c]
        q = ('"' + s.astype(str) + '"').where(s.notna(), "NA")
        out = out + "," + q
    text = ",".join(cols) + "\n" + ("\n".join(out.tolist()) + "\n" if len(out) else "")
    data = text.encode("utf-8")
    path.write_bytes(data)
    return dict(file=path.name, rows=int(len(df)), bytes=len(data), sha1=hashlib.sha1(data).hexdigest())


class Writer:
    def __init__(self, out: Path):
        self.out = out
        self.cat = load_catalog()
        self.files: list[dict] = []

    def cols(self, ds_id: str, drop: tuple = ()) -> list[str]:
        return [c for c in self.cat.get(ds_id).column_names if c not in drop]

    def write(self, ds_id: str, ym: str, df: pd.DataFrame, drop: tuple = (), note: str = "") -> None:
        spec = self.cat.get(ds_id)
        name = f"SYNTHETIC_{ds_id}_{safe_name(spec.name)}_{ym}.csv"
        info = write_center_csv(self.out / name, df, self.cols(ds_id, drop))
        info.update(dataset_id=ds_id, dataset_name=spec.name, period=ym, columns_omitted=list(drop), note=note)
        self.files.append(info)


def fmt_int(a) -> pd.Series:
    return pd.Series(np.asarray(a, dtype=np.int64)).astype(str)


# ------------------------------------------------------------------------------------------- 카드 셀
def week_profile(bjd: str, biz: str) -> str:
    if biz == "I56220":
        return "cafe_tour" if bjd == "애월읍" else "cafe_local"
    if biz.startswith("I561") or biz in ("I56192",):
        return "food"
    if biz in ("I56211", "I56219"):
        return "bar"
    if biz.startswith("I55"):
        return "lodging"
    return "retail"


def day_weights(bjd: str, biz: str, y: int, m: int, festival: bool = True) -> np.ndarray:
    prof = WEEK_PROFILES[week_profile(bjd, biz)]
    w = []
    for d in days_of(y, m):
        x = prof[7] if d in HOL else prof[d.weekday()]
        if festival and bjd == "대정읍" and d in FEST_DAY:
            x *= 1 + (FEST_DAY[d] - 1) * FEST_BIZ.get(biz, 0.3)
        w.append(x)
    return np.array(w, dtype=float)


def fest_uplift(bjd: str, biz: str, y: int, m: int) -> float:
    if bjd != "대정읍" or (y, m) not in ((2024, 11), (2024, 12)):
        return 1.0
    return float(day_weights(bjd, biz, y, m, True).sum() / day_weights(bjd, biz, y, m, False).sum())


class CardModel:
    """746(2019) 셀 구조 → 2023~2024 가상 카드 셀(1-08 기준). 1-12/1-14 는 이 셀을 요일·시간대로 분할."""

    def __init__(self):
        cm = P.card_mashup()
        cm = cm[cm["ym"].str.startswith("2019")].copy()
        cm["m"] = cm["ym"].str.slice(5, 7).astype(int)
        cm["tour_type"] = cm["tour_type"].fillna("NA")
        demo = P.card_mashup_demo()
        demo = demo[demo["year"] == "2019"].copy()
        demo["tour_type"] = demo["tour_type"].fillna("NA")
        demo["age"] = demo["age"].fillna("NA")
        keys = ["bjd", "biz_cd", "user_type", "tour_type"]
        demo["share"] = demo["use_amt"] / demo.groupby(keys)["use_amt"].transform("sum")
        sel = set()
        allb = sorted(cm["bjd"].unique())
        for b in FOCUS_BJD:
            sel |= {(b, z) for z in TICKET}
        for b in NEIGH_A:
            sel |= {(b, z) for z in A_BIZ}
        for b in CTRL_B:
            sel |= {(b, z) for z in B_BIZ}
        for b in allb:
            sel |= {(b, z) for z in CONTEXT_BIZ}
        self.detailed = {(b, z) for (b, z) in sel if b in FOCUS_BJD + NEIGH_A + CTRL_B}
        cm = cm[[(b, z) in sel for b, z in zip(cm["bjd"], cm["biz_cd"])]]
        cells = cm.merge(demo[keys + ["age", "sex", "share"]], on=keys, how="left")
        cells["share"] = cells["share"].fillna(1.0)
        cells["age"] = cells["age"].fillna("NA")
        cells["sex"] = cells["sex"].fillna("알수없음")
        cells["base_amt"] = cells["use_amt"] * cells["share"]
        cells = cells[cells["base_amt"] > 0]
        # 비상세 지역(맥락용): (법정동,업종)별 상위 12개 셀만 남기고 합계 보존
        det = np.array([(b, z) in self.detailed for b, z in zip(cells["bjd"], cells["biz_cd"])])
        coarse = cells[~det].copy()
        coarse["rk"] = coarse.groupby(["m", "bjd", "biz_cd"])["base_amt"].rank(ascending=False, method="first")
        tot = coarse.groupby(["m", "bjd", "biz_cd"])["base_amt"].transform("sum")
        coarse = coarse[coarse["rk"] <= 12]
        coarse["base_amt"] *= tot.loc[coarse.index] / coarse.groupby(["m", "bjd", "biz_cd"])["base_amt"].transform("sum")
        self.cells = pd.concat([cells[det], coarse.drop(columns="rk")], ignore_index=True)
        self.cells = self.cells[["m", "city", "area_type", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type", "age", "sex",
                                 "base_amt"]].sort_values(["m", "bjd", "biz_cd", "user_type", "tour_type", "age", "sex"])
        self.cells = self.cells.reset_index(drop=True)
        self.drift = {}
        r = rng_for("drift")
        for b, z in sorted(set(zip(self.cells["bjd"], self.cells["biz_cd"]))):
            self.drift[(b, z)] = float(np.exp(r.normal(0, 0.035)))

    def month(self, y: int, m: int) -> pd.DataFrame:
        c = self.cells[self.cells["m"] == m].copy()
        r = rng_for("card108", y, m)
        g = 1.10 * (1.03 if y == 2024 else 1.0)
        mult = np.full(len(c), g)
        drift = np.array([self.drift[(b, z)] ** (1 if y == 2024 else 0) for b, z in zip(c["bjd"], c["biz_cd"])])
        mult *= drift
        is_cafe = c["biz_cd"].eq("I56220").values
        aw = c["bjd"].eq("애월읍").values
        # A: 애월 카페 — 시장 규모·성장·관광객 비중·젊은층·여름 성수기
        a_mask = aw & is_cafe
        mult[a_mask] *= 2.6 * ((1.18 / 1.03) if y == 2024 else 1.0) * {7: 1.12, 8: 1.20, 10: 1.05}.get(m, 1.0)
        ind = c["user_type"].eq("내국인").values & c["tour_type"].eq("개별").values
        grp = c["user_type"].eq("내국인").values & c["tour_type"].eq("단체").values
        mult[a_mask & ind] *= 1.5
        mult[a_mask & grp] *= 0.45
        mult[a_mask & c["age"].isin(["20", "30"]).values] *= 1.3
        n_mask = c["bjd"].isin(NEIGH_A).values & is_cafe
        mult[n_mask] *= 1.8 * ((1.04 / 1.03) if y == 2024 else 1.0)
        # B: 대정읍 축제(2024-11, 12) — 일자 가중치로 산출한 월 증가율 + 관광객 비중 상승
        if (y, m) in ((2024, 11), (2024, 12)):
            dj = c["bjd"].eq("대정읍").values
            for z in set(c.loc[dj, "biz_cd"]):
                up = fest_uplift("대정읍", z, y, m)
                mk = dj & c["biz_cd"].eq(z).values
                mult[mk] *= up
                mult[mk & ind] *= 1 + 0.6 * (up - 1)
        ut = c["user_type"].map(USER_TICKET).fillna(1.0).values
        tk = np.array([TICKET[z] for z in c["biz_cd"]]) * 1.15 * ut * (1.02 if y == 2024 else 1.0)
        pre = c["base_amt"].values * mult
        # 잡음은 셀 규모에 반비례(거래가 많은 큰 셀은 안정, 작은 셀은 변동 큼)
        sig_a = np.clip(0.12 / np.sqrt(np.maximum(pre / tk, 1) / 20), 0.02, 0.15)
        amt = pre * np.exp(r.normal(0, 1, len(c)) * sig_a)
        cnt = r.poisson(np.maximum(amt / tk, 0))
        keep = cnt > 0
        c, amt, tk, cnt = c[keep].copy(), amt[keep], tk[keep], cnt[keep]
        sig_t = np.clip(0.15 / np.sqrt(np.maximum(cnt, 1) / 5), 0.015, 0.15)
        unit = tk * np.exp(r.normal(0, 1, len(c)) * sig_t)
        use_amt = np.round(cnt * unit / 10) * 10
        use_amt = np.maximum(use_amt, 1000)
        ratio = np.where(c["biz_cd"].str.startswith(("I55", "G4713")), 1.15, 1.07) * np.exp(r.normal(0, 0.03, len(c)))
        user = np.clip(np.round(cnt / ratio), 1, cnt)
        c["use_cnt"], c["user_cnt"], c["use_amt"] = cnt.astype(np.int64), user.astype(np.int64), use_amt.astype(np.int64)
        c["y"] = y
        return c.reset_index(drop=True)


def card_frame_108(c: pd.DataFrame, y: int, m: int) -> pd.DataFrame:
    out = pd.DataFrame({
        "base_year_month": f"{y}{m:02d}", "pt_year": str(y), "pt_month": f"{m:02d}",
        "dstrct_se": [R.bjd_to_dstrct(b, ct)[0] for b, ct in zip(c["bjd"], c["city"])],
        "city": c["city"].values, "area_type": c["area_type"].values, "bjd": c["bjd"].values,
        "biz_cd": c["biz_cd"].values, "biz_nm": c["biz_nm"].values, "user_type": c["user_type"].values,
        "tour_type": c["tour_type"].replace({"NA": None}).values, "age": c["age"].replace({"NA": None}).values,
        "sex": c["sex"].values, "user_cnt": fmt_int(c["user_cnt"]).values, "use_cnt": fmt_int(c["use_cnt"]).values,
        "use_amt": fmt_int(c["use_amt"]).values})
    return out


def split_cells(c: pd.DataFrame, y: int, m: int, kind: str, r: np.random.Generator) -> pd.DataFrame:
    """1-08 셀을 요일(kind='week') 또는 시간대(kind='time')로 분할."""
    rows = []
    for (b, z), g in c.groupby(["bjd", "biz_cd"], sort=True):
        n = g["use_cnt"].values.astype(np.int64)
        if kind == "week":
            w = day_weights(b, z, y, m, True)
            p = w / w.sum()
            dd = days_of(y, m)
            k = r.multinomial(n, np.broadcast_to(p, (len(n), len(p))))
            wd = np.array([d.weekday() for d in dd])
            hol = np.array([d in HOL for d in dd])
            cols = {WEEK_KO[i]: k[:, wd == i].sum(axis=1) for i in range(7)}
            cols["공휴일"] = k[:, hol].sum(axis=1) if hol.any() else np.zeros(len(n), dtype=int)
            labels = WEEK_KO + ["공휴일"]
        else:
            prof = week_profile(b, z)
            base = np.array(TIME_PROFILES[prof])
            f = 0.0
            if b == "대정읍" and (y, m) in ((2024, 11), (2024, 12)):
                up = fest_uplift(b, z, y, m)
                f = (up - 1) / up
            pf = np.array(TIME_FEST.get(prof, TIME_PROFILES[prof]))
            p = (1 - f) * base + f * pf
            p = p / p.sum()
            k = r.multinomial(n, np.broadcast_to(p, (len(n), len(p))))
            cols = {TIME_BANDS[i]: k[:, i] for i in range(5)}
            labels = TIME_BANDS
        ticket = g["use_amt"].values / np.maximum(n, 1)
        ratio = g["use_cnt"].values / np.maximum(g["user_cnt"].values, 1)
        for lab in labels:
            cnt = cols[lab]
            mk = cnt > 0
            if not mk.any():
                continue
            gg = g[mk]
            amt = np.round(cnt[mk] * ticket[mk] * np.exp(r.normal(0, 0.05, mk.sum())) / 10) * 10
            usr = np.clip(np.round(cnt[mk] / ratio[mk]), 1, cnt[mk])
            rows.append(pd.DataFrame({"bjd": gg["bjd"].values, "city": gg["city"].values, "biz_cd": gg["biz_cd"].values,
                                      "biz_nm": gg["biz_nm"].values, "user_type": gg["user_type"].values,
                                      "tour_type": gg["tour_type"].values, "age": gg["age"].values,
                                      "sex": gg["sex"].values, kind: lab, "use_cnt": cnt[mk],
                                      "user_cnt": usr.astype(np.int64), "use_amt": np.maximum(amt, 1000).astype(np.int64)}))
    d = pd.concat(rows, ignore_index=True)
    order = {v: i for i, v in enumerate(WEEK_KO + ["공휴일"] if kind == "week" else TIME_BANDS)}
    d["_o"] = d[kind].map(order)
    d = d.sort_values(["bjd", "biz_cd", "user_type", "tour_type", "_o", "age", "sex"], na_position="last")
    return d.drop(columns="_o").reset_index(drop=True)


def card_frame_split(d: pd.DataFrame, y: int, m: int, kind: str) -> pd.DataFrame:
    return pd.DataFrame({
        "base_year_month": f"{y}{m:02d}", "pt_year": str(y), "pt_month": f"{m:02d}",
        "dstrct_se": [R.bjd_to_dstrct(b, ct)[0] for b, ct in zip(d["bjd"], d["city"])], "bjd": d["bjd"].values,
        "biz_cd": d["biz_cd"].values, "biz_nm": d["biz_nm"].values, "user_type": d["user_type"].values,
        "tour_type": d["tour_type"].replace({"NA": None}).values, kind: d[kind].values,
        "age": d["age"].replace({"NA": None}).values, "sex": d["sex"].values,
        "user_cnt": fmt_int(d["user_cnt"]).values, "use_cnt": fmt_int(d["use_cnt"]).values,
        "use_amt": fmt_int(d["use_amt"]).values})


# ------------------------------------------------------------------------------------------- 1-17
def make_1_17(w: Writer) -> None:
    st = P.card_mashup_stores()
    base = st[st["ym"] == st["ym"].max()].copy()
    biz = {b["code"]: b["name"] for b in load_catalog().reference["biz42"]}
    owners = ["내국인 개인", "제주도민 개인", "법인", "외국인", None]
    shares = np.array([0.17, 0.58, 0.21, 0.01, 0.03])
    growth = {"I56220": 1.18, "L69110": 0.95, "I56111": 1.06, "G47122": 1.10, "I55112": 1.04}
    for y, m in months("202401", "202412"):
        r = rng_for("1-17", y, m)
        rows = []
        for city in ["제주시", "서귀포시"]:
            for code, nm in biz.items():
                b = base[(base["city"] == city) & (base["biz_cd"] == code)]
                n0 = int(b["store_cnt"].iloc[0]) if len(b) and pd.notna(b["store_cnt"].iloc[0]) else int(r.integers(1, 6))
                n0 = max(1, int(n0 * (0.62 if city == "서귀포시" else 1.0)))
                g = growth.get(code, 1.04) * (1 + 0.004 * (m - 6) * (0.9 if code != "I56220" else 1.6))
                tot = max(1, int(round(n0 * g * np.exp(r.normal(0, 0.01)))))
                k = r.multinomial(tot, shares)
                for o, c in zip(owners, k):
                    if c > 0:
                        rows.append((city, o, code, nm, c))
        d = pd.DataFrame(rows, columns=["city", "owner_gb", "biz_cd", "biz_nm", "store_cnt"])
        df = pd.DataFrame({"base_year_month": f"{y}{m:02d}", "pt_year": str(y), "pt_month": str(m),
                           "city": d["city"], "owner_gb": d["owner_gb"], "biz_cd": d["biz_cd"], "biz_nm": d["biz_nm"],
                           "store_cnt": fmt_int(d["store_cnt"])})
        w.write("1-17", f"{y}{m:02d}", df)


# ------------------------------------------------------------------------------------------- 4-10
HOUR_RESD = np.array([1.18] * 7 + [1.1, 0.95, 0.85, 0.82, 0.82, 0.82, 0.82, 0.82, 0.84, 0.88, 0.95, 1.02, 1.08, 1.12,
                      1.15, 1.16, 1.17])  # 시각 1..24 (index 0 = 1시)
HOUR_WORK_WD = np.array([0.25] * 7 + [0.6, 1.2, 1.8, 1.95, 1.95, 1.8, 1.9, 1.95, 1.9, 1.7, 1.2, 0.7, 0.45, 0.35, 0.3,
                         0.28, 0.26])
HOUR_VISIT = np.array([0.35, 0.3, 0.28, 0.27, 0.28, 0.35, 0.5, 0.75, 1.0, 1.25, 1.45, 1.55, 1.6, 1.62, 1.6, 1.55, 1.45,
                       1.3, 1.15, 1.05, 0.9, 0.7, 0.55, 0.42])
FEST_HOUR = np.array([0.0] * 7 + [0.2, 0.5, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 0.4, 0.1])


def make_4_10(w: Writer) -> None:
    pop = P.card_pop_population()
    pop = pop[pop["ym"].str.startswith("2019")].copy()
    pop["m"] = pop["ym"].str.slice(5, 7).astype(int)
    emds = []
    for full in POP_EMD:
        h = R.get_hjd(full)
        emds.append((full, h["code"], h["name"], h["city"]))
    for y, m in [(2023, 11), (2023, 12), (2024, 11), (2024, 12)]:
        r = rng_for("4-10", y, m)
        dd = days_of(y, m)
        nd = calendar.monthrange(2019, m)[1]
        parts = []
        for full, code, short, city in emds:
            p = pop[(pop["emd"] == short) & (pop["city"] == city) & (pop["m"] == m)]
            lv = {k: float(p[k].sum()) / (nd * 24) for k in ("resd_pop", "work_pop", "visit_pop")}
            g_v = (1.06 if short != "애월읍" else 1.10) * (1.05 if y == 2024 else 1.0) * (1.08 if short == "애월읍" and y == 2024 else 1.0)
            tour = VISIT_TOUR_SHARE.get(short, 0.3)
            for d in dd:
                wkend = d.weekday() >= 5 or d in HOL
                vday = (1.25 if tour >= 0.4 else 1.12) if wkend else 1.0
                vday *= np.exp(r.normal(0, 0.05))
                work = HOUR_WORK_WD * (0.45 if wkend else 1.0)
                fmul = np.ones(24)
                if short == "대정읍" and d in FEST_DAY and y == 2024:
                    fmul = 1 + (FEST_DAY[d] - 1) * FEST_HOUR
                for resd, sh in (("제주", 1 - tour), ("그외", tour)):
                    rp = lv["resd_pop"] * (0.975 if resd == "제주" else 0.025) * (1.01 if y == 2024 else 1.0) * HOUR_RESD
                    wp = lv["work_pop"] * (0.94 if resd == "제주" else 0.06) * work
                    fm = fmul if resd == "제주" else 1 + (fmul - 1) * 1.35
                    vp = lv["visit_pop"] * sh * g_v * vday * HOUR_VISIT * fm
                    noise = np.exp(r.normal(0, 0.04, (3, 24)))
                    parts.append(pd.DataFrame({
                        "base_date": f"{d:%Y%m%d}", "pt_year": str(y), "pt_month": str(m),
                        "time": [str(h) for h in range(1, 25)], "emd_cd": code, "emd_nm": full,
                        "resd_pop_cnt": np.round(rp * noise[0], 4), "work_pop_cnt": np.round(wp * noise[1], 4),
                        "visit_pop_cnt": np.round(vp * noise[2], 4), "resd": resd}))
        df = pd.concat(parts, ignore_index=True).sort_values(["base_date", "emd_cd", "resd", "time"],
                                                             key=lambda s: s.astype(int) if s.name == "time" else s)
        for c in ("resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"):
            df[c] = df[c].map("{:.4f}".format)
        w.write("4-10", f"{y}{m:02d}", df, drop=("sex", "age"),
                note="센터 화면에서 행정동 12개를 조건으로 선택하고 성별·연령대 컬럼은 선택하지 않은 경우를 가정(선택 컬럼 CSV 예시)")


# ------------------------------------------------------------------------------------------- 버스 정류소
def build_stops() -> pd.DataFrame:
    ri = {(x["bjd"], x["name"]): (x["lon"], x["lat"]) for x in R._data()["ri"]}
    rows = []

    def add(name, bjd, lon, lat, base, kind="minor", city_type="시내", cat="default", fest=0.0, in304=True,
            in2023=False, extra=False):
        rows.append(dict(name=name, bjd=bjd, lon=round(lon, 6), lat=round(lat, 6), base=base, kind=kind,
                         city_type=city_type, cat=cat, fest=fest, in304=in304, in2023=in2023, extra=extra))
    # 대정읍·모슬포(시나리오 B·C)
    dj = [("모슬포항", 126.2516, 33.2166, 900, "hub", "시외", "default", 1.0),
          ("모슬포 중앙", 126.2545, 33.2205, 380, "major", "시외", "default", 0.6),
          ("대정읍사무소", 126.2528, 33.2238, 450, "major", "시외", "default", 0.5),
          ("하모체육공원", 126.2480, 33.2200, 120, "mid", "시내", "default", 0.6),
          ("하모리", 126.2566, 33.2178, 150, "mid", "시내", "default", 0.7),
          ("대정고등학교", 126.2610, 33.2290, 85, "mid", "시내", "student", 0.1),
          ("상모리", *ri[("대정읍", "상모리")], 110, "mid", "시내", "default", 0.3),
          ("상모리 입구", 126.2655, 33.2262, 40, "minor", "시내", "rural", 0.1),
          ("동일리", *ri[("대정읍", "동일리")], 35, "minor", "시내", "rural", 0.1),
          ("일과리", *ri[("대정읍", "일과리")], 30, "minor", "시내", "rural", 0.0),
          ("인성리", *ri[("대정읍", "인성리")], 25, "minor", "시내", "rural", 0.0),
          ("안성리", *ri[("대정읍", "안성리")], 30, "minor", "시내", "rural", 0.0),
          ("보성리", *ri[("대정읍", "보성리")], 28, "minor", "시외", "rural", 0.0),
          ("신평리", *ri[("대정읍", "신평리")], 22, "minor", "시내", "rural", 0.0),
          ("무릉리", *ri[("대정읍", "무릉리")], 6, "rural", "시내", "rural", 0.0),
          ("영락리", *ri[("대정읍", "영락리")], 3, "rural", "시내", "rural", 0.0),
          ("구억리", *ri[("대정읍", "구억리")], 2, "rural", "시내", "rural", 0.0)]
    main6 = {"모슬포항", "모슬포 중앙", "대정읍사무소", "하모체육공원", "하모리", "상모리"}
    for n, lon, lat, b, k, ct, cat, f in dj:
        add(n, "대정읍", lon, lat, b, k, ct, cat, f, True, n in main6)
    # 비교지역·애월: 3-04 에는 주요 정류소만(용량 제한), 나머지는 3-03(위치)에만
    for n, b, use in [("화순리", 180, True), ("사계리", 90, True), ("덕수리", 25, True), ("창천리", 60, False),
                      ("감산리", 30, False)]:
        add(n, "안덕면", *ri[("안덕면", n)], b, "minor", "시외" if n == "화순리" else "시내", "rural", 0.0, use, False)
    for n, b, use in [("고산리", 160, True), ("신창리", 45, True), ("저지리", 50, True), ("용수리", 10, False)]:
        add(n, "한경면", *ri[("한경면", n)], b, "minor", "시외" if n == "고산리" else "시내", "rural", 0.0, use, False)
    for n, b, use in [("애월리", 220, True), ("곽지리", 90, True), ("고내리", 40, True), ("하귀1리", 300, True),
                      ("구엄리", 60, True), ("신엄리", 45, False), ("납읍리", 25, False), ("수산리", 35, False)]:
        add(n.replace("1리", "") if n == "하귀1리" else n, "애월읍", *ri[("애월읍", n)], b, "mid" if b >= 150 else "minor",
            "시외" if n in ("애월리", "하귀1리") else "시내", "default", 0.0, use, False)
    add("제주시청", "이도이동", 126.5312, 33.4996, 3500, "hub", "시내", "default", 0.0, False, False)
    # 섬 전역 맥락용 정류소(3-03 에만 포함): 법정동 중심 주변 2개씩
    r = rng_for("stops")
    used = {x["bjd"] for x in rows}
    for b in R.bjd_list():
        if b["lon"] is None:
            continue
        k = 1 if b["name"] in used else 2
        for j in range(k):
            lon = b["lon"] + r.normal(0, 0.004)
            lat = b["lat"] + r.normal(0, 0.003)
            suffix = ["입구", "사거리", "복지회관", "중앙", "초등학교"][int(r.integers(0, 5))]
            add(f"{b['name']} {suffix}" + ("" if j == 0 else " 2"), b["name"], lon, lat, 0, "context", "시내",
                "default", 0.0, False, False)
    # 시나리오 C 보강: 모슬포(126.25, 33.22) 반경 3km 안의 저이용 정류소 2곳(일평균 승차 5명 미만).
    # 기존 정류소의 ID·일련번호·이용 기록이 바뀌지 않도록 맨 뒤에 추가하고 별도 난수 흐름(extra)으로 생성.
    add("하모해수욕장", "대정읍", 126.2440, 33.2150, 2.5, "rural", "시내", "rural", 0.0, True, False, extra=True)
    add("모슬봉 입구", "대정읍", 126.2640, 33.2350, 3.0, "rural", "시내", "rural", 0.0, True, False, extra=True)
    st = pd.DataFrame(rows)
    st = st.drop_duplicates("name").reset_index(drop=True)
    city = {b["name"]: b["city"] for b in R.bjd_list()}
    st["city"] = st["bjd"].map(city)
    seq = {"제주시": 0, "서귀포시": 0}
    ids = []
    for c in st["city"]:
        seq[c] += 1
        ids.append(f"{405 if c == '제주시' else 406}{seq[c]:04d}")
    st["bus_station_id"] = ids
    return st


def make_3_03(w: Writer, st: pd.DataFrame) -> None:
    df = pd.DataFrame({"serial_number": [str(i + 1) for i in range(len(st))], "base_date": "20241101",
                       "pt_year": "2024", "pt_month": "11", "bus_station_id": st["bus_station_id"],
                       "bus_station_name": st["name"], "bus_city_type": st["city_type"],
                       "longitude": st["lon"].map("{:.6f}".format), "latitude": st["lat"].map("{:.6f}".format)})
    w.write("3-03", "202411", df, note="2024-11-01 스냅샷 1일치(실데이터는 일 단위 변경이력 누적)")


BUS_HOUR = {5: 0.010, 6: 0.030, 7: 0.075, 8: 0.080, 9: 0.062, 10: 0.058, 11: 0.058, 12: 0.058, 13: 0.062, 14: 0.064,
            15: 0.066, 16: 0.072, 17: 0.078, 18: 0.068, 19: 0.050, 20: 0.035, 21: 0.022, 22: 0.010, 23: 0.004}
BUS_DOW = [1.07, 0.98, 1.0, 1.0, 1.08, 0.94, 0.82]
CATS = ["어린이", "청소년", "일반", "경로", "장애 일반", "장애 동반", "유공 일반", "유공 동반", "기타"]
CAT_SH = {"default": [0.04, 0.16, 0.57, 0.15, 0.035, 0.025, 0.015, 0.002, 0.003],
          "rural": [0.03, 0.12, 0.46, 0.28, 0.05, 0.035, 0.02, 0.002, 0.003],
          "student": [0.03, 0.56, 0.25, 0.10, 0.03, 0.02, 0.005, 0.002, 0.003]}


def hour_profile(kind: str, cat: str, weekend: bool) -> dict[int, float]:
    p = dict(BUS_HOUR)
    if weekend:
        for h in (6, 7, 8, 17, 18):
            p[h] *= 0.7
    if cat == "student":
        for h in p:
            p[h] *= {7: 3.0, 8: 2.0, 16: 2.5, 17: 2.0}.get(h, 0.5)
    if kind == "hub":  # 모슬포항·제주시청: 첨두 집중(과밀)
        for h in (7, 8, 17, 18):
            p[h] *= 1.5
    s = sum(p.values())
    return {h: v / s for h, v in p.items()}


def make_3_04(w: Writer, st: pd.DataFrame) -> None:
    for y, m in [(2023, 11), (2023, 12), (2024, 11), (2024, 12)]:
        r_main = rng_for("3-04", y, m)
        r_extra = rng_for("3-04x", y, m)          # 추가 정류소 전용(기존 정류소 값은 그대로 유지)
        sub = st[st["in304"] & (st["in2023"] if y == 2023 else True)]
        parts = []
        for d in days_of(y, m):
            wkend = d.weekday() >= 5 or d in HOL
            dowf = 0.8 if d in HOL else BUS_DOW[d.weekday()]
            for s in sub.itertuples():
                r = r_extra if s.extra else r_main
                if s.cat == "student" and wkend:
                    dfac = 0.3
                else:
                    dfac = dowf
                daily = s.base * dfac * (1.03 if y == 2024 else 1.0) * np.exp(r.normal(0, 0.08))
                prof = hour_profile(s.kind, s.cat, wkend)
                hours = np.array(sorted(prof))
                lam_on = daily * np.array([prof[h] for h in hours])
                if y == 2024 and d in FEST_DAY and s.fest > 0:
                    lam_on = lam_on * (1 + (FEST_DAY[d] - 1) * s.fest * np.array([FEST_HOUR[h - 1] for h in hours]))
                lam_tr = lam_on * (0.14 if s.kind == "hub" else 0.06)
                prof_off = {h: prof.get(h - 1, prof[h] * 0.5) for h in hours}
                lam_off = (lam_on.sum() + lam_tr.sum()) * 0.95 * np.array([prof_off[h] for h in hours]) / sum(prof_off.values())
                if y == 2024 and d in FEST_DAY and s.fest > 0:
                    lam_off = lam_off * (1 + (FEST_DAY[d] - 1) * s.fest * np.array([FEST_HOUR[min(h, 23) - 1] for h in hours]))
                sh = np.array(CAT_SH[s.cat])
                for typ, lam in (("승차", lam_on), ("환승", lam_tr), ("하차", lam_off)):
                    cnt = r.poisson(lam[:, None] * sh[None, :])
                    hi, ci = np.nonzero(cnt)
                    if len(hi) == 0:
                        continue
                    parts.append(pd.DataFrame({
                        "base_date": f"{d:%Y%m%d}", "pt_year": str(y), "pt_month": str(m),
                        "day_of_week": DOW_SHORT[d.weekday()], "base_hour": [f"{hours[i]:02d}" for i in hi],
                        "bus_station_id": s.bus_station_id, "bus_station_name": s.name, "get_on_off_type": typ,
                        "user_category": [CATS[j] for j in ci], "pay_type": "카드", "user_count": cnt[hi, ci]}))
        df = pd.concat(parts, ignore_index=True)
        tord = {"승차": 0, "환승": 1, "하차": 2}
        cord = {c: i for i, c in enumerate(CATS)}
        df = df.assign(_t=df["get_on_off_type"].map(tord), _c=df["user_category"].map(cord))
        df = df.sort_values(["base_date", "base_hour", "bus_station_id", "_t", "_c"]).drop(columns=["_t", "_c"])
        df["user_count"] = fmt_int(df["user_count"]).values
        note = "2023년은 모슬포 일대 대정읍 주요 정류소 6개만 포함(전년 동기 비교용)" if y == 2023 else ""
        w.write("3-04", f"{y}{m:02d}", df, note=note)


ROUTES = [("40225100", "251(제주버스터미널~모슬포남항, 평화로)", 1500, 0.6, "default"),
          ("40215100", "151(제주국제공항~모슬포남항, 급행)", 1100, 0.7, "default"),
          ("40575001", "750-1(모슬포남항~영락리)", 180, 0.4, "rural"),
          ("40575002", "750-2(모슬포남항~구억리)", 120, 0.2, "rural")]


def make_3_02(w: Writer) -> None:
    for y, m in [(2024, 11), (2024, 12)]:
        r = rng_for("3-02", y, m)
        parts = []
        for d in days_of(y, m):
            wkend = d.weekday() >= 5 or d in HOL
            dowf = 0.8 if d in HOL else BUS_DOW[d.weekday()]
            for rid, rname, base, fest, cat in ROUTES:
                prof = hour_profile("major", cat, wkend)
                hours = np.array(sorted(prof))
                lam = base * dowf * np.exp(r.normal(0, 0.07)) * np.array([prof[h] for h in hours])
                if d in FEST_DAY:
                    lam = lam * (1 + (FEST_DAY[d] - 1) * fest * np.array([FEST_HOUR[h - 1] for h in hours]))
                sh = np.array(CAT_SH[cat])
                for typ, lt in (("승차", lam), ("환승", lam * 0.18)):
                    cnt = r.poisson(lt[:, None] * sh[None, :])
                    hi, ci = np.nonzero(cnt)
                    if len(hi) == 0:
                        continue
                    parts.append(pd.DataFrame({
                        "base_date": f"{d:%Y%m%d}", "pt_year": str(y), "pt_month": str(m),
                        "day_of_week": DOW_SHORT[d.weekday()], "base_hour": [f"{hours[i]:02d}" for i in hi],
                        "bus_route_id": rid, "bus_route_name": rname, "get_on_off_type": typ,
                        "user_category": [CATS[j] for j in ci], "pay_type": "카드", "user_count": cnt[hi, ci]}))
        df = pd.concat(parts, ignore_index=True)
        cord = {c: i for i, c in enumerate(CATS)}
        df = df.assign(_c=df["user_category"].map(cord)).sort_values(
            ["base_date", "base_hour", "bus_route_id", "get_on_off_type", "_c"]).drop(columns="_c")
        df["user_count"] = fmt_int(df["user_count"]).values
        w.write("3-02", f"{y}{m:02d}", df, note="대정읍 경유 노선 4개(노선명·번호는 가상)")


# ------------------------------------------------------------------------------------------- 5-02
CLIM_T = [6.1, 7.3, 10.3, 14.6, 18.6, 22.1, 26.4, 27.5, 24.0, 19.3, 13.8, 8.6]
RAIN_P = [0.35, 0.33, 0.33, 0.30, 0.30, 0.40, 0.42, 0.40, 0.38, 0.22, 0.30, 0.38]
OBS = {"184": ("제주", 0.0, 1.0), "185": ("고산", -0.5, 1.5), "188": ("성산", -0.3, 1.1), "189": ("서귀포", 0.8, 0.8)}


def make_5_02(w: Writer) -> None:
    r = rng_for("5-02")
    anom = 0.0
    series = []
    for y, m in months("202301", "202412"):
        for d in days_of(y, m):
            anom = 0.7 * anom + r.normal(0, 1.6)
            rain_day = r.random() < RAIN_P[m - 1]
            base_rain = r.gamma(0.8, 9.0) if rain_day else 0.0
            series.append((d, anom, rain_day, base_rain, r.random()))
    for y, m in months("202301", "202412"):
        rows = []
        for (d, anom, rain_day, base_rain, u) in [s for s in series if (s[0].year, s[0].month) == (y, m)]:
            for code, (nm, toff, wmul) in OBS.items():
                rr = rng_for("5-02", code, d.isoformat())
                avg = CLIM_T[m - 1] + toff + anom + rr.normal(0, 0.4)
                lo = avg - rr.uniform(2.0, 4.5)
                hi = avg + rr.uniform(2.5, 5.0)
                rain = max(0.0, base_rain * rr.uniform(0.5, 1.5)) if rain_day and rr.random() < 0.85 else 0.0
                wind_avg = max(0.5, (3.0 + (1.5 if m in (12, 1, 2) else 0)) * wmul + rr.normal(0, 0.8))
                wind_max = wind_avg * rr.uniform(2.0, 3.2)
                rows.append(dict(base_date=f"{d:%Y%m%d}", pt_year=str(y), pt_month=str(m), observatory_code=code,
                                 average_temperature=f"{avg:.1f}", lowest_temperature=f"{lo:.1f}",
                                 lowest_temperature_time=f"{int(rr.integers(1, 8)):02d}{int(rr.integers(0, 60)):02d}",
                                 highest_temperature=f"{hi:.1f}",
                                 highest_temperature_time=f"{int(rr.integers(12, 16)):02d}{int(rr.integers(0, 60)):02d}",
                                 daily_rainfall=f"{rain:.1f}", maximum_wind_speed=f"{wind_max:.1f}",
                                 maximum_wind_speed_time=f"{int(rr.integers(0, 24)):02d}{int(rr.integers(0, 60)):02d}",
                                 average_wind_speed=f"{wind_avg:.1f}",
                                 maximum_wind_speed_direction=f"{(315 if m in (11, 12, 1, 2) else 180) + rr.normal(0, 40):.1f}"))
        w.write("5-02", f"{y}{m:02d}", pd.DataFrame(rows))


# ------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    for old in a.out.glob("SYNTHETIC_*.csv"):
        old.unlink()
    w = Writer(a.out)
    cm = CardModel()
    for y, m in months("202301", "202412"):
        c = cm.month(y, m)
        w.write("1-08", f"{y}{m:02d}", card_frame_108(c, y, m))
        if y == 2024:
            sub = c[[(b, z) in set(SUBSET_112) for b, z in zip(c["bjd"], c["biz_cd"])]]
            wk = split_cells(sub, y, m, "week", rng_for("1-12", y, m))
            w.write("1-12", f"{y}{m:02d}", card_frame_split(wk, y, m, "week"),
                    note="법정동·업종 조건 선택 조회 가정(애월·인접 읍 카페, 대정·안덕·한경 주요 5업종)")
            tm = split_cells(sub, y, m, "time", rng_for("1-14", y, m))
            w.write("1-14", f"{y}{m:02d}", card_frame_split(tm, y, m, "time"),
                    note="법정동·업종 조건 선택 조회 가정(1-12와 같은 조건)")
    make_1_17(w)
    make_4_10(w)
    st = build_stops()
    make_3_03(w, st)
    make_3_04(w, st)
    make_3_02(w)
    make_5_02(w)
    total = sum(f["bytes"] for f in w.files)
    by_ds: dict[str, dict] = {}
    for f in w.files:
        e = by_ds.setdefault(f["dataset_id"], dict(name=f["dataset_name"], files=0, rows=0, bytes=0, periods=[]))
        e["files"] += 1
        e["rows"] += f["rows"]
        e["bytes"] += f["bytes"]
        e["periods"].append(f["period"])
    manifest = {
        "synthetic": True,
        "title": "오름(OREUM) 시연용 가상 센터 데이터 — 실제 제주빅데이터센터 데이터가 아닙니다",
        "generator": "tools/make_demo_data.py", "generator_version": GEN_VERSION, "seed": SEED,
        "format": "UTF-8(BOM 없음), 헤더=명세서 영문 컬럼명(명세서 순서), 구분자 ',', 결측 NA, 첫 컬럼 외 값은 큰따옴표, 1개월 1파일",
        "calibration": ["허브 746(2019) 업종별 카드매출 셀 구조·금액 → 2023~2024 수준(×1.10~1.13) 환산",
                        "허브 746(2017) 업종별 건당 금액으로 이용건수 산출", "허브 748(2019) 행정동별 거주·근무·방문 인구 수준",
                        "허브 793 상가 위치(리 중심좌표)로 정류소 좌표", "허브 512 시간대·요일 분포로 버스 이용 패턴",
                        "허브 746 매장수(2021)로 업종별 매장 수"],
        "scenarios": {
            "A_trade_aewol_cafe": "애월읍 비알콜 음료점업(I56220): 2024 전년比 +18%(한림·조천·구좌 +4%), 내국인(개별) 비중↑, "
                                  "20~30대↑, 7~8월 성수기, 요일 토·일·공휴일↑, 시간대 오후(14~18시) 집중, 방문인구 주말↑",
            "B_event_bangeo_2024": "대정읍 2024-11-28~12-01(최남단 방어축제): 일자 효과 11/27 ×1.2, 11/28 ×2.2, 11/29 ×2.6, "
                                   "11/30 ×3.2, 12/01 ×2.8, 12/02 ×1.15(업종 민감도 반영) → 1-08/1-12/1-14 한식·수산물·주점·숙박·편의점 "
                                   "증가, 4-10 방문인구(10~21시, 도외 거주 비중↑), 3-04 모슬포항 일대 정류소 승하차, 3-02 대정 경유 노선. "
                                   "2023년 동기·비교지역(안덕면·한경면)에는 효과 없음",
            "C_transit_daejeong": "3-04: 출퇴근 첨두(07~08, 17~18시), 월·금↑ 일↓, 모슬포항 첨두 과밀(시간당 100명+), "
                                  "저이용(모슬포 반경 3km 안 하모해수욕장·모슬봉 입구, 외곽 영락리·구억리 — 일평균 승차 5명 미만), "
                                  "대정고등학교 청소년 비중↑",
        },
        "limits": ["1-12·1-14 는 일부 법정동·업종 조건 조회 가정", "1-08 은 시나리오 지역(애월·대정·인접·비교지역)만 전 업종 상세, "
                   "그 밖의 법정동은 카페·한식 2개 업종을 축약(상위 셀) 형태로 포함",
                   "4-10 은 행정동 12개, 성별·연령 컬럼 미선택(선택 컬럼 CSV)",
                   "3-04 는 대정읍 19개·안덕 3·한경 3·애월 5개 정류소(2023년은 모슬포 일대 6개만), 3-03 은 섬 전역 180개",
                   "정류소·노선 이름·ID 는 가상", "5-02 관측소 코드 184 제주·185 고산·188 성산·189 서귀포(값은 가상)"],
        "datasets": by_ds,
        "total_bytes": total,
        "files": w.files,
    }
    (a.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in sorted(by_ds.items()):
        print(f"{k:5s} files={v['files']:3d} rows={v['rows']:9,d} size={v['bytes'] / 1e6:6.2f} MB  {v['name']}")
    print(f"total {total / 1e6:.2f} MB, {len(w.files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
