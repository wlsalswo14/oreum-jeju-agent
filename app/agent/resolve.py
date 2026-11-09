"""계획(Plan)의 자연어 값을 실제 분석 파라미터로 바꾸고, 필요한 데이터를 안내하는 '데이터 길잡이'.

- 지역: 법정동(카드)·행정동(유동인구)·좌표(버스 반경)로 동시에 해석
- 업종: BC카드 42업종 코드 + 허브 상가정보 매칭 키워드
- 행사: 허브 축제 목록(VISIT JEJU) + 장소 큐레이션
- 데이터 체크리스트: 센터 조회 화면에서 받을 데이터명·기간(월 목록)·조건·이유
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..analysis.common import IndustrySel, RegionSel, ym_add, ym_label
from ..config import DEMO_DIR, settings
from ..catalog import regions as R

HUB_NAMES = {
    "hub:793": "제주데이터허브, 제주특별자치도 상가(상권) 정보(2025.09)",
    "hub:761": "제주데이터허브, 제주관광공사 VISIT JEJU 축제행사콘텐츠",
    "hub:746": "제주데이터허브, 예비창업자를 위한 업종별 카드 매출과 매장수 매쉬업(2017~2021)",
    "hub:748": "제주데이터허브, 상권분석을 위한 유동인구에 따른 카드 매출액 변화 매쉬업",
    "hub:512": "제주데이터허브, 일별 시간대별 버스 승차 인원 수 현황",
    "hub:612": "제주데이터허브 Open API, 버스 정류소 기본 정보",
    "hub:613": "제주데이터허브 Open API, 일일 정류소별 버스 이용자 정보",
    "hub:583": "제주데이터허브 Open API, 읍면동 단위 내국인 유동인구(일별)",
}
POP583_LAST = date(2025, 10, 20)   # 583 Open API 최신 자료일(2026.11 확인). 이후 날짜는 응답 없음

DEMO = [
    {"id": "trade", "mode": "trade_area", "label": "창업·상권 분석",
     "question": "애월읍에서 카페를 창업하려고 하는데 상권이 어떤가요?",
     "datasets": ["1-08", "1-12", "1-14", "4-10", "3-03", "3-04", "1-17"]},
    {"id": "event", "mode": "event_impact", "label": "축제 효과 분석",
     "question": "2024 최남단 방어축제가 대정읍 지역경제에 준 효과를 알고 싶어요",
     "datasets": ["1-08", "1-12", "4-10", "3-03", "3-04", "5-02"]},
    {"id": "transit", "mode": "transit", "label": "버스 정류소 진단",
     "question": "대정읍 모슬포 일대 버스 정류소 이용을 진단해 주세요",
     "datasets": ["3-03", "3-04", "3-02"]},
]


# ---------------------------------------------------------------------------------------- 사전
@lru_cache(maxsize=1)
def _catalog():
    from ..catalog.catalog import load_catalog

    return load_catalog()


def dataset_name(did: str) -> str:
    if did in HUB_NAMES:
        return HUB_NAMES[did].split(", ", 1)[-1]
    try:
        return _catalog().get(did).name
    except Exception:
        return did


def export_grade(did: str) -> str:
    if did.startswith("hub:"):
        return "공개 데이터"
    try:
        return "원본 반출 가능" if _catalog().exportable(did) else "가공 형태만 반출(10 미만 제외)"
    except Exception:
        return "-"


@lru_cache(maxsize=1)
def _industry_names() -> list[str]:
    try:
        ref = _catalog().reference if hasattr(_catalog(), "reference") else {}
        b = ref.get("biz42") if isinstance(ref, dict) else None
        if b:
            return [x["name"] for x in b]
    except Exception:
        pass
    from ..analysis.event_impact import FOOD_LODGING

    return list(FOOD_LODGING.values())


@lru_cache(maxsize=1)
def _festival_names() -> list[str]:
    try:
        from ..catalog import festivals

        fn = getattr(festivals, "festival_vocab", None) or getattr(festivals, "names", None)
        if fn:
            return list(fn())[:200]
        df = festivals.load_festivals() if hasattr(festivals, "load_festivals") else None
        if df is not None:
            return list(dict.fromkeys(df["name"].astype(str)))[:200]
    except Exception:
        pass
    return []


def vocab() -> dict:
    return {"regions": R.region_vocab(), "industries": _industry_names(), "festivals": _festival_names()}


def demo_scenarios() -> list[dict]:
    return [{k: v for k, v in d.items() if k != "datasets"} for d in DEMO]


def demo_files(scenario: str) -> list[Path]:
    sc = next((d for d in DEMO if d["id"] == scenario), None)
    if not sc or not DEMO_DIR.exists():
        return []
    out = []
    for f in sorted(DEMO_DIR.glob("*.csv")):
        for did in sc["datasets"]:
            if f.name.startswith(f"SYNTHETIC_{did}_"):
                out.append(f)
                break
    return out


# ---------------------------------------------------------------------------------------- 해석
def _radius_for(m) -> float:
    lvl = getattr(m, "level", "bjd")
    name = getattr(m, "name", "")
    if lvl in ("dstrct", "city"):
        return 8.0
    if lvl == "ri":
        return 1.5
    if name.endswith(("읍", "면")):
        return 4.0
    return 1.5


def region_sel_from_text(text: str, radius: float | None = None) -> RegionSel | None:
    if not text:
        return None
    ms = R.resolve_region(text)
    ms = [m for m in ms if getattr(m, "level", "") != "island"]
    if not ms:
        return None
    m = ms[0]
    kw = m.region_sel_kwargs()
    sel = RegionSel(**kw, radius_km=radius or _radius_for(m))
    if not sel.bjd and sel.emd:
        sel.bjd = R.pop_emd_to_card_bjd(sel.emd[0])
    if not sel.emd and sel.bjd:
        e = R.card_bjd_to_pop_emd(sel.bjd[0], sel.city)
        if e:
            sel.emd = [e["emd_nm"]]
    return sel


def multi_region_sel(text: str) -> RegionSel | None:
    """'안덕면·한경면', '안덕면, 한경면' 처럼 여러 지역을 하나의 비교 묶음으로."""
    import re

    parts = [p.strip() for p in re.split(r"[·,/、]|\s+및\s+|\s+와\s+|\s+과\s+", text or "") if p.strip()]
    sels = [s for s in (region_sel_from_text(p) for p in parts) if s]
    if not sels:
        return None
    if len(sels) == 1:
        return sels[0]
    uniq = []
    for s in sels:
        if s.label not in [u.label for u in uniq]:
            uniq.append(s)
    return RegionSel(label="·".join(u.label for u in uniq), bjd=list(dict.fromkeys(sum((u.bjd for u in uniq), []))),
                     emd=list(dict.fromkeys(sum((u.emd for u in uniq), []))), center=uniq[0].center)


def industry_sel_from_text(text: str) -> IndustrySel | None:
    if not text:
        return None
    try:
        from ..catalog import industries as I

        ms = I.resolve_industry(text)
        if not ms:
            return None
        m = ms[0]
        get = (lambda k, d=None: m.get(k, d)) if isinstance(m, dict) else (lambda k, d=None: getattr(m, k, d))
        code = get("code") or get("biz_cd")
        name = get("name") or get("biz_nm") or code
        kws = get("store_keywords") or get("store_kw") or []
        if not kws and hasattr(I, "store_keywords"):
            kws = I.store_keywords(code)
        return IndustrySel(code=code, name=name, label=get("label") or get("matched") or name, store_keywords=list(kws or []))
    except Exception:
        for n in _industry_names():
            if n in text:
                return IndustrySel(code="", name=n, label=n)
        return None


def _find_festival(text: str, year: int | None):
    try:
        from ..catalog import festivals as F

        ms = F.search_festival(text, year=year)
        if not ms:
            return None
        m = ms[0]
        get = (lambda k, d=None: m.get(k, d)) if isinstance(m, dict) else (lambda k, d=None: getattr(m, k, d))
        s, e = get("start"), get("end")
        to_iso = lambda v: v.isoformat() if hasattr(v, "isoformat") else (str(v)[:10] if v else "")
        return {
            "name": get("name") or text, "start": to_iso(s), "end": to_iso(e),
            "bjd": list(get("bjd") or ([get("bjd_name")] if get("bjd_name") else [])),
            "region": get("region") or get("emd") or get("hjd") or "",
            "venue_name": get("venue") or "", "lon": get("lon"), "lat": get("lat"),
            "confidence": get("confidence") or "", "note": get("note") or "",
            "other_editions": list(get("other_editions") or []),
        }
    except Exception:
        return None


def resolve_plan(plan, question: str, explicit: bool = False) -> dict:
    mode = plan.mode
    rv: dict = {"mode": mode, "issues": []}
    q = question or ""
    region_text = plan.region if (plan.region and (explicit or plan.source == "llm")) else q
    radius = None
    try:
        radius = float(plan.radius_km) if getattr(plan, "radius_km", "") else None
    except ValueError:
        radius = None

    if mode == "trade_area":
        rsel = region_sel_from_text(region_text) or region_sel_from_text(q)
        ind_text = plan.industry if (plan.industry and (explicit or plan.source == "llm")) else q
        isel = industry_sel_from_text(ind_text) or industry_sel_from_text(q)
        if rsel is None:
            rv["issues"].append("분석할 지역(읍면동)을 입력해 주세요.")
        if isel is None:
            rv["issues"].append("업종을 선택해 주세요(예: 비알콜 음료점업=카페).")
        rv.update(region_sel=rsel, industry_sel=isel)

    elif mode == "event_impact":
        import re

        years = [int(t) for t in re.findall(r"20\d{2}", f"{plan.event_name} {q}")]
        fest = _find_festival(plan.event_name if (explicit and plan.event_name) else (plan.event_name or q),
                              years[0] if years else None)
        # 날짜 우선순위: 이용자가 화면에서 고친 값 > 질문에 적힌 날짜 > 축제 목록 > (LLM 추정값은 쓰지 않음)
        q_has_dates = bool(re.search(r"20\d{2}[.\-/년 ]\s*\d{1,2}[.\-/월 ]\s*\d{1,2}", q))
        use_plan_dates = bool(plan.event_start) and (explicit or q_has_dates or not (fest and fest.get("start")))
        start = plan.event_start if use_plan_dates else (fest or {}).get("start", "")
        end = (plan.event_end if use_plan_dates else (fest or {}).get("end", "")) or start
        name = (fest or {}).get("name") or plan.event_name or "행사"
        if plan.event_name and explicit:
            name = plan.event_name
        if len(name) > 40:          # 규칙 모드에서 질문 전체가 행사명으로 들어오는 경우
            name = name[:40] + "…"
        rsel = None
        if plan.region and (explicit or plan.source == "llm"):
            rsel = multi_region_sel(plan.region)
        if rsel is None and fest and fest.get("bjd"):
            rsel = multi_region_sel("·".join(fest["bjd"]))   # 행사장 법정동 전체를 행사지역으로
        if rsel is None and fest and fest.get("region"):
            rsel = region_sel_from_text(fest["region"])
        if rsel is None:
            rsel = region_sel_from_text(q)
        venue = None
        if fest and fest.get("lon") and fest.get("lat"):
            venue = (float(fest["lon"]), float(fest["lat"]))
        elif rsel and rsel.center:
            venue = rsel.center
        csel = multi_region_sel(plan.control_region) if plan.control_region else None
        if csel is not None and rsel is not None and set(csel.bjd) & set(rsel.bjd):
            rv.setdefault("warnings", []).append("비교지역에 행사지역이 겹쳐 있어 겹친 곳은 비교지역에서 뺐습니다.")
            keep = [b for b in csel.bjd if b not in set(rsel.bjd)]
            csel = multi_region_sel("·".join(keep)) if keep else None
        if csel is None and rsel and rsel.bjd:
            treated = set(rsel.bjd)
            nb = [n for n in R.neighbors(rsel.bjd[0], k=6, city=rsel.city, same_type=True) if n["name"] not in treated][:2]
            if nb:
                csel = multi_region_sel("·".join(n["name"] for n in nb))
        if not start:
            opts = [o for o in ((fest or {}).get("other_editions") or []) if not o.get("cancelled") and o.get("start")]
            msg = "행사 시작일·종료일을 입력해 주세요"
            if fest:
                msg += f" — 축제 목록에 '{fest.get('name')}'의 해당 연도 일정이 없습니다"
                if fest.get("note"):
                    msg += f"({fest['note'].split(';')[0]})"
                if opts:
                    msg += ". 다른 회차: " + ", ".join(f"{o['start']}~{o['end']}" for o in opts[-3:])
            else:
                msg += "(축제 목록에서 찾지 못함)"
            rv["issues"].append(msg + ".")
            rv["event_options"] = opts[-3:]
        else:
            try:
                s_, e_ = date.fromisoformat(start), date.fromisoformat(end)
                if e_ < s_:
                    rv["issues"].append("종료일이 시작일보다 빠릅니다.")
                elif (e_ - s_).days > 31:
                    rv.setdefault("warnings", []).append("행사 기간이 한 달을 넘어 일 단위 효과가 흐려질 수 있습니다.")
            except ValueError:
                rv["issues"].append("날짜 형식은 YYYY-MM-DD로 입력해 주세요.")
        if rsel is None:
            rv["issues"].append("행사가 열린 지역(읍면동)을 입력해 주세요.")
        if fest and fest.get("confidence") in ("low",):
            rv.setdefault("warnings", []).append("행사 장소가 추정값입니다. 지역을 확인해 주세요.")
        rv.update(region_sel=rsel, control_sel=csel,
                  event={"name": name, "start": start, "end": end,
                         "venue": list(venue) if venue else None, "venue_name": (fest or {}).get("venue_name", ""),
                         "from_catalog": bool(fest and fest.get("start") and start == fest.get("start"))})

    elif mode == "transit":
        rsel = region_sel_from_text(region_text, radius) if region_text else None
        if rsel is None and region_text != q:
            rsel = region_sel_from_text(q, radius)
        if rsel is not None and radius:
            rsel.radius_km = radius
        rv.update(region_sel=rsel)
    else:
        rv["issues"].append("상권·축제·버스 분석 중 하나로 질문해 주세요.")
    rv["summary"] = _summary(rv)
    return rv


def _summary(rv: dict) -> str:
    m = rv.get("mode")
    r = rv.get("region_sel")
    parts = []
    if r:
        if len(r.bjd) > 6:
            parts.append(f"지역 {r.label}(법정동 {len(r.bjd)}곳 / 행정동 {len(r.emd)}곳)")
        else:
            parts.append(f"지역 {r.label}(카드 법정동: {', '.join(r.bjd) or '-'} / 유동인구 행정동: {', '.join(r.emd) or '-'})")
    if m == "trade_area" and rv.get("industry_sel"):
        i = rv["industry_sel"]
        parts.append(f"업종 {i.name}({i.code})")
    if m == "event_impact":
        e = rv.get("event", {})
        parts.append(f"행사 {e.get('name')} {e.get('start')}~{e.get('end')}" + (" (축제 목록에서 찾음)" if e.get("from_catalog") else ""))
        if rv.get("control_sel"):
            parts.append(f"비교지역 {rv['control_sel'].label}")
    if m == "transit" and r:
        parts.append("지역 안 모든 정류소" if len(r.bjd) > 3 else f"반경 {r.radius_km:g}km 정류소")
    return " · ".join(parts)


def public_resolved(rv: dict) -> dict:
    r, i, c = rv.get("region_sel"), rv.get("industry_sel"), rv.get("control_sel")
    e = rv.get("event") or {}
    return {
        "region": r.label if r else "", "bjd": r.bjd if r else [], "emd": r.emd if r else [],
        "radius_km": r.radius_km if r else None, "industry": i.name if i else "", "industry_code": i.code if i else "",
        "event_name": e.get("name", ""), "event_start": e.get("start", ""), "event_end": e.get("end", ""),
        "control": c.label if c else "", "issues": rv.get("issues", []), "summary": rv.get("summary", ""),
        "warnings": rv.get("warnings", []), "event_options": rv.get("event_options", []),
    }


# ---------------------------------------------------------------------------------------- 데이터 길잡이
def _ref_last_month() -> int:
    t = date.today()
    return ym_add(t.year * 100 + t.month, -2)   # 센터 데이터는 보통 1~2개월 늦게 적재


def _months_between(d0: date, d1: date) -> list[int]:
    out, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append(y * 100 + m)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _mtext(months: list[int]) -> str:
    months = sorted(set(months))
    if not months:
        return "-"
    runs, cur = [], [months[0]]
    for m in months[1:]:
        if m == ym_add(cur[-1], 1):
            cur.append(m)
        else:
            runs.append(cur)
            cur = [m]
    runs.append(cur)
    txt = ", ".join(ym_label(r[0]) if len(r) == 1 else f"{ym_label(r[0])}~{ym_label(r[-1])}" for r in runs)
    return f"{txt} ({len(months)}개 파일)"


def _item(did, need, months, cond, why, alt=None):
    return {"id": did, "ids": [did] + (alt or []), "name": dataset_name(did), "need": need,
            "how": f"<b>기간</b> {_mtext(months)} · <b>조건</b> {cond}", "why": why, "status": "missing"}


def data_checklist(mode: str, rv: dict) -> list[dict]:
    items: list[dict] = []
    r = rv.get("region_sel")
    last = _ref_last_month()
    if mode == "trade_area":
        i = rv.get("industry_sel")
        last24 = [ym_add(last, -k) for k in range(23, -1, -1)]
        last12 = last24[12:]
        biz = f"업종={i.name}" if i else "업종 조건 없음"
        items += [
            _item("1-08", "recommended", last24, f"{biz} (지역 조건 없이 전체 — 다른 지역과 비교에 필요)",
                  "최신 월별 매출·전년 대비·고객 구성·지역 비교(없으면 허브 746 실데이터 2017~2021로 분석)", ["1-07"]),
            _item("1-12", "recommended", last12, f"{biz}, 지역={', '.join(r.bjd) if r else '-'}",
                  "요일별 하루 평균 매출 지수", ["1-11"]),
            _item("1-14", "recommended", last12, f"{biz}, 지역={', '.join(r.bjd) if r else '-'}",
                  "시간대별 매출 비중·피크 시간", ["1-13"]),
            _item("4-11", "recommended", last24[-3:], f"행정동={', '.join(r.emd) if r else '-'}",
                  "시간대별 방문·거주·근무 인구(최근 3개월, 없으면 허브 748 월별 실데이터)", ["4-10"]),
            _item("3-03", "recommended", [last], "조건 없음(정류소 좌표)", "반경 내 정류소 위치"),
            _item("3-04", "recommended", [last], "조건 없음", "정류소 이용량(대중교통 접근성)"),
        ]
        items += [
            {"id": "hub:746", "ids": ["hub:746"], "name": "업종별 카드 매출·매장수 — 제주데이터허브", "need": "auto",
             "how": "오름에 포함(2017.01~2021.05 실데이터)", "why": "매출 추이·고객 구성·지역 비교", "status": "auto"},
            {"id": "hub:748", "ids": ["hub:748"], "name": "유동인구-카드매출 매쉬업 — 제주데이터허브", "need": "auto",
             "how": "오름에 포함(2017.01~2021.05 실데이터)", "why": "월별 방문인구·방문인구 대비 매출", "status": "auto"},
            {"id": "hub:793", "ids": ["hub:793"], "name": "상가(상권) 정보 — 제주데이터허브", "need": "auto",
             "how": "오름에 포함(2025.09 기준 57,592개 점포)", "why": "경쟁 점포 수·위치", "status": "auto"},
            {"id": "hub:583", "ids": ["hub:583"], "name": "읍면동 일별 유동인구 — 제주데이터허브 Open API", "need": "auto",
             "how": "Agent가 Open API를 직접 호출(키 없으면 스냅샷)", "why": "최근 2년 방문인구 추이·연령 구성", "status": "auto"},
        ]
    elif mode == "event_impact":
        e = rv.get("event") or {}
        try:
            s, en = date.fromisoformat(e.get("start")), date.fromisoformat(e.get("end") or e.get("start"))
        except (TypeError, ValueError):
            s = en = None
        if s:
            win = _months_between(s - timedelta(days=21), en + timedelta(days=7))
            evm = _months_between(s, en)
            prevm = [ym_add(m, -12) for m in evm]
            pop_id = "4-11" if s >= date(2025, 5, 1) else "4-10"
            emds = ", ".join((r.emd if r else []) + ((rv.get("control_sel").emd) if rv.get("control_sel") else []))
            items += [
                _item(pop_id, "recommended", win, f"행정동={emds or '-'} (행사지역+비교지역)",
                      "시간대별 방문인구·붐비는 시각(없으면 허브 583 일별 실데이터로 분석)", ["4-10", "4-11"]),
                _item("3-03", "recommended", [win[-1]], "조건 없음", "행사장 반경 정류소 찾기(없으면 허브 612)"),
                _item("3-04", "recommended", win, "조건 없음", "정류소 시간대별 승차·셔틀 시각(없으면 허브 613 일별)"),
                _item("1-08", "recommended", evm + prevm, "지역 조건 없이 전체(비교지역 포함)",
                      "업종별 매출 전년 동월 대비·추정 추가 소비액(월 단위)", ["1-07"]),
                _item("5-02", "recommended", evm, "조건 없음", "행사일 날씨(강수·기온)"),
            ]
        items += [
            {"id": "hub:761", "ids": ["hub:761"], "name": "축제행사 정보 — 제주데이터허브(VISIT JEJU)", "need": "auto",
             "how": "오름에 포함(행사명·기간 검색)", "why": "행사 기간 자동 확인", "status": "auto"},
            {"id": "hub:583", "ids": ["hub:583"], "name": "읍면동 일별 유동인구 — 제주데이터허브 Open API", "need": "auto",
             "how": "Agent가 Open API를 직접 호출(키 없으면 대표 시나리오 스냅샷 사용)", "why": "일별 방문인구·이중차분·연령 구성",
             "status": "auto"},
            {"id": "hub:613", "ids": ["hub:612", "hub:613"], "name": "정류소 정보·정류소별 일일 이용 — 제주데이터허브 Open API",
             "need": "auto", "how": "오름에 포함(2024.11 행사·기준선 날짜 스냅샷)", "why": "행사장 인근 정류소 승차 증가",
             "status": "auto"},
        ]
    elif mode == "transit":
        mon = [ym_add(last, -1), last]
        items += [
            _item("3-03", "recommended", [last], "조건 없음", "정류소 이름·좌표(정류소 단위 진단)"),
            _item("3-04", "recommended", mon, "조건 없음(정류소별 시간 단위)", "정류소별 승하차·요일×시간 패턴"),
            _item("3-02", "recommended", mon, "조건 없음", "노선별 이용 순위"),
        ]
        items.append({"id": "hub:613", "ids": ["hub:612", "hub:613"], "name": "정류소 정보·정류소별 일일 이용 — 제주데이터허브 Open API",
                      "need": "auto", "how": "오름에 포함(스냅샷 날짜)", "why": "지역 정류소 순위·저이용 정류소", "status": "auto"})
        items.append({"id": "hub:512", "ids": ["hub:512"], "name": "일별 시간대별 버스 승차 — 제주데이터허브", "need": "auto",
                      "how": "오름에 포함(2023.12~2025.10 실데이터, 제주 전역)", "why": "월별 추이·요일×시간대·이용자 구성",
                      "status": "auto"})
    return items


def coverage_text(ld) -> str:
    for attr in ("coverage_text", "coverage"):
        v = getattr(ld, attr, None)
        if isinstance(v, str) and v:
            return v
    df = getattr(ld, "df", None)
    try:
        if df is not None and "base_year_month" in df:
            m = pd.to_numeric(df["base_year_month"].astype(str).str.slice(0, 6), errors="coerce").dropna().astype(int)
            return f"{ym_label(m.min())}~{ym_label(m.max())}"
        if df is not None and "base_date" in df:
            d = pd.to_datetime(df["base_date"].astype(str).str.slice(0, 8), format="%Y%m%d", errors="coerce").dropna()
            return f"{d.min():%Y.%m.%d}~{d.max():%Y.%m.%d}"
    except Exception:
        pass
    return "-"


# ---------------------------------------------------------------------------------------- 공개 데이터·버스
def public_bundle(mode: str, rv: dict) -> dict:
    out: dict = {"data": {}, "labels": {}}
    try:
        from ..hub import public_data as P
    except Exception:
        return out
    if mode == "trade_area":
        try:
            out["stores"] = P.stores()
            out["labels"]["hub:793"] = HUB_NAMES["hub:793"]
        except Exception:
            pass
        try:
            cm = P.card_mashup()
            if cm is not None and len(cm):
                out["data"]["hub:746"] = cm
                out["labels"]["hub:746"] = HUB_NAMES["hub:746"]
        except Exception:
            pass
        try:
            cp = P.card_pop_mashup()
            if cp is not None and len(cp):
                out["data"]["hub:748"] = cp
                out["labels"]["hub:748"] = HUB_NAMES["hub:748"]
        except Exception:
            pass
    log = out.setdefault("log", [])
    if mode == "trade_area":
        r = rv.get("region_sel")
        if r is not None and r.emd:
            end = POP583_LAST
            pop = _hub_pop(r.emd, date(end.year - 2, end.month, end.day) + timedelta(days=1), end, log)
            if pop is not None and len(pop):
                out["data"]["hub:583"] = pop
                out["labels"]["hub:583"] = HUB_NAMES["hub:583"]
    if mode == "event_impact":
        out["labels"]["hub:761"] = HUB_NAMES["hub:761"]
        ev, r, c = rv.get("event") or {}, rv.get("region_sel"), rv.get("control_sel")
        try:
            s, en = date.fromisoformat(ev.get("start")), date.fromisoformat(ev.get("end") or ev.get("start"))
        except (TypeError, ValueError):
            s = en = None
        if s and r is not None:
            emds = list(dict.fromkeys((r.emd or []) + ((c.emd or []) if c else [])))
            pop = _hub_pop(emds, s - timedelta(days=21), en + timedelta(days=7), log)
            ly = _hub_pop(r.emd or [], s - timedelta(days=364), en - timedelta(days=364), log)   # 52주 전 같은 요일
            if pop is not None and ly is not None:
                pop = pd.concat([pop, ly], ignore_index=True).drop_duplicates()
                pop.attrs["daily"] = True
            if pop is not None and len(pop):
                out["data"]["hub:583"] = pop
                out["labels"]["hub:583"] = HUB_NAMES["hub:583"]
    if mode in ("event_impact", "transit"):
        try:
            out["stops612"] = P.stops612()
            out["usage613"] = P.usage613()
            out["labels"]["hub:612"] = HUB_NAMES["hub:612"]
            out["labels"]["hub:613"] = HUB_NAMES["hub:613"]
        except Exception:
            pass
    if mode == "transit":
        try:
            b = P.bus_daily_timeband()
            if b is not None and len(b):
                out["data"]["hub:512"] = b
                out["labels"]["hub:512"] = HUB_NAMES["hub:512"]
        except Exception:
            pass
    return out


def _hub_pop(emd_names: list[str], start: date, end: date, log: list | None = None) -> "pd.DataFrame | None":
    """허브 583(읍면동 일별 유동인구): 번들 스냅샷이 기간을 덮으면 그것을, 아니면 허브 키가 있을 때 Open API를 직접 호출.

    emd_names 는 유동인구 형식('서귀포시 대정읍'). 반환은 4-11 형식(일 단위, attrs['daily']=True).
    """
    from ..hub import public_data as P

    need_days = (end - start).days + 1
    frames, missing = [], []
    try:
        snap = P.pop583()
    except Exception:
        snap = None
    for nm in emd_names:
        got = None
        if snap is not None and len(snap):
            d = snap[snap["emd_nm"] == nm]
            dd = pd.to_datetime(d["base_date"], format="%Y%m%d", errors="coerce")
            d = d[(dd >= pd.Timestamp(start)) & (dd <= pd.Timestamp(end))]
            if d["base_date"].nunique() >= need_days * 0.9:
                got = d
        if got is None:
            missing.append(nm)
        else:
            frames.append(got)
    if missing and settings.hub_project_key:
        try:
            from ..hub.client import HubClient

            hc = HubClient(settings.hub_project_key)
            for nm in missing:
                emd = nm.split(" ")[-1]
                r = hc.fetch("583", {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "emd": emd},
                             max_pages=300)
                if r.records:
                    frames.append(P.to_pop_frame(pd.DataFrame(r.records)))
                    if log is not None:
                        log.append(f"허브 Open API 583 호출: {emd} {start:%Y.%m.%d}~{end:%Y.%m.%d} "
                                   f"({r.api_calls}회 호출, 캐시 {r.from_cache}쪽)")
        except Exception as e:  # 키·네트워크·쿼터 문제는 분석을 막지 않음
            if log is not None:
                log.append(f"허브 Open API 583 호출 실패({type(e).__name__}) — 있는 자료로 분석")
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    out.attrs["daily"] = True
    return out


def region_event_days(sel, max_days: int = 10) -> dict:
    """선택 지역(법정동)에서 열린 짧은 행사(취소 제외, max_days일 이하)의 날짜 → 행사명. 버스 평시 진단의 제외일."""
    if sel is None or not getattr(sel, "bjd", None):
        return {}
    try:
        from ..hub import public_data as P

        f = P.festivals()
    except Exception:
        return {}
    out: dict = {}
    want = set(sel.bjd)
    for _, r in f.iterrows():
        b = r.get("bjd")
        if not isinstance(b, (list, tuple)) or not want & set(b) or r.get("cancelled") or int(r.get("days") or 99) > max_days:
            continue
        for d in pd.date_range(r["start"], r["end"]).date:
            out.setdefault(d, str(r["name"]))
    return out


def bus_inputs(data: dict, pub: dict):
    stops = data.get("3-03")
    usage = data.get("3-04")
    if stops is not None and "base_date" in stops.columns:
        stops = stops.sort_values("base_date").drop_duplicates("bus_station_id", keep="last")
    if stops is not None and usage is not None:
        return stops, usage, "3-04", "3-03"
    # 센터 자료가 없으면 허브 Open API 스냅샷(612 정류소 + 613 정류소별 일일 이용)
    hs, hu = pub.get("stops612"), pub.get("usage613")
    if hs is not None and hu is not None and len(hs) and len(hu):
        return hs, hu, "hub:613", "hub:612"
    return stops, usage, "3-04", "3-03"


def count_stores(stores: pd.DataFrame, sel: RegionSel, ind: IndustrySel) -> float:
    if stores is None or sel is None:
        return float("nan")
    s = stores
    bcol = "법정동명" if "법정동명" in s.columns else ("bjd" if "bjd" in s.columns else None)
    if bcol is None:
        return float("nan")
    s = s[s[bcol].isin(sel.bjd)]
    if "bc_biz_cd" in s.columns and ind.code:
        s = s[s["bc_biz_cd"].astype(str) == ind.code]
    elif ind.store_keywords:
        import re

        pat = "|".join(map(re.escape, ind.store_keywords))
        cols = [c for c in ("상권업종소분류명", "표준산업분류명", "상권업종중분류명", "sub", "std", "mid") if c in s.columns]
        m = pd.Series(False, index=s.index)
        for c in cols:
            m |= s[c].astype(str).str.contains(pat, regex=True, na=False)
        s = s[m]
    return float(len(s))


def context_text(mode: str, rv: dict, res) -> str:
    lines = [f"- 분석 제목: {res.title}"]
    for k, v in res.params.items():
        if isinstance(v, (list, tuple)):
            v = ", ".join(map(str, v))
        lines.append(f"- {k}: {v}")
    if rv.get("summary"):
        lines.append(f"- 해석된 조건: {rv['summary']}")
    lines.append("- 사용 데이터: " + ", ".join(dict.fromkeys(dataset_name(s) for s in res.used_sources)))
    return "\n".join(lines)
