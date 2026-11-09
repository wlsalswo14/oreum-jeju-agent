"""차트(PNG) 생성 — 반드시 반출 검사를 통과한 표(GuardResult)로만 그린다.

반출 정책: 시각화는 그림파일(png/jpg) 형태로 반출 가능, 시각화 안의 수치는 반출 기준을 따름.
→ 비공개 셀은 그리지 않고(빈칸), 각주로 표시한다.
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from ..analysis.common import ChartSpec  # noqa: E402
from ..guard.export_guard import GuardResult  # noqa: E402

# 브랜드 색: 감귤(강조) · 바다(기본) · 현무암(텍스트)
TANGERINE = "#F28C28"
OCEAN = "#1E6FB8"
OCEAN_LIGHT = "#8FB8DE"
BASALT = "#2B2D31"
MUTED = "#A7B1BF"
GRID = "#E6E9EE"
SERIES = [OCEAN, TANGERINE, "#2E9E6B", "#8E5CC4", "#C44E52", "#7F8C8D"]


def _setup_font() -> None:
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams.update({
        "axes.edgecolor": GRID, "axes.labelcolor": BASALT, "xtick.color": BASALT, "ytick.color": BASALT,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
        "axes.titlesize": 12, "axes.titleweight": "bold", "figure.dpi": 110,
    })


_setup_font()


def _fmt_num(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.1f}억"
    if a >= 1e4:
        return f"{v / 1e4:,.0f}만"
    if a >= 100:
        return f"{v:,.0f}"
    return f"{v:,.1f}"


def _axis_fmt(ax, axis="y"):
    from matplotlib.ticker import FuncFormatter

    def tick(v, _):
        a = abs(v)
        if a >= 1e8:
            return f"{v / 1e8:,.1f}억"
        if a >= 1e4:
            s = f"{v / 1e4:,.1f}"
            return (s[:-2] if s.endswith(".0") else s) + "만"
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"

    f = FuncFormatter(tick)
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def render(spec: ChartSpec, gr: GuardResult, out_dir: Path) -> Path | None:
    if not gr.ok:
        return None
    t = gr.table
    df = t.df.copy()
    if df.empty:
        return None
    for c in spec.y:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # 비공개(반출 기준) 셀과 '자료 없음' 셀을 구분: 자료 없는 행은 막대 차트에서 제외
    y0 = spec.y[0] if spec.y else None
    m = gr.suppressed_mask
    df["_hidden"] = m[y0].reindex(df.index).fillna(False).astype(bool).values if (m is not None and y0 in m.columns) else False
    if spec.kind in ("bar", "barh") and y0 in df.columns:
        df = df[df[y0].notna() | df["_hidden"]]
        if df.empty:
            return None
    fig = None
    try:
        if spec.kind == "line":
            fig = _line(spec, df, t)
        elif spec.kind == "bar":
            fig = _bar(spec, df, t)
        elif spec.kind == "barh":
            fig = _barh(spec, df, t)
        elif spec.kind == "stacked":
            fig = _stacked(spec, df, t)
        elif spec.kind == "heat":
            fig = _heat(spec, df, t)
        elif spec.kind == "donut":
            fig = _donut(spec, df, t)
        if fig is None:
            return None
        foot = []
        if gr.n_suppressed:
            foot.append(f"※ 반출 기준(10 미만)에 따라 {gr.n_suppressed}개 값 비공개")
        foot.append("출처: " + ", ".join(_src_label(s) for s in t.sources))
        fig.text(0.01, 0.005, "   ".join(foot), fontsize=7.5, color="#6B7280", ha="left", va="bottom")
        out = out_dir / f"{spec.key.replace('.', '_')}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
        return out
    finally:
        if fig is not None:
            plt.close(fig)


def _src_label(s: str) -> str:
    if s.startswith("hub:"):
        return f"제주데이터허브({s[4:]})"
    return f"제주빅데이터센터({s})"


def _xlabels(df, x):
    vals = df[x].tolist()
    out = []
    for v in vals:
        s = str(v)
        if s.isdigit() and len(s) == 6:
            s = f"{s[2:4]}.{s[4:]}"
        out.append(s)
    return out


def _line(spec, df, t):
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    if spec.series and spec.series in df.columns:
        for i, (k, g) in enumerate(df.groupby(spec.series, sort=False)):
            ax.plot(g[spec.x].astype(str), g[spec.y[0]], marker="o", ms=3, lw=2, color=SERIES[i % len(SERIES)], label=str(k))
        ax.legend(frameon=False, loc="upper left")
    else:
        xs = _xlabels(df, spec.x)
        for i, c in enumerate(spec.y):
            ax.plot(xs, df[c], marker="o", ms=3, lw=2.2, color=SERIES[i % len(SERIES)], label=t.labels.get(c, c))
        if len(spec.y) > 1:
            ax.legend(frameon=False, loc="upper left")
        if spec.highlight:
            _shade(ax, xs, spec.highlight, spec.highlight_label)
        _thin_xticks(ax, xs)
    ax.set_title(spec.title, loc="left")
    ax.set_ylabel(spec.y_label, rotation=0, ha="right", va="center", labelpad=8)
    _axis_fmt(ax)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def _shade(ax, xs, hl, label):
    try:
        i0, i1 = xs.index(str(hl[0])), xs.index(str(hl[1]))
    except ValueError:
        return
    ax.axvspan(i0 - 0.5, i1 + 0.5, color=TANGERINE, alpha=0.15, lw=0)
    ax.text((i0 + i1) / 2, ax.get_ylim()[1], label, ha="center", va="bottom", color=TANGERINE, fontsize=9, fontweight="bold")


def _thin_xticks(ax, xs, max_n=16):
    n = len(xs)
    if n > max_n:
        step = math.ceil(n / max_n)
        ax.set_xticks(range(0, n, step))
        ax.set_xticklabels([xs[i] for i in range(0, n, step)], rotation=0)


def _bar(spec, df, t):
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    xs = _xlabels(df, spec.x)
    c = spec.y[0]
    vals = df[c].values
    colors = [OCEAN] * len(xs)
    if spec.highlight:
        try:
            i0, i1 = xs.index(str(spec.highlight[0])), xs.index(str(spec.highlight[1]))
            for i in range(i0, i1 + 1):
                colors[i] = TANGERINE
        except ValueError:
            pass
    elif len(vals) and np.isfinite(np.nanmax(vals)):
        colors[int(np.nanargmax(vals))] = TANGERINE
    ax.bar(range(len(xs)), np.nan_to_num(vals, nan=0.0), color=colors, width=0.7)
    for i, v in enumerate(vals):
        if not np.isfinite(v):
            ax.text(i, 0, "비공개", ha="center", va="bottom", fontsize=7, color="#9CA3AF")
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs, rotation=0 if len(xs) <= 12 else 45, ha="center" if len(xs) <= 12 else "right")
    _thin_xticks(ax, xs, 24)
    if (np.nan_to_num(vals) < 0).any():
        ax.axhline(0, color=BASALT, lw=0.8)
    ax.set_title(spec.title, loc="left")
    ax.set_ylabel(spec.y_label, rotation=0, ha="right", va="center", labelpad=8)
    _axis_fmt(ax)
    if len(xs) <= 12:
        for i, v in enumerate(vals):
            if np.isfinite(v):
                ax.text(i, v, _fmt_num(v), ha="center", va="bottom" if v >= 0 else "top", fontsize=8, color=BASALT)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def _barh(spec, df, t):
    c = spec.y[0]
    d = df[[spec.x, c]].copy()
    if spec.sort:
        d = d.sort_values(c, ascending=True, na_position="first")
    h = max(2.6, 0.32 * len(d) + 0.8)
    fig, ax = plt.subplots(figsize=(8.6, h))
    hl = set(spec.extra.get("highlight_rows", []))
    colors = [TANGERINE if str(v) in hl else (OCEAN if len(hl) == 0 else MUTED) for v in d[spec.x]]
    if not hl and len(d):
        colors = [OCEAN] * len(d)
    vals = d[c].values.astype(float)
    ax.barh(range(len(d)), np.nan_to_num(vals, nan=0.0), color=colors, height=0.65)
    ax.set_yticks(range(len(d)))
    ax.set_yticklabels([str(v) for v in d[spec.x]])
    for i, v in enumerate(vals):
        ax.text(np.nan_to_num(v) if np.isfinite(v) else 0, i, " " + (_fmt_num(v) if np.isfinite(v) else "비공개"),
                va="center", fontsize=8, color=BASALT)
    if (np.nan_to_num(vals) < 0).any():
        ax.axvline(0, color=BASALT, lw=0.8)
    ax.set_title(spec.title, loc="left")
    _axis_fmt(ax, "x")
    ax.grid(axis="y", visible=False)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def _stacked(spec, df, t):
    c = spec.y[0]
    p = df.pivot_table(index=spec.x, columns=spec.series, values=c, aggfunc="sum", sort=False)
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    bottom = np.zeros(len(p))
    for i, col in enumerate(p.columns):
        v = np.nan_to_num(p[col].values.astype(float))
        ax.bar(range(len(p)), v, bottom=bottom, color=SERIES[i % len(SERIES)], label=str(col), width=0.65)
        bottom += v
    ax.set_xticks(range(len(p)))
    ax.set_xticklabels([str(i) for i in p.index])
    ax.legend(frameon=False, ncol=len(p.columns))
    ax.set_title(spec.title, loc="left")
    ax.set_ylabel(spec.y_label or "%", rotation=0, ha="right", va="center", labelpad=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def _heat(spec, df, t):
    c = spec.y[0]
    p = df.pivot_table(index=spec.series, columns=spec.x, values=c, aggfunc="sum", sort=False)
    order = [d for d in ["월", "화", "수", "목", "금", "토", "일"] if d in p.index]
    p = p.loc[order] if order else p
    fig, ax = plt.subplots(figsize=(8.6, 3.2))
    im = ax.imshow(p.values.astype(float), aspect="auto", cmap="YlOrBr")
    ax.set_yticks(range(len(p.index)))
    ax.set_yticklabels([str(i) for i in p.index])
    ax.set_xticks(range(len(p.columns)))
    ax.set_xticklabels([str(int(x)) if str(x).isdigit() or isinstance(x, (int, np.integer)) else str(x) for x in p.columns], fontsize=8)
    ax.grid(False)
    ax.set_xlabel("시각")
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    ax.set_title(spec.title, loc="left")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def _donut(spec, df, t):
    c = spec.y[0]
    d = df[[spec.x, c]].dropna().sort_values(c, ascending=False)
    if d.empty:
        return None
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    colors = [SERIES[i % len(SERIES)] for i in range(len(d))]
    wedges, _ = ax.pie(d[c].values, colors=colors, startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.38, edgecolor="white"))
    ax.legend(wedges, [f"{a}  {b:.1f}%" for a, b in zip(d[spec.x], d[c])], frameon=False, loc="center left",
              bbox_to_anchor=(1.0, 0.5))
    ax.set_title(spec.title, loc="left")
    ax.axis("equal")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def render_map(points: list[dict], title: str, out_dir: Path, center: tuple[float, float] | None = None) -> Path | None:
    """보고서용 정적 지도(배경지도 없이 좌표 산점도). 반출 가능/공개 데이터 좌표만 사용."""
    if not points:
        return None
    df = pd.DataFrame(points)
    fig, ax = plt.subplots(figsize=(7.6, 5.6))
    ax.set_facecolor("#F3F7FB")
    styles = {"store": (TANGERINE, 9, "동종 점포(허브 상가정보)"), "stop": (OCEAN, 26, "버스 정류소(크기=이용량)"),
              "venue": ("#C0392B", 260, "행사장")}
    for typ, (col, size, lab) in styles.items():
        g = df[df["type"] == typ]
        if g.empty:
            continue
        s = size
        if typ == "stop" and "value" in g and g["value"].notna().any():
            v = g["value"].fillna(0).astype(float)
            s = 18 + 320 * (v / max(1.0, v.max()))
        ax.scatter(g["lon"], g["lat"], s=s, c=col, alpha=0.55 if typ == "store" else 0.85, label=lab,
                   marker="*" if typ == "venue" else "o", edgecolors="white", linewidths=0.6, zorder=3 if typ != "store" else 2)
    stops = df[df["type"] == "stop"]
    if len(stops):  # 이용량 상위 정류소 이름 표시(없으면 앞에서 6개)
        top = stops.sort_values("value", ascending=False).head(6) if "value" in stops else stops.head(6)
        for _, r in top.iterrows():
            ax.annotate(str(r["name"]), (r["lon"], r["lat"]), xytext=(5, 4), textcoords="offset points",
                        fontsize=7.5, color=BASALT, zorder=4)
    for _, r in df[df["type"] == "venue"].iterrows():
        ax.annotate(str(r["name"]), (r["lon"], r["lat"]), xytext=(7, -10), textcoords="offset points", fontsize=8.5,
                    color="#C0392B", fontweight="bold", zorder=4)
    lon_pad = max(0.004, (df["lon"].max() - df["lon"].min()) * 0.08)
    lat_pad = max(0.003, (df["lat"].max() - df["lat"].min()) * 0.08)
    ax.set_xlim(df["lon"].min() - lon_pad, df["lon"].max() + lon_pad)
    ax.set_ylim(df["lat"].min() - lat_pad, df["lat"].max() + lat_pad)
    ax.set_aspect(1 / math.cos(math.radians(33.4)))
    ax.ticklabel_format(useOffset=False, style="plain")
    from matplotlib.ticker import FormatStrFormatter

    ax.xaxis.set_major_formatter(FormatStrFormatter("%.3f°E"))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.3f°N"))
    ax.tick_params(labelsize=7, colors="#6B7280")
    ax.grid(color="white", linewidth=1)
    ax.set_title(title, loc="left")
    ax.legend(frameon=True, facecolor="white", edgecolor=GRID, loc="best", fontsize=8)
    fig.text(0.01, 0.005, "좌표 출처: 제주빅데이터센터 버스 정류소 정보(반출 가능), 제주데이터허브 상가(상권) 정보", fontsize=7, color="#6B7280")
    out = out_dir / "map.png"
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out
