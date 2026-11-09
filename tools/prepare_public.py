"""제주데이터허브 공개 원본 파일 → data/public/*.parquet (+ PROVENANCE.json, app/catalog/region_data.json).

사용법:
    .venv/Scripts/python.exe tools/prepare_public.py [--raw-dir DIR ...] [--out DIR]

원본(허브에서 로그인 없이 내려받은 공개 파일)은 저장소에 넣지 않고, 필요한 컬럼·집계만 남긴 압축본을 만든다.
- 793 상가(상권) 정보      → stores.parquet          (필요 컬럼만 + 리(里) 이름)
- 746 업종별 카드매출·매장수 → card_mashup.parquet     (월×법정동×업종×이용자구분×관광구분 합계, 2017.01~2021.05)
                              card_mashup_stores.parquet (월×시군구×업종 매장수)
- 748 유동인구-카드매출     → card_pop_sales.parquet / card_pop_population.parquet
- 761 VISIT JEJU 축제행사   → festivals.parquet       (중복·깨진 행 정리)
- 512 일별 시간대별 버스 승차 → bus_daily_timeband.parquet
- 793 기반 법정동↔행정동 매핑·중심좌표 → app/catalog/region_data.json
원본 파일 위치는 --raw-dir 로 지정(기본: ../_research/hub_data, ../_research/hub_data_extra).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW = [ROOT.parent / "_research" / "hub_data", ROOT.parent / "_research" / "hub_data_extra"]
DEFAULT_OUT = ROOT / "data" / "public"
REGION_OUT = ROOT / "app" / "catalog" / "region_data.json"
HUB_URL = "https://jejudatahub.net/data/view/data/{}"
PQ = dict(index=False, compression="zstd")

TITLES = {
    "512": "일별 시간대별 버스 승차 인원 수 현황",
    "746": "제주특별자치도_주제1_제주 예비창업자를 위한 업종별 카드 매출과 매장수 데이터 활용_매쉬업 결과",
    "748": "제주특별자치도_주제3_상권분석을 위한 유동인구에 따른 카드 매출액 변화 데이터 활용_매쉬업결과",
    "761": "제주관광공사_제주관광정보시스템(VISIT JEJU)_축제행사콘텐츠",
    "793": "제주특별자치도 상가(상권) 정보",
}
ORIGINAL_NAMES = {}  # 파일ID → 허브 원본 파일명 (hub_files.json 이 있으면 채움)


def read_csv_any(path: Path, **kw) -> pd.DataFrame:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"인코딩을 알 수 없음: {path}")
    return pd.read_csv(io.StringIO(text), **kw)


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def find(raw_dirs: list[Path], data_id: str) -> list[Path]:
    out = []
    for d in raw_dirs:
        if d.exists():
            out += sorted(d.glob(f"{data_id}_*.csv"))
    # 같은 파일(동일 해시) 중복 제거
    seen, uniq = set(), []
    for p in out:
        h = sha1(p)
        if h not in seen:
            seen.add(h)
            uniq.append(p)
    return uniq


def norm_ym(s: pd.Series) -> pd.Series:
    """'2018-01-01' / '2020-05' / '21-Apr' / '202101' → 'YYYY-MM'."""
    s = s.astype(str).str.strip()
    out = pd.Series(pd.NA, index=s.index, dtype="object")
    m1 = s.str.match(r"^\d{4}-\d{2}")
    out[m1] = s[m1].str.slice(0, 7)
    m2 = s.str.match(r"^\d{6}$")
    out[m2] = s[m2].str.slice(0, 4) + "-" + s[m2].str.slice(4, 6)
    m3 = s.str.match(r"^\d{2}-[A-Za-z]{3}$")
    if m3.any():
        out[m3] = pd.to_datetime(s[m3], format="%y-%b", errors="coerce").dt.strftime("%Y-%m")
    return out


# ----------------------------------------------------------------------------------------------
def prep_stores(raw_dirs, out: Path, prov: dict) -> pd.DataFrame | None:
    files = find(raw_dirs, "793")
    if not files:
        return None
    f = files[0]
    s = read_csv_any(f, dtype={"행정동코드": str, "법정동코드": str, "신우편번호": str})
    n0 = len(s)
    tok = s["지번주소"].astype(str).str.split()
    ri = tok.str.get(3)
    is_eupmyeon = s["법정동명"].astype(str).str.endswith(("읍", "면"))
    s["리"] = np.where(is_eupmyeon & ri.astype(str).str.endswith("리"), ri, None)
    keep = ["상호명", "상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "표준산업분류명", "시군구명",
            "행정동명", "법정동명", "리", "행정동코드", "법정동코드", "경도", "위도"]
    s = s[keep].copy()
    for c in ["상권업종중분류명", "상권업종소분류명", "표준산업분류명"]:
        s[c] = s[c].astype(str).str.strip()
    s = s.drop_duplicates().reset_index(drop=True)
    for c in ["상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "표준산업분류명", "시군구명", "행정동명",
              "법정동명", "리", "행정동코드", "법정동코드"]:
        s[c] = s[c].astype("category")
    s.to_parquet(out / "stores.parquet", **PQ)
    prov["793"] = dict(dataset_id="793", title=TITLES["793"], source_url=HUB_URL.format("793"),
                       original_files=[ORIGINAL_NAMES.get(f.stem.split("_")[1], f.name)], raw_sha1=[sha1(f)],
                       period="2025.09 기준", rows_raw=n0, rows_out=len(s),
                       output="stores.parquet",
                       processing=["cp949 → UTF-8 읽기", "시도명·주소·우편번호 컬럼 제거(용량 축소)",
                                   "지번주소에서 읍·면 하위 리(里) 이름 추출 → '리' 컬럼",
                                   "업종명 공백 정리, 완전 중복 행 제거", "BC 42업종 매핑은 실행 시 app/catalog/industries.py 규칙으로 계산"])
    return s


def prep_card_mashup(raw_dirs, out: Path, prov: dict):
    files = find(raw_dirs, "746")
    if not files:
        return
    frames, used = {}, []
    for f in files:
        head = read_csv_any(f, nrows=2000, dtype=str)
        months = sorted(norm_ym(head["년월"]).dropna().unique())
        has_cnt = "이용건수" in head.columns
        key = months[0][:4] if months else f.name
        if key in frames and not (has_cnt and not frames[key][1]):
            continue  # 같은 연도 파일 중복(2017년 파일 2종) — 이용건수 포함본 우선
        frames[key] = (f, has_cnt)
    parts_sales, parts_store, parts_demo = [], [], []
    rows_raw = 0
    for key in sorted(frames):
        f, has_cnt = frames[key]
        used.append(f)
        d = read_csv_any(f, dtype={"업종코드": str, "연령대": str})
        rows_raw += len(d)
        d["ym"] = norm_ym(d["년월"])
        d = d.rename(columns={"시도명": "city", "지역구분": "area_type", "읍면동명": "bjd", "업종코드": "biz_cd",
                              "업종명": "biz_nm", "업종명 대분류": "biz_group", "이용자 구분": "user_type",
                              "관광구분": "tour_type", "이용금액": "use_amt", "이용건수": "use_cnt",
                              "이용자수": "user_cnt", "매장수": "store_cnt"})
        for c in ("use_amt", "use_cnt", "user_cnt", "store_cnt"):
            if c in d:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        d["tour_type"] = d["tour_type"].fillna("NA")
        keys = ["ym", "city", "area_type", "bjd", "biz_cd", "biz_nm", "biz_group", "user_type", "tour_type"]
        agg = {"use_amt": "sum"}
        if has_cnt:
            agg.update(use_cnt="sum", user_cnt="sum")
        g = d.groupby(keys, dropna=False, observed=True).agg(agg).reset_index()
        if not has_cnt:
            g["use_cnt"] = np.nan
            g["user_cnt"] = np.nan
        parts_sales.append(g)
        st = d.groupby(["ym", "city", "biz_cd", "biz_nm"], observed=True)["store_cnt"].max().reset_index()
        parts_store.append(st)
        d["year"] = d["ym"].str.slice(0, 4)
        d["age"] = d["연령대"].fillna("NA").astype(str).str.replace(r"\.0$", "", regex=True)
        d["sex"] = d["성별"].fillna("알수없음")
        dg = d.groupby(["year", "city", "bjd", "biz_cd", "user_type", "tour_type", "age", "sex"], observed=True)[
            "use_amt"].sum().reset_index()
        dg["n_months"] = d["ym"].nunique()
        parts_demo.append(dg)
    sales = pd.concat(parts_sales, ignore_index=True)
    sales["use_amt"] = sales["use_amt"].astype("int64")
    for c in ("use_cnt", "user_cnt"):
        sales[c] = sales[c].astype("Float64").round().astype("Int64")
    for c in ("ym", "city", "area_type", "bjd", "biz_cd", "biz_nm", "biz_group", "user_type", "tour_type"):
        sales[c] = sales[c].astype("category")
    sales = sales.sort_values(["ym", "bjd", "biz_cd", "user_type", "tour_type"]).reset_index(drop=True)
    sales.to_parquet(out / "card_mashup.parquet", **PQ)
    stores = pd.concat(parts_store, ignore_index=True).sort_values(["ym", "city", "biz_cd"]).reset_index(drop=True)
    stores["store_cnt"] = stores["store_cnt"].astype("Int64")
    stores.to_parquet(out / "card_mashup_stores.parquet", **PQ)
    demo = pd.concat(parts_demo, ignore_index=True)
    demo = demo.groupby(["year", "city", "bjd", "biz_cd", "user_type", "tour_type", "age", "sex"], observed=True).agg(
        use_amt=("use_amt", "sum"), n_months=("n_months", "max")).reset_index()
    demo["use_amt"] = demo["use_amt"].astype("int64")
    for c in ("year", "city", "bjd", "biz_cd", "user_type", "tour_type", "age", "sex"):
        demo[c] = demo[c].astype("category")
    demo.sort_values(["year", "bjd", "biz_cd"]).reset_index(drop=True).to_parquet(out / "card_mashup_demo.parquet", **PQ)
    yms = sorted(sales["ym"].astype(str).unique())
    prov["746"] = dict(dataset_id="746", title=TITLES["746"], source_url=HUB_URL.format("746"),
                       original_files=[ORIGINAL_NAMES.get(f.stem.split("_")[1], f.name) for f in used],
                       raw_sha1=[sha1(f) for f in used], period=f"{yms[0].replace('-', '.')}~{yms[-1].replace('-', '.')}", rows_raw=int(rows_raw),
                       rows_out=int(len(sales)),
                       output=["card_mashup.parquet", "card_mashup_stores.parquet", "card_mashup_demo.parquet"],
                       processing=["연도별 원본(2017~2021.05) 6개 중 2017년 중복본은 이용자수·이용건수 포함본만 사용",
                                   "년월 → YYYY-MM 정규화, 컬럼명 영문 표준화(1-07/1-08 명세와 동일 이름)",
                                   "연령대·성별 차원을 합산: 월×시군구×도심구분×법정동×업종×이용자구분×관광구분 이용금액 합계",
                                   "이용건수·이용자수는 2017년만 존재(그 외 연도 결측)",
                                   "매장수(업종×시군구 값이 행마다 반복)는 월×시군구×업종 최댓값으로 별도 표 분리",
                                   "연령대·성별 구성은 연도×법정동×업종×이용자구분×관광구분×연령대×성별 연간 합계로 별도 표"
                                   "(card_mashup_demo, n_months=해당 연도 포함 개월 수)",
                                   "관광구분 결측 → 'NA'"])


def prep_card_pop(raw_dirs, out: Path, prov: dict):
    files = find(raw_dirs, "748")
    if not files:
        return
    ds, rows_raw = [], 0
    for f in files:
        d = read_csv_any(f)
        rows_raw += len(d)
        d["ym"] = norm_ym(d["년월"])
        ds.append(d)
    d = pd.concat(ds, ignore_index=True)
    d = d.rename(columns={"시도명": "city", "읍면동명": "emd", "업종명": "biz_nm", "성별": "sex", "이용자수": "user_cnt",
                          "이용금액": "use_amt", "거주인구": "resd_pop", "근무인구": "work_pop", "방문인구": "visit_pop"})
    d = d.drop_duplicates(subset=["ym", "city", "emd", "biz_nm", "sex"], keep="last")
    sales = d[["ym", "city", "emd", "biz_nm", "sex", "user_cnt", "use_amt"]].copy()
    for c in ("ym", "city", "emd", "biz_nm", "sex"):
        sales[c] = sales[c].astype("category")
    sales = sales.sort_values(["ym", "emd", "biz_nm", "sex"]).reset_index(drop=True)
    sales.to_parquet(out / "card_pop_sales.parquet", **PQ)
    pop = d[d["resd_pop"].notna()].drop_duplicates(subset=["ym", "city", "emd", "sex"])[
        ["ym", "city", "emd", "sex", "resd_pop", "work_pop", "visit_pop"]].copy()
    for c in ("resd_pop", "work_pop", "visit_pop"):
        pop[c] = pop[c].round(1)
    pop = pop.sort_values(["ym", "emd", "sex"]).reset_index(drop=True)
    pop.to_parquet(out / "card_pop_population.parquet", **PQ)
    yms = sorted(sales["ym"].astype(str).unique())
    prov["748"] = dict(dataset_id="748", title=TITLES["748"], source_url=HUB_URL.format("748"),
                       original_files=[ORIGINAL_NAMES.get(f.stem.split("_")[1], f.name) for f in files],
                       raw_sha1=[sha1(f) for f in files], period=f"{yms[0].replace('-', '.')}~{yms[-1].replace('-', '.')}", rows_raw=int(rows_raw),
                       rows_out=int(len(sales)), output=["card_pop_sales.parquet", "card_pop_population.parquet"],
                       processing=["연도별 원본 5개 병합, 년월 표기 3종('2018-01-01','2020-05','21-Apr') → YYYY-MM",
                                   "매출(월×행정동×업종×성별)과 유동인구(월×행정동×성별, 업종 행마다 반복되던 값)를 분리",
                                   "유동인구 값은 월간 시간대별 측정값 합(연인원) — 원본 그대로, 소수 1자리 반올림",
                                   "법인 행(성별=법인)은 인구값 없음"])


FEST_DROP_RE = re.compile(r"삭제|카테고리\s*잘못|^\W*$|^\.+$|미공개")
FEST_CANCEL_RE = re.compile(r"취소|미개최")


def prep_festivals(raw_dirs, out: Path, prov: dict):
    files = find(raw_dirs, "761")
    if not files:
        return
    f = files[0]
    d = read_csv_any(f, dtype=str)
    n0 = len(d)
    d = d.rename(columns={"콘텐츠명": "name", "시작일": "start", "종료일": "end", "시작시간": "start_time",
                          "종료시간": "end_time", "요금": "fee", "주관": "organizer", "주최": "host", "후원": "sponsor"})
    d["name"] = d["name"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    d["start"] = pd.to_datetime(d["start"], errors="coerce")
    d["end"] = pd.to_datetime(d["end"], errors="coerce")
    steps = {"원본": n0}
    d = d[~d["name"].str.contains(FEST_DROP_RE, na=True)]
    steps["삭제·오류 표기 행 제거"] = n0 - len(d)
    k = len(d)
    d = d[d["start"].notna() & d["end"].notna()]
    steps["날짜 결측 제거"] = k - len(d)
    k = len(d)
    d = d[d["end"] >= d["start"]]
    steps["종료일<시작일 제거"] = k - len(d)
    d["cancelled"] = d["name"].str.contains(FEST_CANCEL_RE)
    d["days"] = (d["end"] - d["start"]).dt.days + 1
    k = len(d)
    d = d.drop_duplicates(subset=["name", "start", "end"]).sort_values(["start", "name"]).reset_index(drop=True)
    steps["완전 중복 제거"] = k - len(d)
    d.to_parquet(out / "festivals.parquet", **PQ)
    prov["761"] = dict(dataset_id="761", title=TITLES["761"], source_url=HUB_URL.format("761"),
                       original_files=[ORIGINAL_NAMES.get(f.stem.split("_")[1], f.name)], raw_sha1=[sha1(f)],
                       period=f"{d['start'].min():%Y.%m.%d}~{d['end'].max():%Y.%m.%d}", rows_raw=n0, rows_out=len(d),
                       output="festivals.parquet",
                       processing=[f"{k_}: {v}" for k_, v in steps.items()] +
                                  ["이름에 '취소/미개최' 표기 → cancelled=True 로 표시(삭제하지 않음)",
                                   "장소(읍면동·좌표)는 원본에 없으므로 app/catalog/festivals.py 큐레이션 표로 결합"])


def prep_bus(raw_dirs, out: Path, prov: dict):
    files = find(raw_dirs, "512")
    if not files:
        return
    f = files[0]
    d = read_csv_any(f, dtype={"base_date": str})
    n0 = len(d)
    d["date"] = pd.to_datetime(d["base_date"], format="%Y%m%d", errors="coerce")
    d = d.rename(columns={"day_of_week": "dow", "base_hour": "time_band"})
    d = d[["date", "dow", "time_band", "user_type", "user_count"]].dropna(subset=["date"])
    d = d.drop_duplicates().sort_values(["date", "time_band", "user_type"]).reset_index(drop=True)
    for c in ("dow", "time_band", "user_type"):
        d[c] = d[c].astype("category")
    d.to_parquet(out / "bus_daily_timeband.parquet", **PQ)
    prov["512"] = dict(dataset_id="512", title=TITLES["512"], source_url=HUB_URL.format("512"),
                       original_files=[ORIGINAL_NAMES.get(f.stem.split("_")[1], f.name)], raw_sha1=[sha1(f)],
                       period=f"{d['date'].min():%Y.%m.%d}~{d['date'].max():%Y.%m.%d}", rows_raw=n0, rows_out=len(d),
                       output="bus_daily_timeband.parquet",
                       processing=["UTF-8(BOM) 읽기, base_date → 날짜형", "컬럼명 단순화(dow, time_band)", "완전 중복 제거"])


# ----------------------------------------------------------------------------------------------
def build_region_data(stores: pd.DataFrame) -> dict:
    """793 상가 위치로 법정동↔행정동 다수결 매핑·중심좌표·리(里) 중심좌표 산출."""
    s = stores.copy()
    for c in ("시군구명", "법정동명", "행정동명", "리", "법정동코드", "행정동코드"):
        s[c] = s[c].astype(object)
    s = s[s["경도"].between(126.0, 127.1) & s["위도"].between(33.0, 34.1)]
    bjd = []
    for (city, name), g in s.groupby(["시군구명", "법정동명"]):
        shares = g["행정동명"].value_counts(normalize=True)
        code8 = (g["법정동코드"].astype(str).str.slice(0, 8)).value_counts().index[0]
        bjd.append(dict(name=name, city=city, code8=code8, hjd=shares.index[0],
                        hjd_shares={k: round(float(v), 4) for k, v in shares.items()},
                        n_stores=int(len(g)), lon=round(float(g["경도"].median()), 6), lat=round(float(g["위도"].median()), 6)))
    hjd = []
    for (city, name), g in s.groupby(["시군구명", "행정동명"]):
        code = g["행정동코드"].astype(str).value_counts().index[0]
        bjds = g["법정동명"].value_counts()
        hjd.append(dict(name=name, city=city, code=code, n_stores=int(len(g)),
                        bjd=[k for k in bjds.index], bjd_counts={k: int(v) for k, v in bjds.items()},
                        lon=round(float(g["경도"].median()), 6),
                        lat=round(float(g["위도"].median()), 6)))
    ri = []
    r = s[s["리"].notna()]
    for (city, b, name), g in r.groupby(["시군구명", "법정동명", "리"]):
        if len(g) < 3:
            continue
        ri.append(dict(name=name, bjd=b, city=city, n_stores=int(len(g)),
                       lon=round(float(g["경도"].median()), 6), lat=round(float(g["위도"].median()), 6)))
    return {"source": "제주데이터허브 [793] 제주특별자치도 상가(상권) 정보 — 점포 위치 기반 추정",
            "method": "법정동→행정동은 점포 수 다수결(hjd), 비율(hjd_shares) 함께 제공. 중심좌표는 점포 경위도 중앙값.",
            "bjd": sorted(bjd, key=lambda x: (x["city"], x["name"])),
            "hjd": sorted(hjd, key=lambda x: x["code"]),
            "ri": sorted(ri, key=lambda x: (x["bjd"], x["name"]))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw-dir", type=Path, action="append", default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    raw_dirs = a.raw_dir or DEFAULT_RAW
    a.out.mkdir(parents=True, exist_ok=True)
    hf = ROOT.parent / "_research" / "hub_files.json"
    if hf.exists():
        for row in json.loads(hf.read_text(encoding="utf-8")):
            ORIGINAL_NAMES[str(row[1])] = row[5]
    prov: dict = {}
    stores = prep_stores(raw_dirs, a.out, prov)
    prep_card_mashup(raw_dirs, a.out, prov)
    prep_card_pop(raw_dirs, a.out, prov)
    prep_festivals(raw_dirs, a.out, prov)
    prep_bus(raw_dirs, a.out, prov)
    if stores is not None:
        REGION_OUT.write_text(json.dumps(build_region_data(stores), ensure_ascii=False, indent=1), encoding="utf-8")
    meta = {
        "note": "제주데이터허브(https://jejudatahub.net)에서 로그인 없이 내려받은 공개 원본을 전처리한 파일입니다. "
                "출처 표기: '제주데이터허브, <데이터명>(기간)'. 원본 파일은 저장소에 포함하지 않습니다.",
        "license": "공공데이터의 제공 및 이용 활성화에 관한 법률 및 제주데이터허브 이용약관에 따름",
        "generated_by": "tools/prepare_public.py",
        "datasets": prov,
    }
    (a.out / "PROVENANCE.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    total = sum(p.stat().st_size for p in a.out.glob("*") if p.is_file())
    for p in sorted(a.out.glob("*")):
        print(f"{p.name:32s} {p.stat().st_size / 1e6:7.2f} MB")
    print(f"total {total / 1e6:.2f} MB; datasets={sorted(prov)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
