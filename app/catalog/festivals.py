"""축제·행사: 허브 [761] VISIT JEJU 축제행사 목록 + 장소(법정동·행정동·좌표) 큐레이션 + 자연어 검색.

[761] 원본에는 장소 정보가 없으므로, 제주의 주요 반복 개최 축제 장소를 아래 CURATED 표로 정리했다.
- confidence: high(개최지가 고정·널리 알려짐) / medium(대표 장소, 연도별 변동 가능) / low(대략적 위치)
- 좌표는 대표 지점 근사치(소수 4자리, 수백 m 오차 가능). 분석 반경 설정용이며 지도 표시용 정밀 좌표가 아니다.
- 실제 해당 연도 개최지는 주최 측 공고로 확인해야 한다(보고서에 '장소 큐레이션(추정)' 표기 권장).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache

# (id, 대표명, 검색키[], 법정동[], 대표 행정동/읍면, 장소, 경도, 위도, 대표 개최월[], confidence, 비고)
_CURATED_ROWS = [
    ("bangeo", "최남단 방어축제", ["최남단방어축제", "방어축제", "모슬포방어"], ["대정읍"], "대정읍", "모슬포항 일대(대정읍 하모리)",
     126.2516, 33.2166, [11, 12], "high", "매년 11월 말~12월 초"),
    ("tamna", "탐라문화제", ["탐라문화제"], ["건입동", "일도일동", "삼도이동"], "건입동", "탑동광장·산지천 일대(제주시 원도심)",
     126.5254, 33.5156, [10], "medium", "주 무대는 연도별로 탑동·산지천·신산공원 등 변동"),
    ("chilsimni", "서귀포 칠십리축제", ["칠십리축제", "서귀포칠십리"], ["서귀동"], "천지동", "서귀포 자구리공원·천지연 일대(서귀동)",
     126.5640, 33.2440, [10], "medium", "서귀포 구도심(천지동·정방동) 일대"),
    ("seongsan_sunrise", "성산일출축제", ["성산일출축제", "일출축제"], ["성산읍"], "성산읍", "성산일출봉 일대(성산읍 성산리)",
     126.9400, 33.4590, [12, 1], "high", "12월 31일~1월 1일(2024년 취소)"),
    ("deulbul", "제주들불축제", ["들불축제", "제주들불"], ["애월읍"], "애월읍", "새별오름(애월읍 봉성리)",
     126.3570, 33.3655, [3], "high", "2024년 미개최"),
    ("cherry", "제주왕벚꽃축제", ["왕벚꽃축제", "제주왕벚꽃", "왕벚꽃"], ["삼도일동"], "삼도1동", "전농로 일대(삼도일동)",
     126.5180, 33.5070, [3, 4], "medium", "애월읍 장전리 행사를 병행한 연도 있음"),
    ("canola_walk", "서귀포 유채꽃 국제걷기대회", ["유채꽃국제걷기", "유채꽃걷기", "서귀포유채꽃"], ["표선면"], "표선면",
     "녹산로·조랑말체험공원 일대(표선면 가시리)", 126.7410, 33.3840, [3, 4], "medium", ""),
    ("hallim_park", "한림공원 꽃축제", ["한림공원"], ["한림읍"], "한림읍", "한림공원(한림읍 협재리)", 126.2393, 33.3893,
     [2, 3, 4, 6, 7, 11], "high", "매화·튤립·수국·연꽃·국화 등 계절별 축제"),
    ("haenyeo", "제주해녀축제", ["해녀축제"], ["구좌읍"], "구좌읍", "제주해녀박물관 일대(구좌읍 하도리)", 126.8640, 33.5216,
     [9, 10], "high", ""),
    ("seongeup", "제주성읍마을 전통민속재현축제", ["성읍마을", "성읍민속", "전통민속재현", "전통민속재연"], ["표선면"], "표선면",
     "성읍민속마을(표선면 성읍리)", 126.8013, 33.3866, [10, 11], "high", ""),
    ("ihotewoo", "이호테우축제", ["이호테우축제"], ["이호일동"], "이호동", "이호테우해변(이호일동)", 126.4528, 33.4977,
     [7, 8], "high", ""),
    ("samyang_sand", "삼양검은모래축제", ["검은모래축제", "삼양검은모래"], ["삼양이동"], "삼양동", "삼양해수욕장 일대",
     126.5862, 33.5257, [7], "medium", ""),
    ("dodu", "도두오래물축제", ["오래물축제", "도두오래물"], ["도두일동"], "도두동", "도두동 오래물 일대", 126.4675, 33.5079,
     [8], "medium", ""),
    ("sanji", "산지천축제", ["산지천축제"], ["건입동", "일도일동"], "건입동", "산지천 일대", 126.5277, 33.5137, [9], "medium", ""),
    ("bomok", "보목자리돔축제", ["자리돔축제", "보목자리돔"], ["보목동"], "송산동", "보목포구(보목동)", 126.5978, 33.2463,
     [5, 6], "high", ""),
    ("soesokkak", "쇠소깍축제", ["쇠소깍축제"], ["하효동"], "효돈동", "쇠소깍(하효동)", 126.6236, 33.2524, [9], "high", ""),
    ("chilseonnyeo", "중문칠선녀축제", ["칠선녀축제", "중문칠선녀"], ["중문동"], "중문동", "천제연폭포 일대(중문동)",
     126.4180, 33.2530, [10], "medium", ""),
    ("yerae", "예래생태한가위축제", ["예래생태", "예래한가위"], ["상예동", "하예동"], "예래동", "예래동 일대", 126.3900, 33.2500,
     [9], "low", ""),
    ("galchi", "서귀포은갈치축제", ["은갈치축제", "갈치축제"], ["서귀동"], "천지동", "서귀포항 일대(서귀동)", 126.5650, 33.2400,
     [10], "medium", ""),
    ("chuja", "추자도 참굴비 대축제", ["참굴비", "추자도참굴비", "굴비축제"], ["추자면"], "추자면", "추자항 일대(추자면)",
     126.2994, 33.9621, [10], "high", ""),
    ("udo_sora", "우도소라축제", ["우도소라축제", "소라축제"], ["우도면"], "우도면", "우도(우도면)", 126.9517, 33.5060, [4],
     "high", ""),
    ("honinji", "혼인지축제", ["혼인지"], ["성산읍"], "성산읍", "혼인지(성산읍 온평리)", 126.9020, 33.4110, [5, 6, 10],
     "medium", "혼인지 수국축제 포함"),
    ("ipchun", "탐라국입춘굿", ["입춘굿", "탐라국입춘"], ["삼도이동"], "삼도2동", "제주목관아·관덕정 일대(삼도이동)",
     126.5219, 33.5134, [2], "high", ""),
    ("mokgwana", "제주목관아 야간개장", ["목관아", "제주목관아"], ["삼도이동"], "삼도2동", "제주목관아(삼도이동)",
     126.5219, 33.5134, [5, 6, 7, 8, 9, 10], "high", ""),
    ("hueree", "휴애리 꽃축제", ["휴애리"], ["남원읍"], "남원읍", "휴애리자연생활공원(남원읍 신례리)", 126.6225, 33.3070,
     [1, 2, 3, 4, 6, 9, 10, 11, 12], "high", "매화·수국·핑크뮬리·동백 등 계절별 축제"),
    ("sanghyowon", "상효원 꽃축제", ["상효원", "상효수목원"], ["상효동"], "영천동", "상효원 수목원(상효동)", 126.5990, 33.2840,
     [3, 4, 6, 9, 10], "medium", ""),
    ("gapado", "가파도 청보리축제", ["가파도청보리", "가파도"], ["대정읍"], "대정읍", "가파도(대정읍 가파리)", 126.2735, 33.1700,
     [4, 5], "high", ""),
    ("boromwat", "보롬왓 꽃축제", ["보롬왓"], ["표선면"], "표선면", "보롬왓(표선면 성읍리)", 126.7630, 33.4060, [3, 4, 5, 6, 10],
     "medium", "튤립·메밀 등"),
    ("981park", "9.81파크 이벤트", ["981파크", "9.81파크", "981"], ["애월읍"], "애월읍", "9.81파크(애월읍 어음리)",
     126.3604, 33.3860, [], "high", ""),
    ("snoopy", "스누피가든 이벤트", ["스누피가든"], ["구좌읍"], "구좌읍", "스누피가든(구좌읍 송당리)", 126.7804, 33.4434, [],
     "high", ""),
    ("manorblanc", "마노르블랑 꽃축제", ["마노르블랑"], ["안덕면"], "안덕면", "마노르블랑(안덕면 덕수리)", 126.2980, 33.2520,
     [3, 4, 9, 10, 11, 12], "medium", ""),
    ("herb_dongsan", "제주허브동산 축제", ["허브동산"], ["표선면"], "표선면", "제주허브동산(표선면 표선리)", 126.8380, 33.3220,
     [], "medium", ""),
    ("folk_village", "제주민속촌 행사", ["제주민속촌", "민속촌"], ["표선면"], "표선면", "제주민속촌(표선면 표선리)",
     126.8423, 33.3229, [], "high", ""),
    ("garlic_expo", "대정암반수마농박람회", ["마농박람회", "암반수마농"], ["대정읍"], "대정읍", "대정읍 일대", 126.2550, 33.2300,
     [6], "medium", ""),
    ("aewol_expo", "애월읍 농수축 박람회", ["애월읍농수축", "농수축박람회"], ["애월읍"], "애월읍", "애월읍 일대", 126.3300, 33.4620,
     [11], "medium", "장소 좌표는 읍 중심 근사"),
    ("pyoseon_sand", "표선해변 하얀모래축제", ["하얀모래축제", "표선해변"], ["표선면"], "표선면", "표선해수욕장(표선면 표선리)",
     126.8370, 33.3260, [7, 8], "high", ""),
    ("geumneung", "금능원담축제", ["원담축제", "금능원담"], ["한림읍"], "한림읍", "금능해수욕장 일대(한림읍 금능리)",
     126.2348, 33.3900, [8], "high", ""),
    ("gangjeong", "강정마을 생태축제", ["강정마을생태", "강정생태"], ["강정동"], "대천동", "강정마을(강정동)", 126.4950, 33.2290,
     [8], "medium", ""),
    ("woljeong", "월정 한모살 축제", ["한모살", "월정한모살"], ["구좌읍"], "구좌읍", "월정리 해변(구좌읍)", 126.7960, 33.5560,
     [7], "high", ""),
    ("jocheon_spring", "조천리 용천수 문화곳간 축제", ["용천수문화곳간", "조천리용천수"], ["조천읍"], "조천읍", "조천리(조천읍)",
     126.6340, 33.5390, [9], "high", ""),
    ("hwabuk_port", "화북 포구문화제", ["포구문화제", "화북포구"], ["화북일동"], "화북동", "화북포구(화북일동)", 126.5680, 33.5220,
     [9], "medium", ""),
    ("sinsan_light", "신산 도채비 빛 축제", ["도채비빛축제", "신산도채비", "신산공원"], ["일도이동"], "일도2동", "신산공원(일도이동)",
     126.5340, 33.5100, [10], "medium", ""),
    ("gomaro", "고마로 마문화축제", ["고마로", "마문화축제"], ["일도이동"], "일도2동", "고마로 일대(일도이동)", 126.5400, 33.5050,
     [10], "medium", ""),
    ("gosan_site", "제주 고산리 유적·수월봉 행사", ["고산리유적", "수월봉"], ["한경면"], "한경면", "고산리·수월봉(한경면)",
     126.1680, 33.2960, [4, 5], "high", ""),
    ("uigwi", "제주의귀말축제", ["의귀말축제", "의귀말"], ["남원읍"], "남원읍", "의귀리(남원읍)", 126.7200, 33.3200, [10],
     "medium", ""),
    ("gosari", "한라산청정고사리축제", ["고사리축제"], ["남원읍"], "남원읍", "남원읍 중산간 일대", 126.7000, 33.3500, [4],
     "medium", ""),
    ("saeyeon", "새연교 공연·음악분수", ["새연교"], ["서귀동"], "천지동", "새연교·새섬 일대(서귀포항)", 126.5590, 33.2390,
     [5, 6, 7, 8, 9, 10], "medium", ""),
    ("seonganolle", "성안올레 걷기축제", ["성안올레"], ["삼도이동", "일도일동"], "삼도2동", "제주시 원도심 일대", 126.5230, 33.5130,
     [9, 11], "low", ""),
    ("jungmun_day", "중문데이", ["중문데이"], ["중문동", "색달동"], "중문동", "중문관광단지 일대", 126.4120, 33.2500, [11], "low", ""),
]


@dataclass
class FestivalMatch:
    name: str
    start: str | None = None          # 'YYYY-MM-DD' (761 목록 기준)
    end: str | None = None
    bjd: list[str] = field(default_factory=list)       # 카드 데이터 법정동
    region: str = ""                                   # 대표 행정동/읍면 이름
    hjd: list[str] = field(default_factory=list)
    emd_nm: list[str] = field(default_factory=list)    # 유동인구 emd_nm 형식
    venue: str = ""
    lon: float | None = None
    lat: float | None = None
    confidence: str = "low"
    score: float = 0.0
    source: str = ""                                   # '761+curated' | '761' | 'curated'
    curated_id: str | None = None
    year: int | None = None
    cancelled: bool = False
    typical_months: list[int] = field(default_factory=list)
    other_editions: list[dict] = field(default_factory=list)
    note: str = ""

    @property
    def start_date(self) -> date | None:
        return date.fromisoformat(self.start) if self.start else None

    @property
    def end_date(self) -> date | None:
        return date.fromisoformat(self.end) if self.end else None

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_name(s: str) -> str:
    """'제63회 탐라문화제', '2024 최남단 방어축제(2024년 취소)' → '탐라문화제', '최남단방어축제'."""
    s = str(s or "")
    s = re.sub(r"[\(\[](?=[^\)\]]*(취소|삭제|미개최|중복|년|월|\d))[^\)\]]*[\)\]]", " ", s)
    s = re.sub(r"제\s*\d+\s*회", " ", s)
    s = re.sub(r"(19|20)\d{2}\s*년?", " ", s)
    s = re.sub(r"'\d{2}\s*년?|\d{2}\s*년", " ", s)
    s = re.sub(r"\d+\s*회", " ", s)
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    return s.lower()


@lru_cache(maxsize=1)
def curated() -> list[dict]:
    from . import regions as R

    out = []
    for (cid, name, keys, bjd, region, venue, lon, lat, months, conf, note) in _CURATED_ROWS:
        hjd, emd = [], []
        for b in bjd:
            e = R.card_bjd_to_pop_emd(b)
            if e and e["emd_nm"] not in emd:
                emd.append(e["emd_nm"])
                hjd.append(e["emd_nm"].split()[-1])
        out.append(dict(id=cid, name=name, keys=[normalize_name(k) for k in keys] + [normalize_name(name)],
                        bjd=list(bjd), region=region, hjd=hjd, emd_nm=emd, venue=venue, lon=lon, lat=lat,
                        typical_months=list(months), confidence=conf, note=note))
    return out


def _match_curated(norm: str) -> dict | None:
    best, best_len = None, 0
    for c in curated():
        for k in c["keys"]:
            if len(k) >= 2 and k in norm and len(k) > best_len:
                best, best_len = c, len(k)
    return best


def attach_locations(df):
    """761 정리본 DataFrame 에 큐레이션 장소 컬럼 추가: norm_name, curated_id, venue, bjd(list), region, lon, lat,
    confidence. 큐레이션이 없으면 주최·주관·이름에서 지역을 추정(confidence='low')."""
    import pandas as pd

    from . import regions as R

    df = df.copy()
    rows = []
    for _, r in df.iterrows():
        n = normalize_name(r["name"])
        c = _match_curated(n)
        if c:
            rows.append(dict(norm_name=n, curated_id=c["id"], venue=c["venue"], bjd=list(c["bjd"]), region=c["region"],
                             lon=c["lon"], lat=c["lat"], confidence=c["confidence"]))
            continue
        text = " ".join(str(r.get(k) or "") for k in ("name", "host", "organizer", "sponsor"))
        m = [x for x in R.resolve_region(text, fine_only=True) if x.level in ("bjd", "hjd", "alias", "ri")]
        if m:
            x = m[0]
            rows.append(dict(norm_name=n, curated_id=None, venue="", bjd=list(x.bjd), region=(x.hjd or [x.name])[0],
                             lon=x.lon, lat=x.lat, confidence="low"))
        else:
            rows.append(dict(norm_name=n, curated_id=None, venue="", bjd=[], region="", lon=None, lat=None,
                             confidence=None))
    extra = pd.DataFrame(rows, index=df.index)
    return pd.concat([df, extra], axis=1)


@lru_cache(maxsize=1)
def _events():
    try:
        from ..hub.public_data import festival_events

        return attach_locations(festival_events())
    except Exception:  # 공개 데이터 미생성 시 큐레이션만 사용
        return None


def _fmt(d) -> str | None:
    try:
        return None if d is None or str(d) == "NaT" else d.strftime("%Y-%m-%d")
    except AttributeError:
        return str(d)[:10]


def _score(key: str, norm_text: str) -> float:
    if len(key) < 2:
        return 0.0
    if key in norm_text:
        return 0.9 + min(0.08, len(key) / 100)
    if len(norm_text) >= 3 and norm_text in key:
        return 0.85
    sm = difflib.SequenceMatcher(None, key, norm_text, autojunk=False)
    m = sm.find_longest_match(0, len(key), 0, len(norm_text))
    frac = m.size / len(key)
    return 0.55 + 0.3 * frac if frac >= 0.7 and m.size >= 3 else 0.0


def search_festival(text: str, year: int | None = None, limit: int = 5) -> list[FestivalMatch]:
    """자연어(축제명 일부·문장)로 축제 찾기. year(또는 문장 속 연도)가 있으면 그 해의 761 일정을 우선.
    '제63회', '2024' 같은 회차·연도 표기는 무시하고 비교한다. 장소는 큐레이션 표(없으면 이름·주최로 추정)."""
    if not text:
        return []
    if year is None:
        m = re.search(r"(20\d{2})\s*년?", str(text))
        year = int(m.group(1)) if m else None
    nt = normalize_name(text)
    ev = _events()
    groups: dict[str, dict] = {}
    for c in curated():
        sc = max(_score(k, nt) for k in c["keys"])
        if sc > 0:
            groups[f"c:{c['id']}"] = dict(score=sc, curated=c, rows=[])
    if ev is not None:
        for idx, r in ev.iterrows():
            nn = r["norm_name"]
            sc = _score(nn, nt) if len(nn) >= 3 else 0.0
            key = f"c:{r['curated_id']}" if r["curated_id"] else f"e:{nn}"
            if sc <= 0 and key not in groups:
                continue
            g = groups.setdefault(key, dict(score=sc, curated=None, rows=[]))
            g["score"] = max(g["score"], sc)
            g["rows"].append(r)
            if r["curated_id"] and g["curated"] is None:
                g["curated"] = next((c for c in curated() if c["id"] == r["curated_id"]), None)
    out: list[FestivalMatch] = []
    from . import regions as R

    for key, g in groups.items():
        c, rows = g["curated"], g["rows"]
        rows = sorted(rows, key=lambda r: r["start"])
        pick = None
        if rows:
            cand = rows
            if year is not None:
                cand = [r for r in rows if r["start"].year == year or r["end"].year == year]
            if cand:  # 같은 묶음(예: 한림공원 꽃축제) 안에서는 질의와 이름이 더 맞는 행 → 최근 행
                pick = max(cand, key=lambda r: (_score(r["norm_name"], nt), r["start"]))
        score = g["score"]
        note = ""
        if year is not None:
            if pick is not None:
                score += 0.02
            else:
                note = f"{year}년 일정이 761 목록에 없음(다른 연도 일정 참고)"
        if c:
            fm = FestivalMatch(name=c["name"], bjd=list(c["bjd"]), region=c["region"], hjd=list(c["hjd"]),
                               emd_nm=list(c["emd_nm"]), venue=c["venue"], lon=c["lon"], lat=c["lat"],
                               confidence=c["confidence"], curated_id=c["id"], typical_months=list(c["typical_months"]),
                               source="761+curated" if rows else "curated", note="; ".join(x for x in (c["note"], note) if x))
        else:
            r0 = pick if pick is not None else rows[-1]
            hj, emd = [], []
            for b in r0["bjd"] or []:
                e = R.card_bjd_to_pop_emd(b)
                if e and e["emd_nm"] not in emd:
                    emd.append(e["emd_nm"])
                    hj.append(e["emd_nm"].split()[-1])
            fm = FestivalMatch(name=re.sub(r"\s+", " ", r0["name"]).strip(), bjd=list(r0["bjd"] or []),
                               region=r0["region"] or "", hjd=hj, emd_nm=emd, venue=r0["venue"] or "",
                               lon=r0["lon"], lat=r0["lat"], confidence=r0["confidence"] or "low", source="761",
                               note="장소 큐레이션 없음 — 이름·주최 기관명에서 지역 추정" if r0["bjd"] else
                               "장소 정보 없음 — 지역을 직접 지정하세요")
            if note:
                fm.note = "; ".join(x for x in (fm.note, note) if x)
        if pick is not None:
            fm.start, fm.end = _fmt(pick["start"]), _fmt(pick["end"])
            fm.year = pick["start"].year
            fm.cancelled = bool(pick["cancelled"])
            if not c:
                fm.name = re.sub(r"\s+", " ", pick["name"]).strip()
        else:
            fm.year = year
        fm.other_editions = [dict(name=r["name"], start=_fmt(r["start"]), end=_fmt(r["end"]),
                                  cancelled=bool(r["cancelled"])) for r in rows if pick is None or r is not pick]
        fm.score = round(min(score, 1.0), 4)
        out.append(fm)
    out.sort(key=lambda m: (-m.score, m.start is None, m.name))
    return out[:limit]


def festival_location(name: str) -> dict | None:
    """축제 이름 → 큐레이션 장소(dict) 또는 None."""
    c = _match_curated(normalize_name(name))
    return dict(c) if c else None


def festival_vocab() -> list[str]:
    """플래너 프롬프트용 축제·행사 이름(큐레이션 대표명 우선, 이어서 761 목록 최근순)."""
    names = [c["name"] for c in curated()]
    ev = _events()
    if ev is not None:
        recent = ev[~ev["cancelled"]].sort_values("start", ascending=False)
        for n in recent["name"]:
            n2 = re.sub(r"\s+", " ", re.sub(r"[\(\[][^\)\]]*[\)\]]", "", str(n))).strip()
            if n2 and normalize_name(n2) not in {normalize_name(x) for x in names}:
                names.append(n2)
    return list(dict.fromkeys(names))
