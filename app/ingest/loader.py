"""센터/허브 CSV 인식·병합·검증.

`load_files(paths)` → `IngestReport`
- 인코딩 자동 판별(UTF-8 BOM → UTF-8 → CP949 → EUC-KR), 따옴표 혼재·앞뒤 공백 정리, 'NA'·빈 값 → 결측
- 헤더로 데이터셋 인식(app.catalog.Catalog.detect; 일부 컬럼만 선택·순서 변경·pt_year/pt_month 유무 허용)
- 같은 데이터셋 파일(월별 1개씩)을 병합, 같은 파일 재업로드·겹치는 행 제거, 측정값 수치 변환
- 기간 커버리지(월/일)·누락 기간·출처 표기(“제주빅데이터센터, 데이터셋명(YYYY.MM~YYYY.MM)”) 산출
- SYNTHETIC(가상) 데이터 표시: 파일명에 'SYNTHETIC' 포함 또는 같은 폴더 manifest.json 의 synthetic 표시

반출 정책상 행 데이터를 화면/LLM 에 보여주는 함수는 두지 않는다(요약 메타데이터만 제공, repr 에 df 미포함).
"""
from __future__ import annotations

import calendar
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from ..catalog.catalog import Catalog, DatasetSpec, load_catalog, normalize_header

NA_VALUES = {"", "NA", "N/A", "NULL", "null", "NaN", "nan", "None"}
ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")
_NUM_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")


@dataclass
class FileInfo:
    path: Path
    name: str
    size: int = 0
    sha1: str = ""
    encoding: str | None = None
    dataset_id: str | None = None
    dataset_name: str = ""
    score: float = 0.0
    ambiguous: bool = False
    candidates: list[tuple[str, float]] = field(default_factory=list)
    missing_cols: list[str] = field(default_factory=list)
    extra_cols: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: int = 0
    periods: list[str] = field(default_factory=list)
    status: str = "pending"          # loaded | duplicate | unrecognized | error
    message: str = ""
    is_synthetic: bool = False

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["path"] = str(self.path)
        return d


@dataclass
class LoadedDataset:
    id: str
    name: str
    df: pd.DataFrame = field(repr=False)
    files: list[str] = field(default_factory=list)
    file_paths: list[Path] = field(default_factory=list, repr=False)
    months: list[str] = field(default_factory=list)       # 'YYYY-MM'
    days: list[str] = field(default_factory=list, repr=False)  # 'YYYY-MM-DD' (일·시간 단위 데이터)
    missing_periods: list[str] = field(default_factory=list)
    row_count: int = 0
    warnings: list[str] = field(default_factory=list)
    source_label: str = ""
    coverage_text: str = ""
    is_synthetic: bool = False
    exportable: bool = False
    grain: str = ""
    source: str = "center"
    detect_score: float = 0.0
    missing_cols: list[str] = field(default_factory=list)
    extra_cols: list[str] = field(default_factory=list)
    duplicates_removed: int = 0

    @property
    def spec(self) -> DatasetSpec:
        return load_catalog().get(self.id)

    @property
    def period_start(self) -> str | None:
        return (self.days[0] if self.days else (self.months[0] if self.months else None))

    @property
    def period_end(self) -> str | None:
        return (self.days[-1] if self.days else (self.months[-1] if self.months else None))

    def summary(self) -> dict:
        """메타데이터만(행 값 없음)."""
        return dict(id=self.id, name=self.name, files=list(self.files), rows=self.row_count, columns=list(self.df.columns),
                    months=list(self.months), n_days=len(self.days), coverage=self.coverage_text,
                    missing_periods=list(self.missing_periods), source_label=self.source_label,
                    is_synthetic=self.is_synthetic, exportable=self.exportable, grain=self.grain,
                    missing_cols=list(self.missing_cols), extra_cols=list(self.extra_cols),
                    duplicates_removed=self.duplicates_removed, warnings=list(self.warnings))


@dataclass
class IngestReport:
    datasets: dict[str, LoadedDataset] = field(default_factory=dict)
    files: list[FileInfo] = field(default_factory=list)
    unrecognized: list[FileInfo] = field(default_factory=list)
    duplicates: list[FileInfo] = field(default_factory=list)
    errors: list[FileInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def get(self, dataset_id: str) -> LoadedDataset | None:
        return self.datasets.get(dataset_id)

    def frames(self) -> dict[str, pd.DataFrame]:
        return {k: v.df for k, v in self.datasets.items()}

    @property
    def is_synthetic(self) -> bool:
        return any(d.is_synthetic for d in self.datasets.values())

    def summary(self) -> dict:
        return dict(datasets={k: v.summary() for k, v in self.datasets.items()},
                    files=[f.to_dict() for f in self.files],
                    unrecognized=[f.name for f in self.unrecognized], duplicates=[f.name for f in self.duplicates],
                    errors=[f"{f.name}: {f.message}" for f in self.errors], warnings=list(self.warnings))


# ---------------------------------------------------------------------------------------------- 읽기
def _decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for enc in ENCODINGS[1:]:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", raw[:10], 0, 1, "지원하지 않는 인코딩(UTF-8/CP949 아님)")


def _read_csv_text(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_filter=False, sep=",",
                     skipinitialspace=False, engine="c")
    df.columns = [normalize_header(c) for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not re.fullmatch(r"unnamed: ?\d+", c)]]
    for c in df.columns:
        s = df[c].str.strip()
        df[c] = s.where(~s.isin(NA_VALUES), None)
    return df


