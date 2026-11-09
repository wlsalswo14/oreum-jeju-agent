"""데이터 명세서 카탈로그 로드·조회·헤더 인식.

- `load_catalog()` → `Catalog` (spec_catalog.json; tools/build_catalog.py 가 생성)
- `Catalog.detect(columns)` : CSV 헤더(일부 컬럼만 선택·순서 변경 포함)로 데이터셋 인식
- `Catalog.describe_for_llm(ids)` : 공개 명세 정보(컬럼명·설명)만 담은 텍스트 — 데이터 값 없음
- `Catalog.search(text)` : 한글 키워드로 데이터셋 찾기

데이터셋 ID: 센터 데이터 '1-08' 형식, 허브 공개 데이터 'hub:746' 형식.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

CATALOG_PATH = Path(__file__).with_name("spec_catalog.json")

# 같은 이름이 여러 데이터셋에 흔한 컬럼(파티션 등)은 인식 가중치를 낮춘다.
_LOW_WEIGHT_COLS = {"pt_year", "pt_month"}
# v01/v02 형제 데이터셋의 제공 기간(YYYYMM). 시점 값으로 형제를 구분할 때 사용.
VALID_RANGE = {
    "1-07": (201701, 202506), "1-08": (202308, None), "1-11": (201701, 202506), "1-12": (202308, None),
    "1-13": (201701, 202506), "1-14": (202308, None), "1-15": (201701, 202506), "1-16": (202308, None),
    "4-10": (201701, 202504), "4-11": (202505, None), "4-12": (201801, 202503), "4-13": (202504, None),
    "4-14": (201701, 202503), "4-15": (202504, None),
}

# 헤더가 완전히 같은 데이터셋 쌍을 값으로 구분하기 위한 힌트 (컬럼 → 값에 포함될 문자열)
VALUE_HINTS = {
    "1-01": {"user_type": ["내국인관광객", "제주도민"]}, "1-02": {"user_type": ["내국인"]},
    "1-03": {"user_type": ["내국인"]}, "1-04": {"user_type": ["중국"]}, "1-05": {"user_type": ["중국"]},
    "1-06": {"user_type": ["중국"]}, "2-01": {"sale_buy_code": ["구매"]}, "2-02": {"sale_buy_code": ["판매"]},
    "5-05": {"device_no": ["W6X"]}, "5-06": {"device_no": ["W6Y"]},
}

_SYNONYMS = {
    "매출": ["카드", "포스", "이용금액", "매출"], "소비": ["카드", "이용금액"], "결제": ["카드"],
    "상권": ["카드", "매장", "포스"], "창업": ["카드", "매장", "포스"], "카페": ["카드", "업종"], "커피": ["카드", "업종"],
    "식당": ["외식업", "포스", "카드"], "음식점": ["외식업", "포스", "카드"], "메뉴": ["메뉴"],
    "유동인구": ["유동인구"], "인구": ["유동인구"], "방문객": ["유동인구", "방문"], "관광객": ["관광", "유동인구", "카드"],
    "외국인": ["외국인", "중국인"], "버스": ["버스"], "정류장": ["정류소"], "정류소": ["정류소"], "노선": ["노선"],
    "대중교통": ["버스"], "교통량": ["교통량"], "도로": ["도로"], "날씨": ["날씨"], "기온": ["날씨"], "강수": ["날씨"],
    "비": ["날씨"], "와이파이": ["와이파이"], "기업": ["기업"], "재무": ["재무"], "쓰레기": ["쓰레기", "폐기물", "종량제"],
    "요일": ["요일"], "시간대": ["시간대"], "시간": ["시간대"], "매장수": ["매장"], "점포": ["매장"], "경쟁": ["매장"],
    "축제": ["유동인구", "카드", "버스"], "행사": ["유동인구", "카드", "버스"], "골프": ["골프장"], "지역화폐": ["탐나는전"],
    "온라인": ["온라인"],
}


def normalize_header(col: object) -> str:
    """CSV 헤더 정규화: BOM·따옴표·공백 제거, 영문 소문자."""
    s = str(col).replace("﻿", "").strip().strip('"').strip("'").strip()
    return s.lower() if s.isascii() else s


def normalize_id(dataset_id: str) -> str:
    s = str(dataset_id).strip()
    m = re.fullmatch(r"(\d)\s*-\s*(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(?:hub[:_ ]?|h)?(\d{3,4})", s, flags=re.I)
    if m:
        return f"hub:{m.group(1)}"
    return s


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    kor: str = ""
    type: str = ""
    example: str = ""
    note: str = ""


@dataclass
class DatasetSpec:
    id: str
    name: str
    category: str = ""
    exportable: bool = False
    provider: str = ""
    description: str = ""
    period: str = ""
    update: str = ""
    grain: str = "static"
    columns: list[ColumnSpec] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    rate_measures: list[str] = field(default_factory=list)
    measure_agg: dict[str, str] = field(default_factory=dict)
    dims: list[str] = field(default_factory=list)
    id_cols: list[str] = field(default_factory=list)
    time_col: str | None = None
    partition_cols: list[str] = field(default_factory=list)
    optional_cols: list[str] = field(default_factory=list)
    region_cols: dict[str, str] = field(default_factory=dict)
    family: str | None = None
    version_tag: str | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    key_cols: list[str] = field(default_factory=list)
    must_cols: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = "center"
    source_url: str = ""
    table_kind: str = "aggregate"
    verified: bool = False
    registered: str = ""

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def is_hub(self) -> bool:
        return self.source == "hub"

    def col(self, name: str) -> ColumnSpec | None:
        n = normalize_header(name)
        return next((c for c in self.columns if normalize_header(c.name) == n), None)

    def kor(self, name: str) -> str:
        c = self.col(name)
        return c.kor if c else name

    def numeric_cols(self) -> list[str]:
        """수치형으로 변환할 컬럼(측정값 + 명세서상 수치형 비식별 컬럼)."""
        out = list(dict.fromkeys(self.measures + self.rate_measures))
        for c in self.columns:
            if c.name in out or c.name in self.dims or c.name in self.id_cols:
                continue
            if c.type.startswith(("INT", "BIGINT", "DOUBLE", "DECIMAL")) or c.name in ("longitude", "latitude"):
                out.append(c.name)
        return out

    def citation(self, period: str = "") -> str:
        org = "제주데이터허브" if self.is_hub else "제주빅데이터센터"
        return f"{org}, {self.name}({period})" if period else f"{org}, {self.name}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    dataset_id: str
    name: str
    score: float
    precision: float
    recall: float
    matched: list[str]
    missing_cols: list[str]
    extra_cols: list[str]
    raw_score: float = 0.0


@dataclass
class DetectResult:
    dataset_id: str | None
    score: float
    missing_cols: list[str] = field(default_factory=list)
    extra_cols: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    matched_cols: list[str] = field(default_factory=list)
    ambiguous: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        return self.dataset_id is not None

    @property
    def name(self) -> str:
        return self.candidates[0].name if self.candidates and self.dataset_id else ""


def _spec_from_dict(d: dict) -> DatasetSpec:
    cols = [ColumnSpec(**{k: str(c.get(k, "") or "") for k in ("name", "kor", "type", "example", "note")})
            for c in d.get("columns", [])]
    kw = {k: d.get(k) for k in DatasetSpec.__dataclass_fields__ if k in d and k != "columns"}
    for k in ("measures", "rate_measures", "dims", "id_cols", "partition_cols", "optional_cols", "key_cols",
              "must_cols", "notes"):
        kw[k] = list(kw.get(k) or [])
    kw["measure_agg"] = dict(kw.get("measure_agg") or {})
    kw["region_cols"] = dict(kw.get("region_cols") or {})
    kw["source_url"] = kw.get("source_url") or ""
    return DatasetSpec(columns=cols, **kw)


class Catalog:
    def __init__(self, raw: dict):
        self.raw = raw
        self.version: str = raw.get("version", "")
        self.datasets: dict[str, DatasetSpec] = {k: _spec_from_dict(v) for k, v in raw.get("datasets", {}).items()}
        self.hub: dict[str, DatasetSpec] = {k: _spec_from_dict(v) for k, v in raw.get("hub_datasets", {}).items()}
        self.reference: dict = raw.get("reference", {})
        self._all = {**self.datasets, **self.hub}
        # 컬럼 IDF 가중치
        df: dict[str, int] = {}
        for ds in self._all.values():
            for c in {normalize_header(n) for n in ds.column_names}:
                df[c] = df.get(c, 0) + 1
        n = max(1, len(self._all))
        self._w = {c: (0.2 if c in _LOW_WEIGHT_COLS else 1.0 + math.log(n / k)) for c, k in df.items()}

    # ------------------------------------------------------------------ 조회
    def get(self, dataset_id: str) -> DatasetSpec:
        key = normalize_id(dataset_id)
        if key in self._all:
            return self._all[key]
        raise KeyError(f"카탈로그에 없는 데이터셋: {dataset_id}")

    def has(self, dataset_id: str) -> bool:
        return normalize_id(dataset_id) in self._all

    def all(self, include_hub: bool = False) -> list[DatasetSpec]:
        return list(self._all.values()) if include_hub else list(self.datasets.values())

    def exportable(self, dataset_id: str) -> bool:
        """원본 반출 가능 여부(명세서 '반출' 항목). 허브 공개 데이터는 True."""
        try:
            return bool(self.get(dataset_id).exportable)
        except KeyError:
            return False

    def latest(self, dataset_id: str) -> str:
        ds = self.get(dataset_id)
        seen = {ds.id}
        while ds.superseded_by and ds.superseded_by not in seen:
            seen.add(ds.superseded_by)
            ds = self.get(ds.superseded_by)
        return ds.id

    def biz_name(self, code: str) -> str:
        return next((b["name"] for b in self.reference.get("biz42", []) if b["code"] == code), "")

    def pos_menu_codes(self) -> list[dict]:
        p = CATALOG_PATH.with_name(self.reference.get("pos_menu_codes_file", "pos_menu_codes.json"))
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    # ------------------------------------------------------------------ 헤더 인식
    def detect(self, columns: Iterable[str], values: Mapping[str, Iterable] | None = None,
               include_hub: bool = True, top_k: int = 5) -> DetectResult:
        """헤더(컬럼 목록)로 데이터셋을 인식한다.

        기본점수 = 0.8×가중정밀도(입력 컬럼 중 명세 컬럼 비율) + 0.2×가중재현율(명세 컬럼 중 입력에 있는 비율)
        가중치는 컬럼 희소도(IDF) — week·time·resd_pop_cnt·dstrct_se 같은 식별 컬럼이 크게 반영된다.
        보정: 핵심 식별 컬럼(key_cols) 포함 +0.03×비율, 정체성 컬럼(must_cols, 예: 요일별의 week) 누락 −0.06/개,
        핵심(수기 검증) 데이터셋 +0.005, 최신판(v02) +0.01(형제를 컬럼으로 구분할 수 없을 때 v02 우선).
        `values`(컬럼→표본 값)를 주면 v01/v02 형제를 시점·값으로 추가 구분한다.
        반환 score 는 0~1로 자른 값(정렬은 보정 전체 점수 기준).
        """
        cols_in = [normalize_header(c) for c in columns if str(c).strip() != ""]
        cols_in = [c for c in cols_in if not re.fullmatch(r"unnamed: ?\d+", c)]
        I = list(dict.fromkeys(cols_in))
        if not I:
            return DetectResult(None, 0.0, reason="빈 헤더")
        Iset = set(I)
        w = lambda c: self._w.get(c, 1.0 + math.log(max(1, len(self._all))))  # noqa: E731 - 미지 컬럼은 최대 가중
        wI = sum(w(c) for c in I)
        vals = {normalize_header(k): [str(x) for x in v] for k, v in (values or {}).items()}
        cands: list[Candidate] = []
        pool = self._all.values() if include_hub else self.datasets.values()
        for ds in pool:
            S = [normalize_header(c) for c in ds.column_names]
            if not S:
                continue
            Sset = set(S)
            inter = Iset & Sset
            if not inter:
                continue
            optional = {normalize_header(c) for c in ds.optional_cols}
            req = [c for c in S if c not in optional]
            wS = sum(w(c) for c in req)
            prec = sum(w(c) for c in inter) / wI
            rec = (sum(w(c) for c in inter if c not in optional) / wS) if wS else 1.0
            score = 0.8 * prec + 0.2 * rec
            if ds.key_cols:
                kc = [normalize_header(k) for k in ds.key_cols]
                score += 0.03 * sum(k in Iset for k in kc) / len(kc)
            score -= 0.06 * sum(normalize_header(m) not in Iset for m in ds.must_cols)
            score += 0.005 if ds.verified and not ds.is_hub else 0.0
            score += 0.01 if ds.version_tag == "v02" else 0.0
            score += self._value_adjust(ds, vals)
            missing = [c for c in ds.column_names if normalize_header(c) not in Iset and normalize_header(c) not in optional]
            extra = [c for c in I if c not in Sset]
            cands.append(Candidate(ds.id, ds.name, round(max(0.0, min(score, 1.0)), 4), round(prec, 4), round(rec, 4),
                                   [c for c in I if c in Sset], missing, extra, round(score, 5)))
        if not cands:
            return DetectResult(None, 0.0, extra_cols=I, reason="명세서 컬럼과 일치하는 항목 없음")
        cands.sort(key=lambda c: (-c.raw_score, -c.precision, c.dataset_id))
        best = cands[0]
        best = self._prefer_latest(best, cands, Iset, vals)
        if best is not cands[0]:
            cands.remove(best)
            cands.insert(0, best)
        top = cands[:top_k]
        if best.precision < 0.6 or best.raw_score < 0.55:
            return DetectResult(None, best.score, candidates=top, extra_cols=best.extra_cols,
                                reason="일치도가 낮아 데이터셋을 확정하지 못함")
        ambiguous = len(cands) > 1 and (best.raw_score - cands[1].raw_score) < 0.015 \
            and cands[1].precision >= best.precision - 1e-9
        reason = "완전 일치" if not best.missing_cols and not best.extra_cols else "부분 일치(선택 컬럼만 포함되었거나 추가 컬럼 있음)"
        if ambiguous:
            reason += f" — {cands[1].dataset_id}와(과) 구분이 어려움(구분 컬럼 누락)"
        return DetectResult(best.dataset_id, best.score, best.missing_cols, best.extra_cols, top, best.matched,
                            ambiguous, reason)

    def _in_range_share(self, ds_id: str, ds: DatasetSpec, vals: dict[str, list[str]]) -> float | None:
        rng = VALID_RANGE.get(ds_id)
        tcol = normalize_header(ds.time_col) if ds.time_col else None
        if not rng or not tcol or tcol not in vals:
            return None
        yms = [int(d[:6]) for d in (re.sub(r"\D", "", v) for v in vals[tcol]) if len(d) >= 6]
        if not yms:
            return None
        lo, hi = rng
        return sum((lo is None or y >= lo) and (hi is None or y <= hi) for y in yms) / len(yms)

    def _prefer_latest(self, best: Candidate, cands: list[Candidate], Iset: set[str],
                       vals: dict[str, list[str]]) -> Candidate:
        """선택 컬럼만 담긴(부분) 헤더라서 v01/v02 를 컬럼으로 구분할 수 없으면 v02(최신판)를 우선한다.

        단, v01 에만 있는 컬럼이 입력에 있거나, 시점 값이 v01 제공 기간에만 속하면 v01 유지.
        (공식 답변: 겹치는 기간의 v01·v02 값은 동일, v02 사용 권장)
        """
        ds = self._all.get(best.dataset_id)
        if not ds or ds.version_tag != "v01" or not ds.superseded_by or not best.missing_cols:
            return best
        sib = next((c for c in cands if c.dataset_id == ds.superseded_by), None)
        if sib is None or best.raw_score - sib.raw_score > 0.03 or sib.precision < best.precision - 1e-9:
            return best
        v2 = self._all[sib.dataset_id]
        v1_only = {normalize_header(c) for c in ds.column_names} - {normalize_header(c) for c in v2.column_names}
        if v1_only & Iset:
            return best
        s1 = self._in_range_share(ds.id, ds, vals)
        s2 = self._in_range_share(v2.id, v2, vals)
        if s1 is not None and s2 is not None:
            return best if s1 > s2 else sib
        r1, r2 = VALID_RANGE.get(ds.id), VALID_RANGE.get(v2.id)
        if r1 and r2 and r1[1] is not None and r2[0] is not None and r1[1] < r2[0]:
            return best  # 제공 기간이 겹치지 않는 형제(예: 4-10/4-11)는 시점 값 없이 단정하지 않음
        return sib

    def _value_adjust(self, ds: DatasetSpec, vals: dict[str, list[str]]) -> float:
        if not vals:
            return 0.0
        adj = 0.0
        for col, needles in VALUE_HINTS.get(ds.id, {}).items():
            vv = vals.get(col)
            if vv:
                hit = any(any(n in v for n in needles) for v in vv)
                adj += 0.03 if hit else -0.01
        # 시점으로 v01/v02 형제 구분
        inside = self._in_range_share(ds.id, ds, vals)
        if inside is not None:
            adj += 0.02 * inside - 0.03 * (1 - inside)
        # area_type: v01 요일/시간대(6개 권역) vs 읍면동(도심/읍면)
        if "area_type" in vals and "area_type" in [normalize_header(c) for c in ds.column_names]:
            av = {v.strip() for v in vals["area_type"] if v and v.strip().upper() != "NA"}
            if av:
                district_like = any(("부" in v) or v in ("제주시", "서귀포시") for v in av)
                kind = ds.region_cols.get("area_type", "")
                if district_like:
                    adj += 0.02 if "권역" in kind else -0.02
                elif av <= {"도심", "읍면", "전체", "도시"}:
                    adj += 0.02 if kind == "도심구분" else -0.02
        # time: 시간대 구간(오전…) vs 시각(1~24)
        if "time" in vals and "time" in [normalize_header(c) for c in ds.column_names]:
            tv = [v.strip() for v in vals["time"] if v]
            if tv:
                band = any(v in ("오전", "점심", "오후", "저녁", "밤") for v in tv)
                note = (ds.col("time").note if ds.col("time") else "") + (ds.col("time").example if ds.col("time") else "")
                ds_band = "오전" in note
                adj += 0.02 if band == ds_band else -0.02
        return adj

    # ------------------------------------------------------------------ LLM 설명
    def describe_for_llm(self, dataset_ids: Iterable[str], include_columns: bool = True,
                         include_notes: bool = True) -> str:
        """공개 명세 정보만 텍스트로(컬럼명·한글명·형식·비고, 설명·기간·반출 등급). 데이터 값·예시값은 넣지 않는다."""
        blocks = []
        for i in dataset_ids:
            try:
                ds = self.get(i)
            except KeyError:
                continue
            exp = ("원본 반출 가능" if ds.exportable else
                   "원본 반출 불가(가공·집계 결과만, 단순집계는 10 미만 수치 불포함 시 가능)")
            lines = [f"[{ds.id}] {ds.name} — 분류: {ds.category} / 제공: {ds.provider} / {exp}"]
            if ds.description:
                lines.append(f"설명: {ds.description}")
            meta = [f"단위(grain): {ds.grain}"]
            if ds.period:
                meta.append(f"제공기간: {ds.period}")
            if ds.time_col:
                meta.append(f"시점 컬럼: {ds.time_col}")
            lines.append(" / ".join(meta))
            if include_columns:
                lines.append("컬럼:")
                for c in ds.columns:
                    note = f" — {c.note}" if c.note else ""
                    lines.append(f"- {c.name} ({c.kor}, {c.type or '-'}){note}")
            if ds.measures:
                lines.append("합산 가능 측정값: " + ", ".join(ds.measures))
            if ds.rate_measures:
                lines.append("합산 불가 측정값(평균·비율): " + ", ".join(ds.rate_measures))
            if include_notes and ds.notes:
                lines.append("주의: " + " / ".join(ds.notes))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ 검색
    def search(self, text: str, limit: int = 8, include_hub: bool = False,
               include_superseded: bool = False) -> list[DatasetSpec]:
        toks = [t for t in re.findall(r"[가-힣A-Za-z0-9]+", text or "") if len(t) >= 1]
        terms: dict[str, float] = {}
        for t in toks:
            terms[t] = max(terms.get(t, 0), 1.0)
            for key, syns in _SYNONYMS.items():
                if key in t:
                    for s in syns:
                        terms[s] = max(terms.get(s, 0), 0.7)
        if not terms:
            return []
        scored = []
        for ds in self.all(include_hub=include_hub):
            if ds.superseded_by and not include_superseded:
                continue
            name = ds.name
            desc = ds.description
            kor = " ".join(c.kor for c in ds.columns)
            eng = " ".join(c.name for c in ds.columns).lower()
            s = 0.0
            for t, wt in terms.items():
                if len(t) < 2 and not t.isdigit():
                    continue
                if t in name:
                    s += 3 * wt
                if t in desc:
                    s += 1 * wt
                if t in kor:
                    s += 0.5 * wt
                if t.isascii() and t.lower() in eng:
                    s += 0.5 * wt
                if t == ds.id:
                    s += 10
            if s > 0:
                if ds.version_tag == "v02":
                    s += 0.3
                scored.append((s, ds))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [d for _, d in scored[:limit]]


@lru_cache(maxsize=4)
def _load(path: str) -> Catalog:
    return Catalog(json.loads(Path(path).read_text(encoding="utf-8")))


def load_catalog(path: str | Path | None = None) -> Catalog:
    return _load(str(path or CATALOG_PATH))
