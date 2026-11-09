"""반출 안전장치(Export Guard).

근거: 「제주빅데이터센터 데이터 반출 정책」(2026.09.01 개정) 및 대회 게시판 공식 답변.

- 반출 '가능' 데이터(교통·환경·와이파이 등)만으로 만든 표 → 원본 포함 반출 가능 → 통과.
- 반출 '불가' 데이터(카드·POS·유동인구·기업)로 만든 표:
    · 원본/발췌(원본 단위 그대로) → 반출 불가(차단)
    · 단순집계(합계·평균 등) → 10 미만 수치가 없을 때만 가능
        처리방법 1: 범주처리(상위 단위로 재집계)  ← 게시판 답변: 상위 단위 재집계로 모든 셀을 10 이상으로 만들면 범주처리로 인정
        처리방법 2: 삭제처리(해당 셀 비공개)    ← 역산 가능하면 반출 불가 → 보완 억제(complementary suppression) 수행
        처리방법 3: 비식별(같은 값 처리 등)
    · 응용집계(차이·증감률·비율·순위·지수·모델링 결과) → 수치 포함 반출 가능
      단, 삭제된 단순집계 셀에서 계산된 응용집계 값은 함께 비공개(역산 방지).

이 모듈은 분석 엔진이 만든 모든 집계표(AggTable)를 검사·교정하고, 그 결과만
차트·보고서·LLM 입력에 사용되도록 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal

import numpy as np
import pandas as pd

from ..config import settings

ValueKind = Literal["simple", "applied"]

POLICY_REF = "제주빅데이터센터 데이터 반출 정책(2026.09.01 개정)"

# 반출 '가능'(원본 포함) 데이터셋 접두사/아이디. 카탈로그가 있으면 카탈로그 값을 우선 사용.
_EXPORTABLE_FALLBACK = {
    "3-01", "3-02", "3-03", "3-04", "3-05", "3-06", "3-07", "3-08", "3-09", "3-10",
    "4-01", "4-02", "4-03", "4-04", "4-05", "4-06",
    "5-01", "5-02", "5-03", "5-04", "5-05", "5-06",
}


_EXPORT_CACHE: dict[str, bool] = {}


def is_exportable_source(src: str) -> bool:
    """출처가 '원본 포함 반출 가능'인지. hub:* (제주데이터허브 공개 데이터)는 공개 자료이므로 가능."""
    if src in _EXPORT_CACHE:
        return _EXPORT_CACHE[src]
    if src.startswith("hub:") or src.startswith("public:"):
        ok = True
    else:
        try:  # 카탈로그(데이터 계층)가 준비되어 있으면 그 판단을 따른다.
            from ..catalog.catalog import load_catalog

            ok = bool(load_catalog().exportable(src))
        except Exception:
            ok = src in _EXPORTABLE_FALLBACK
    _EXPORT_CACHE[src] = ok
    return ok


@dataclass
class Rollup:
    """범주처리 단계: dim 컬럼 값을 상위 범주로 묶는다."""

    dim: str
    mapping: dict | Callable[[object], object]
    label: str  # 예: "법정동 → 7개 권역", "월 → 분기"

    def apply(self, s: pd.Series) -> pd.Series:
        if callable(self.mapping):
            return s.map(self.mapping)
        return s.map(lambda v: self.mapping.get(v, v))


@dataclass
class AggTable:
    key: str
    title: str
    df: pd.DataFrame
    dims: list[str]
    values: list[str]
    kind: dict[str, ValueKind]
    sources: list[str]
    unit: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    note: str = ""
    # 단순집계 셀의 기반 건수(예: 금액 셀의 이용건수). df와 같은 행 순서, 컬럼=values 중 일부
    support: pd.DataFrame | None = None
    # 응용집계 컬럼이 어떤 단순집계 컬럼에서 계산되었는지(삭제 시 연쇄 비공개)
    derived: dict[str, list[str]] = field(default_factory=dict)
    # 이 표의 합계가 함께 공개되는 묶음 차원(보완 억제 대상). 예: ["week"] = 요일 합계가 공개됨
    margins: list[str] = field(default_factory=list)
    rollups: list[Rollup] = field(default_factory=list)
    # 범주처리 후 응용집계를 다시 계산하는 함수(단순집계 컬럼은 합산됨)
    recompute: Callable[[pd.DataFrame], pd.DataFrame] | None = None
    # 원본 단위 그대로의 행(발췌)인지
    is_extract: bool = False
    # 금액처럼 '10 미만'보다 기반 건수(support)가 중요한 컬럼
    support_only: list[str] = field(default_factory=list)
    # 컬럼별 출처(지정 시 sources 대신 사용). 예: 상가 수는 허브 공개데이터, 매출지수는 카드 데이터
    col_sources: dict[str, list[str]] = field(default_factory=dict)
    # 범주처리 시 컬럼별 집계 방법(기본: 단순집계=sum, 응용집계=재계산 필요). 비중(%)은 'sum' 가능
    rollup_agg: dict[str, str] = field(default_factory=dict)

    def sources_of(self, col: str) -> list[str]:
        return self.col_sources.get(col, self.sources)


@dataclass
class GuardResult:
    table: AggTable                 # 교정된 표(비공개 셀 = NaN)
    status: Literal["pass", "fixed", "blocked"]
    basis: str                      # 판단 근거 요약
    actions: list[str]              # 수행한 처리 내역(반출 신청서에 그대로 사용)
    method: str                     # 단순집계/응용집계/혼합/원본(반출가능)
    n_cells: int = 0
    n_suppressed: int = 0
    suppressed_mask: pd.DataFrame | None = None
    original_title: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("pass", "fixed")

    def display_df(self, max_rows: int | None = None) -> pd.DataFrame:
        """사람이 읽는 형태(비공개 셀 표시, 천단위 콤마)."""
        t = self.table
        df = t.df.copy()
        if max_rows:
            df = df.head(max_rows)
        mask = self.suppressed_mask
        for c in t.values:
            col = df[c]
            out = []
            for i, v in zip(col.index, col.values):
                hidden = mask is not None and c in mask.columns and i in mask.index and bool(mask.at[i, c])
                if hidden:
                    out.append("비공개(<10)")
                elif v is None or (isinstance(v, float) and np.isnan(v)):
                    out.append("-")
                else:
                    out.append(_fmt(v, t.unit.get(c, "")))
            df[c] = out
        return df.rename(columns={**t.labels})

    def summary(self) -> dict:
        return {
            "key": self.table.key,
            "title": self.table.title,
            "status": self.status,
            "method": self.method,
            "basis": self.basis,
            "actions": self.actions,
            "sources": self.table.sources,
            "n_cells": self.n_cells,
            "n_suppressed": self.n_suppressed,
        }


def _fmt(v, unit: str) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if unit in ("%", "%p"):
        return f"{f:,.1f}{unit}"
    if unit == "배":
        return f"{f:,.2f}배"
    if unit == "점":
        return f"{f:,.1f}점"
    if abs(f) >= 100 or float(f).is_integer():
        return f"{f:,.0f}{unit}"
    return f"{f:,.1f}{unit}"


# --------------------------------------------------------------------------------------
# 핵심 검사
# --------------------------------------------------------------------------------------
def check(table: AggTable, min_cell: int | None = None) -> GuardResult:
    min_cell = settings.min_cell if min_cell is None else min_cell
    t = table
    srcs = list(dict.fromkeys(t.sources))
    restricted = [s for s in srcs if not is_exportable_source(s)]
    kinds = {t.kind.get(c, "simple") for c in t.values}
    method = _method_label(kinds, restricted)

    if not restricted:
        return GuardResult(
            table=t, status="pass", method=method,
            basis=f"출처({', '.join(srcs)})가 모두 원본 포함 반출 가능 데이터임 — {POLICY_REF}",
            actions=["추가 처리 불필요(반출 가능 데이터)"],
            n_cells=_n_cells(t), original_title=t.title,
        )

    if t.is_extract:
        return GuardResult(
            table=t, status="blocked", method="원본/발췌",
            basis=f"반출 불가 데이터({', '.join(restricted)})의 원본·발췌는 반출 불가 — {POLICY_REF}",
            actions=["차단: 원본 단위 그대로의 행은 보고서·LLM에 사용하지 않음"],
            n_cells=_n_cells(t), original_title=t.title,
        )

    simple_cols = [
        c for c in t.values
        if any(not is_exportable_source(s) for s in t.sources_of(c))
        and (
            t.kind.get(c, "simple") == "simple"
            # 응용집계라도 기반 건수(support)가 주어지면 '기반 건수 10 미만' 비율은 보수적으로 비공개
            or (t.support is not None and c in t.support.columns)
        )
    ]
    if not simple_cols:
        return GuardResult(
            table=t, status="pass", method=method,
            basis=f"응용집계(비율·증감률·순위·지수 등)는 수치 포함 반출 가능 — {POLICY_REF}",
            actions=["추가 처리 불필요(응용집계)"],
            n_cells=_n_cells(t), original_title=t.title,
        )

    # 단계별 범주처리 후보 생성: L0(원본 집계), L1(rollup1), L2(rollup1+2) ...
    levels: list[tuple[AggTable, list[str]]] = [(t, [])]
    cur, labels = t, []
    for r in t.rollups:
        cur = _apply_rollup(cur, r)
        labels = labels + [r.label]
        levels.append((cur, labels))

    best = None
    for lvl_table, lvl_labels in levels:
        mask = _sensitive_mask(lvl_table, simple_cols, min_cell)
        n_sens = int(mask.values.sum())
        n = max(1, len(lvl_table.df) * len(simple_cols))
        if n_sens == 0:
            actions = (
                [f"범주처리: {lab}" for lab in lvl_labels]
                + [f"모든 단순집계 셀이 {min_cell} 이상임을 확인"]
            )
            status = "fixed" if lvl_labels else "pass"
            basis = (
                f"단순집계 셀이 모두 {min_cell} 이상(기반 건수 포함) — {POLICY_REF} '10 미만 단순집계 반출 불가' 충족"
            )
            return GuardResult(
                table=lvl_table, status=status, method=method, basis=basis, actions=actions,
                n_cells=_n_cells(lvl_table), n_suppressed=0, original_title=t.title,
            )
        ratio = n_sens / n
        if best is None or ratio < best[0] - 1e-9:
            best = (ratio, lvl_table, lvl_labels, mask)

    # 삭제처리 + 보완 억제
    _, lvl_table, lvl_labels, primary = best
    full_mask, n_secondary = _complementary_suppression(lvl_table, simple_cols, primary)
    # 응용집계 연쇄 비공개
    for a, bases in lvl_table.derived.items():
        if a in lvl_table.df.columns:
            dep = np.zeros(len(lvl_table.df), dtype=bool)
            for b in bases:
                if b in full_mask.columns:
                    dep |= full_mask[b].values
            full_mask[a] = dep
    out_df = lvl_table.df.copy()
    for c in full_mask.columns:
        out_df.loc[full_mask[c].values, c] = np.nan
    out_t = _replace_df(lvl_table, out_df)
    n_primary = int(primary.values.sum())
    n_supp = int(full_mask.values.sum())
    total_cells = _n_cells(out_t)
    actions = [f"범주처리: {lab}" for lab in lvl_labels]
    actions.append(f"삭제처리: {min_cell} 미만(또는 기반 건수 {min_cell} 미만) 셀 {n_primary}개 비공개")
    if n_secondary:
        actions.append(f"역산 방지 보완 억제: 합계로 역산 가능한 셀 {n_secondary}개 추가 비공개")
    if lvl_table.derived:
        actions.append("비공개 셀에서 계산된 비율·증감률도 함께 비공개")
    status: Literal["fixed", "blocked"] = "fixed"
    basis = f"10 미만 셀 삭제처리 후 반출 기준 충족 — {POLICY_REF}"
    if total_cells and n_supp / total_cells > 0.5:
        status = "blocked"
        basis = "비공개 셀이 절반을 넘어 정보가치가 낮음 — 상위 단위 또는 비율(응용집계) 형태로 다시 분석 권장"
        actions.append("차단: 보고서·LLM 입력에서 제외")
    return GuardResult(
        table=out_t, status=status, method=method, basis=basis, actions=actions,
        n_cells=total_cells, n_suppressed=n_supp, suppressed_mask=full_mask, original_title=t.title,
    )


def check_all(tables: Iterable[AggTable], min_cell: int | None = None) -> list[GuardResult]:
    return [check(t, min_cell) for t in tables]


# --------------------------------------------------------------------------------------
# 내부 함수
# --------------------------------------------------------------------------------------
def _method_label(kinds: set, restricted: list[str]) -> str:
    if not restricted:
        return "원본·집계(반출 가능 데이터)"
    if kinds == {"applied"}:
        return "응용집계"
    if kinds == {"simple"}:
        return "단순집계"
    return "단순집계+응용집계"


def _n_cells(t: AggTable) -> int:
    return int(len(t.df) * len(t.values))


def _sensitive_mask(t: AggTable, simple_cols: list[str], min_cell: int) -> pd.DataFrame:
    df = t.df
    mask = pd.DataFrame(False, index=df.index, columns=simple_cols)
    for c in simple_cols:
        v = pd.to_numeric(df[c], errors="coerce")
        if c not in t.support_only and t.kind.get(c, "simple") == "simple":
            m = v.notna() & (v.abs() < min_cell)
        else:
            m = pd.Series(False, index=df.index)
        if t.support is not None and c in t.support.columns:
            sup = pd.to_numeric(t.support[c], errors="coerce").reindex(df.index)
            m = m | (sup.notna() & (sup < min_cell) & v.notna())
        mask[c] = m.values
    return mask


def _apply_rollup(t: AggTable, r: Rollup) -> AggTable:
    if r.dim not in t.df.columns:
        return t
    df = t.df.copy()
    df[r.dim] = r.apply(df[r.dim])
    agg = {c: "sum" for c in t.values if t.kind.get(c, "simple") == "simple"}
    agg.update({c: how for c, how in t.rollup_agg.items() if c in t.values})
    if not agg:
        return t
    g = df.groupby(t.dims, dropna=False, sort=False)
    new = g.agg(agg).reset_index()
    sup = None
    if t.support is not None:
        s = t.support.copy()
        s[t.dims] = df[t.dims].values
        sup = s.groupby(t.dims, dropna=False, sort=False)[list(t.support.columns)].sum().reset_index(drop=True)
    if t.recompute is not None:
        new = t.recompute(new)
    else:  # 재계산 함수가 없으면 응용집계 컬럼은 버린다
        pass
    keep_vals = [c for c in t.values if c in new.columns]
    return AggTable(
        key=t.key, title=t.title, df=new.reset_index(drop=True), dims=t.dims, values=keep_vals,
        kind={c: t.kind[c] for c in keep_vals}, sources=t.sources, unit=t.unit, labels=t.labels,
        note=t.note, support=sup, derived={k: v for k, v in t.derived.items() if k in keep_vals},
        margins=t.margins, rollups=[], recompute=t.recompute, is_extract=False, support_only=t.support_only,
        col_sources=t.col_sources, rollup_agg=t.rollup_agg,
    )


def _replace_df(t: AggTable, df: pd.DataFrame) -> AggTable:
    return AggTable(
        key=t.key, title=t.title, df=df, dims=t.dims, values=t.values, kind=t.kind, sources=t.sources,
        unit=t.unit, labels=t.labels, note=t.note, support=t.support, derived=t.derived, margins=t.margins,
        rollups=[], recompute=t.recompute, is_extract=t.is_extract, support_only=t.support_only,
        col_sources=t.col_sources, rollup_agg=t.rollup_agg,
    )


def _complementary_suppression(
    t: AggTable, simple_cols: list[str], primary: pd.DataFrame
) -> tuple[pd.DataFrame, int]:
    """합계가 공개되는 묶음에서 비공개 셀이 1개뿐이면 역산 가능 → 가장 작은 다른 셀을 추가 비공개.

    - margins에 지정된 차원 d에 대해: d를 제외한 나머지 차원이 같은 행들의 묶음 = 합계 공개 묶음
    - 같은 행의 여러 값 컬럼(예: 이용금액·이용건수)은 독립 취급
    - '합계' 행이 표 안에 있으면 그 행도 공개 합계로 간주
    """
    mask = primary.copy()
    df = t.df
    secondary = 0
    groupings: list[list[str]] = []
    for d in t.margins:
        others = [x for x in t.dims if x != d]
        groupings.append(others)
    if not groupings:
        return mask, 0
    changed = True
    it = 0
    while changed and it < 50:
        changed = False
        it += 1
        for c in simple_cols:
            vals = pd.to_numeric(df[c], errors="coerce")
            for others in groupings:
                keys = df[others].astype(str).agg("|".join, axis=1) if others else pd.Series("ALL", index=df.index)
                for _, idx in keys.groupby(keys).groups.items():
                    idx = list(idx)
                    m = mask.loc[idx, c]
                    n_hidden = int(m.sum())
                    if n_hidden == 1 and len(idx) > 1:
                        cand = [i for i in idx if not mask.at[i, c] and pd.notna(vals.at[i])]
                        if not cand:
                            continue
                        j = min(cand, key=lambda i: vals.at[i])
                        mask.at[j, c] = True
                        secondary += 1
                        changed = True
    return mask, secondary


# --------------------------------------------------------------------------------------
# 반출 신청서 초안
# --------------------------------------------------------------------------------------
def export_request_rows(results: list[GuardResult], dataset_labels: dict[str, str], purpose: str) -> list[dict]:
    """반출 신청 화면에 옮겨 적을 '자료별 출처·활용 사유' 초안."""
    rows = []
    for r in results:
        if not r.ok:
            continue
        srcs = [dataset_labels.get(s, s) for s in r.table.sources]
        rows.append(
            {
                "자료": r.table.title,
                "출처(원본데이터 명칭)": "; ".join(srcs),
                "가공 방식": r.method,
                "반출 기준 처리 내역": " / ".join(r.actions),
                "활용 사유": purpose,
            }
        )
    return rows