def _synthetic_names(folder: Path) -> set[str]:
    p = folder / "manifest.json"
    if not p.exists():
        return set()
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return set()
    if not m.get("synthetic", False):
        return set()
    return {f.get("file") if isinstance(f, dict) else str(f) for f in m.get("files", [])}


def _sample_values(df: pd.DataFrame, n: int = 60) -> dict[str, list[str]]:
    out = {}
    for c in df.columns:
        v = df[c].dropna()
        if len(v):
            out[c] = v.drop_duplicates().head(n).astype(str).tolist()
    return out


def _to_num(s: pd.Series) -> tuple[pd.Series, int]:
    """문자열 → 수치. 천단위 쉼표 허용. 변환 실패(결측 아님) 개수 반환."""
    v = s.astype("string").str.strip()
    has_comma = v.str.contains(",", regex=False, na=False)
    if has_comma.any():
        ok = v[has_comma].str.match(_NUM_RE)
        v = v.mask(has_comma & ok.reindex(v.index, fill_value=False), v.str.replace(",", "", regex=False))
    out = pd.to_numeric(v, errors="coerce")
    bad = int((out.isna() & v.notna()).sum())
    if out.notna().any():
        vals = out.dropna()
        if (vals == np.floor(vals)).all() and vals.abs().max() < 2**53:
            out = out.astype("Int64") if out.isna().any() else out.astype("int64")
    return out, bad


def _norm_ym(s: pd.Series) -> pd.Series:
    """'202401' / '2024-01' / '2024-01-01' / '21-Apr' → 'YYYY-MM'."""
    s = s.astype("string").str.strip()
    d = s.str.replace(r"\D", "", regex=True)
    out = pd.Series(pd.NA, index=s.index, dtype="string")
    m = d.str.len() >= 6
    out[m] = d[m].str.slice(0, 4) + "-" + d[m].str.slice(4, 6)
    m3 = s.str.match(r"^\d{2}-[A-Za-z]{3}$", na=False)
    if m3.any():
        out[m3] = pd.to_datetime(s[m3], format="%y-%b", errors="coerce").dt.strftime("%Y-%m")
    return out


def _norm_day(s: pd.Series) -> pd.Series:
    d = s.astype("string").str.replace(r"\D", "", regex=True).str.slice(0, 8)
    return pd.to_datetime(d, format="%Y%m%d", errors="coerce")


def _segments(items: list, step) -> list[tuple]:
    if not items:
        return []
    segs, start, prev = [], items[0], items[0]
    for x in items[1:]:
        if x != step(prev):
            segs.append((start, prev))
            start = x
        prev = x
    segs.append((start, prev))
    return segs


def _next_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + (m == 12)}-{1 if m == 12 else m + 1:02d}"


def _coverage(months: list[str], days: list[str], grain: str) -> tuple[str, list[str], str]:
    """(coverage_text, missing_periods, citation_period)."""
    def mlabel(ym):
        return ym.replace("-", ".")
    if days:
        dts = [date.fromisoformat(d) for d in days]
        segs = _segments(dts, lambda x: x + timedelta(days=1))
        cov = ", ".join(f"{a:%Y.%m.%d}~{b:%Y.%m.%d}" if a != b else f"{a:%Y.%m.%d}" for a, b in segs)
        missing = []
        have = set(dts)
        cur = dts[0]
        months_seen: dict[str, list[date]] = {}
        while cur <= dts[-1]:
            if cur not in have:
                months_seen.setdefault(f"{cur:%Y-%m}", []).append(cur)
            cur += timedelta(days=1)
        for ym, lst in months_seen.items():
            y, m = int(ym[:4]), int(ym[5:])
            if len(lst) == calendar.monthrange(y, m)[1]:
                missing.append(ym)
            else:
                missing += [f"{x:%Y-%m-%d}" for x in lst]
        msegs = _segments(sorted({f"{x:%Y-%m}" for x in dts}), _next_month)
        cite = ", ".join(f"{mlabel(a)}~{mlabel(b)}" if a != b else mlabel(a) for a, b in msegs)
        return cov, missing, cite
    if months:
        segs = _segments(months, _next_month)
        cov = ", ".join(f"{mlabel(a)}~{mlabel(b)}" if a != b else mlabel(a) for a, b in segs)
        missing, cur = [], months[0]
        while cur <= months[-1]:
            if cur not in months:
                missing.append(cur)
            cur = _next_month(cur)
        return cov, missing, cov
    return "", [], ""


def _expand(paths: Iterable) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.suffix.lower() in (".csv", ".txt"))
        else:
            out.append(p)
    return out


def inspect_file(path: Path | str, catalog: Catalog | None = None, nrows: int = 500) -> FileInfo:
    """헤더·앞부분만 읽어 데이터셋 인식(미리보기용; 행 값은 반환하지 않음)."""
    cat = catalog or load_catalog()
    path = Path(path)
    fi = FileInfo(path=path, name=path.name)
    try:
        raw = path.read_bytes()
        fi.size = len(raw)
        head = raw[: 2_000_000]
        if len(raw) > len(head) and b"\n" in head:      # 멀티바이트 문자가 잘리지 않도록 줄 경계에서 자름
            head = head[: head.rfind(b"\n") + 1]
        text, fi.encoding = _decode(head)
        lines = text.splitlines()[: nrows + 1]
        df = _read_csv_text("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        fi.status, fi.message = "error", f"읽기 실패: {e}"
        return fi
    _detect_into(fi, df, cat)
    return fi


def _detect_into(fi: FileInfo, df: pd.DataFrame, cat: Catalog) -> None:
    fi.columns = list(df.columns)
    r = cat.detect(df.columns, values=_sample_values(df.head(5000)))
    fi.dataset_id = r.dataset_id
    fi.dataset_name = r.name
    fi.score = r.score
    fi.ambiguous = r.ambiguous
    fi.candidates = [(c.dataset_id, c.score) for c in r.candidates[:3]]
    fi.missing_cols, fi.extra_cols = list(r.missing_cols), list(r.extra_cols)
    if not r.dataset_id:
        fi.status, fi.message = "unrecognized", r.reason or "데이터셋을 인식하지 못함"


# ---------------------------------------------------------------------------------------------- 메인
def load_files(paths: Iterable[Path | str], catalog: Catalog | None = None,
               synthetic: bool | None = None) -> IngestReport:
    """CSV 파일(또는 폴더)들을 읽어 데이터셋별로 병합한다. synthetic=True 면 모두 가상 데이터로 표시."""
    cat = catalog or load_catalog()
    rep = IngestReport()
    seen_hash: dict[str, str] = {}
    groups: dict[str, list[tuple[FileInfo, pd.DataFrame]]] = {}
    syn_cache: dict[Path, set[str]] = {}
    for path in _expand(paths):
        fi = FileInfo(path=path, name=path.name)
        rep.files.append(fi)
        try:
            raw = path.read_bytes()
        except OSError as e:
            fi.status, fi.message = "error", f"파일을 열 수 없음: {e}"
            rep.errors.append(fi)
            continue
        fi.size = len(raw)
        fi.sha1 = hashlib.sha1(raw).hexdigest()
        if fi.sha1 in seen_hash:
            fi.status, fi.message = "duplicate", f"'{seen_hash[fi.sha1]}'과(와) 내용이 같은 파일(중복 업로드) — 제외"
            rep.duplicates.append(fi)
            rep.warnings.append(f"{fi.name}: 중복 파일 제외")
            continue
        seen_hash[fi.sha1] = fi.name
        try:
            text, fi.encoding = _decode(raw)
            df = _read_csv_text(text)
        except Exception as e:  # noqa: BLE001
            fi.status, fi.message = "error", f"CSV 읽기 실패: {e}"
            rep.errors.append(fi)
            continue
        fi.rows = len(df)
        _detect_into(fi, df, cat)
        if not fi.dataset_id:
            rep.unrecognized.append(fi)
            continue
        folder = path.parent
        if folder not in syn_cache:
            syn_cache[folder] = _synthetic_names(folder)
        fi.is_synthetic = bool(synthetic) or "synthetic" in path.name.lower() or path.name in syn_cache[folder]
        fi.status = "loaded"
        groups.setdefault(fi.dataset_id, []).append((fi, df))
    for ds_id, items in groups.items():
        rep.datasets[ds_id] = _merge(cat.get(ds_id), items)
        rep.warnings += [f"[{ds_id}] {w}" for w in rep.datasets[ds_id].warnings]
    return rep


def load_dir(folder: Path | str, **kw) -> IngestReport:
    return load_files([Path(folder)], **kw)


def _merge(spec: DatasetSpec, items: list[tuple[FileInfo, pd.DataFrame]]) -> LoadedDataset:
    warnings: list[str] = []
    tcol = normalize_header(spec.time_col) if spec.time_col else None
    colsets = {tuple(df.columns) for _, df in items}
    if len({frozenset(c) for c in colsets}) > 1:
        warnings.append("파일마다 컬럼 구성이 다름 — 합집합으로 병합(없는 컬럼은 결측)")
    frames = []
    for i, (fi, df) in enumerate(items):
        d = df.copy()
        d["__file"] = i
        d["__occ"] = d.groupby(list(df.columns), dropna=False, sort=False).cumcount()
        if tcol and tcol in d:
            per = _norm_ym(d[tcol]).dropna().unique().tolist()
            fi.periods = sorted(per)
            if spec.grain in ("month",) and len(per) > 1:
                warnings.append(f"{fi.name}: 한 파일에 {len(per)}개월 포함")
        frames.append(d)
    all_cols = list(dict.fromkeys(c for _, df in items for c in df.columns))
    big = pd.concat(frames, ignore_index=True, sort=False)
    n0 = len(big)
    # 같은 내용의 행이 여러 파일에 있으면(같은 달 파일을 이름만 바꿔 다시 올린 경우 등) 첫 파일만 유지.
    # 한 파일 안의 동일 행은 유지(선택 컬럼만 내려받아 같은 값이 반복될 수 있음).
    key = [c for c in all_cols] + ["__occ"]
    big = big.drop_duplicates(subset=key, keep="first")
    removed = n0 - len(big)
    if removed:
        warnings.append(f"여러 파일에 중복된 행 {removed:,}건 제거")
    # 중복 제거 후에도 같은 기간(월)의 행이 여러 파일에서 남아 있으면(내용이 다른 같은 달 파일) 경고
    if tcol and tcol in big and len(items) > 1 and spec.grain in ("month", "day", "hour"):
        nfile = big.assign(__p=_norm_ym(big[tcol])).groupby("__p")["__file"].nunique()
        overl = sorted(nfile[nfile > 1].index.astype(str).tolist())
        if overl:
            warnings.append(f"같은 기간({', '.join(overl[:6])}{'…' if len(overl) > 6 else ''})의 서로 다른 행이 "
                            "여러 파일에 있음 — 조회 조건이 다른 파일인지 확인 필요")
    big = big.drop(columns=["__file", "__occ"]).reset_index(drop=True)
    big = big[[c for c in all_cols]]
    # 수치 변환
    num_cols = [normalize_header(c) for c in spec.numeric_cols()]
    for c in num_cols:
        if c in big:
            big[c], bad = _to_num(big[c])
            if bad:
                warnings.append(f"{c}: 숫자로 변환할 수 없는 값 {bad:,}건 → 결측 처리")
    # 기간
    months, days = [], []
    if tcol and tcol in big:
        if spec.grain == "month" or tcol in ("base_year_month", "년월"):
            months = sorted(_norm_ym(big[tcol]).dropna().unique().tolist())
        else:
            dts = _norm_day(big[tcol]).dropna()
            days = sorted({f"{x:%Y-%m-%d}" for x in dts.unique()})
            months = sorted({d[:7] for d in days})
            if not days:
                months = sorted(_norm_ym(big[tcol]).dropna().unique().tolist())
    cov, missing, cite = _coverage(months, days, spec.grain)
    if missing and spec.grain != "static":
        warnings.append(f"누락 기간 {len(missing)}건: {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}")
    first = items[0][0]
    if first.ambiguous:
        alt = ", ".join(c for c, _ in first.candidates[1:2])
        warnings.append(f"헤더만으로는 {alt}와(과) 구분이 어려움 — '{spec.name}'(으)로 처리")
    miss_cols = sorted({c for fi, _ in items for c in fi.missing_cols})
    if miss_cols:
        warnings.append(f"명세서 컬럼 중 포함되지 않은 컬럼: {', '.join(miss_cols)}")
    extra = sorted({c for fi, _ in items for c in fi.extra_cols})
    if extra:
        warnings.append(f"명세서에 없는 컬럼: {', '.join(extra)}")
    is_syn = any(fi.is_synthetic for fi, _ in items)
    label = spec.citation(cite)
    if is_syn:
        label += " [가상 데이터(SYNTHETIC)]"
    return LoadedDataset(
        id=spec.id, name=spec.name, df=big, files=[fi.name for fi, _ in items], file_paths=[fi.path for fi, _ in items],
        months=months, days=days, missing_periods=missing, row_count=len(big), warnings=warnings, source_label=label,
        coverage_text=cov, is_synthetic=is_syn, exportable=spec.exportable, grain=spec.grain, source=spec.source,
        detect_score=min(fi.score for fi, _ in items), missing_cols=miss_cols, extra_cols=extra,
        duplicates_removed=removed)
