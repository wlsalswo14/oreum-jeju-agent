"""제주 지역 체계: 법정동(카드 데이터) · 행정동(유동인구 데이터) · 7개 권역 · 시군구 + 자연어 지역 인식.

데이터 원천
- 법정동 73개(월평동 2곳 → 74행)·행정동 43개·7개 권역 이름: 데이터 명세서(spec_catalog.json reference)
- 법정동→행정동 매핑, 중심좌표, 리(里) 좌표: 허브 [793] 상가(상권) 정보 점포 위치로 산출(region_data.json)
- 법정동/행정동→7개 권역: **추정값**(estimated=True). 명세서 근거로 확정된 것은 노형동=제주시 중부(서),
  대정읍=서귀포시 서부(공식 예시), 월평동(제주시)=제주시 중부(동), 월평동(서귀포시)=서귀포시 중부뿐이다.
  실제 CSV(1-08/1-12/1-14/4-11)의 dstrct_se 값이 있으면 `learn_dstrct_from_frame()` 으로 덮어쓴다(실데이터 우선).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from .catalog import load_catalog

REGION_DATA_PATH = Path(__file__).with_name("region_data.json")

DISTRICTS7 = ["제주시 동부", "제주시 중부(동)", "제주시 중부(서)", "제주시 서부", "서귀포시 동부", "서귀포시 중부", "서귀포시 서부"]

# 행정동(43) → 7개 권역 추정. confidence: confirmed(명세서·공식답변 근거) / high(지리상 명확) / medium / low
DSTRCT_EST: dict[str, tuple[str, str]] = {
    "구좌읍": ("제주시 동부", "high"), "조천읍": ("제주시 동부", "high"), "우도면": ("제주시 동부", "medium"),
    "일도1동": ("제주시 중부(동)", "high"), "일도2동": ("제주시 중부(동)", "high"), "이도1동": ("제주시 중부(동)", "high"),
    "이도2동": ("제주시 중부(동)", "high"), "삼도1동": ("제주시 중부(동)", "high"), "삼도2동": ("제주시 중부(동)", "high"),
    "용담1동": ("제주시 중부(동)", "medium"), "건입동": ("제주시 중부(동)", "high"), "화북동": ("제주시 중부(동)", "high"),
    "삼양동": ("제주시 중부(동)", "high"), "봉개동": ("제주시 중부(동)", "medium"),
    "아라동": ("제주시 중부(동)", "confirmed"), "오라동": ("제주시 중부(동)", "medium"),
    "용담2동": ("제주시 중부(서)", "medium"), "연동": ("제주시 중부(서)", "high"),
    "노형동": ("제주시 중부(서)", "confirmed"), "외도동": ("제주시 중부(서)", "high"),
    "이호동": ("제주시 중부(서)", "high"), "도두동": ("제주시 중부(서)", "high"),
    "한림읍": ("제주시 서부", "high"), "애월읍": ("제주시 서부", "high"), "한경면": ("제주시 서부", "high"),
    "추자면": ("제주시 서부", "low"),
    "성산읍": ("서귀포시 동부", "high"), "남원읍": ("서귀포시 동부", "high"), "표선면": ("서귀포시 동부", "high"),
    "송산동": ("서귀포시 중부", "high"), "정방동": ("서귀포시 중부", "high"), "중앙동": ("서귀포시 중부", "high"),
    "천지동": ("서귀포시 중부", "high"), "효돈동": ("서귀포시 중부", "high"), "영천동": ("서귀포시 중부", "high"),
    "동홍동": ("서귀포시 중부", "high"), "서홍동": ("서귀포시 중부", "high"), "대륜동": ("서귀포시 중부", "high"),
    "대천동": ("서귀포시 중부", "confirmed"), "중문동": ("서귀포시 중부", "high"), "예래동": ("서귀포시 중부", "high"),
    "대정읍": ("서귀포시 서부", "confirmed"), "안덕면": ("서귀포시 서부", "high"),
}

# 자연어 별칭 → 법정동(카드 데이터 기준). lon/lat 는 대표 지점 근사(없으면 법정동 중심).
ALIASES: dict[str, dict] = {
    "모슬포": dict(bjd=["대정읍"], note="대정읍 하모리·상모리 일대(모슬포항)", lon=126.2516, lat=33.2166),
    "모슬포항": dict(bjd=["대정읍"], note="대정읍 하모리 모슬포항", lon=126.2516, lat=33.2166),
    "가파도": dict(bjd=["대정읍"], note="대정읍 가파리"), "마라도": dict(bjd=["대정읍"], note="대정읍 가파리 마라도"),
    "송악산": dict(bjd=["대정읍"], note="대정읍 상모리"),
    "중문관광단지": dict(bjd=["색달동", "중문동"], note="색달동·중문동 일대(행정동 예래동·중문동)"),
    "중문": dict(bjd=["중문동"], note="중문동(행정동 중문동). 관광단지는 색달동 포함"),
    "성산일출봉": dict(bjd=["성산읍"], note="성산읍 성산리"), "성산포": dict(bjd=["성산읍"]), "성산": dict(bjd=["성산읍"]),
    "제주시청": dict(bjd=["이도이동"], note="제주시청 인근(이도이동, 행정동 이도2동)", lon=126.5312, lat=33.4996),
    "시청": dict(bjd=["이도이동"], note="'시청'은 제주시청(이도이동)으로 해석 — 서귀포시청이면 지역을 명시하세요",
               lon=126.5312, lat=33.4996),
    "서귀포시내": dict(bjd=["서귀동"], note="서귀포 구도심(서귀동, 행정동 정방·중앙·천지·송산동)"),
    "서귀포구도심": dict(bjd=["서귀동"], note="서귀동 일대"), "서귀포원도심": dict(bjd=["서귀동"], note="서귀동 일대"),
    "매일올레시장": dict(bjd=["서귀동"], note="서귀포매일올레시장(서귀동)"), "올레시장": dict(bjd=["서귀동"]),
    "이중섭거리": dict(bjd=["서귀동"]), "천지연": dict(bjd=["서귀동"]), "서귀포항": dict(bjd=["서귀동"]),
    "정방폭포": dict(bjd=["동홍동"]), "쇠소깍": dict(bjd=["하효동"], note="하효동(행정동 효돈동)"),
    "제주공항": dict(bjd=["용담이동"], note="제주국제공항(용담이동, 행정동 용담2동)", lon=126.4930, lat=33.5070),
    "제주국제공항": dict(bjd=["용담이동"], lon=126.4930, lat=33.5070), "공항": dict(bjd=["용담이동"]),
    "신제주": dict(bjd=["연동", "노형동"], note="신제주(연동·노형동)"),
    "구제주": dict(bjd=["일도일동", "이도일동", "삼도이동", "건입동"], note="제주시 원도심"),
    "원도심": dict(bjd=["일도일동", "이도일동", "삼도이동", "건입동"], note="제주시 원도심(관덕정·동문시장·탑동 일대)"),
    "탑동": dict(bjd=["삼도이동", "건입동"], note="탑동광장 일대", lon=126.5226, lat=33.5160),
    "동문시장": dict(bjd=["이도일동"], note="제주동문재래시장(이도일동)"), "제주항": dict(bjd=["건입동"]),
    "산지천": dict(bjd=["건입동", "일도일동"]), "관덕정": dict(bjd=["삼도이동"]), "제주목관아": dict(bjd=["삼도이동"]),
    "함덕": dict(bjd=["조천읍"], note="조천읍 함덕리"), "협재": dict(bjd=["한림읍"], note="한림읍 협재리"),
    "금능": dict(bjd=["한림읍"]), "한림공원": dict(bjd=["한림읍"]), "곽지": dict(bjd=["애월읍"]), "한담": dict(bjd=["애월읍"]),
    "애월항": dict(bjd=["애월읍"]), "하귀": dict(bjd=["애월읍"]), "새별오름": dict(bjd=["애월읍"], lon=126.3570, lat=33.3655),
    "월정": dict(bjd=["구좌읍"]), "세화": dict(bjd=["구좌읍"]), "김녕": dict(bjd=["구좌읍"]), "평대": dict(bjd=["구좌읍"]),
    "종달": dict(bjd=["구좌읍"]), "송당": dict(bjd=["구좌읍"]), "위미": dict(bjd=["남원읍"]), "성읍": dict(bjd=["표선면"]),
    "사계": dict(bjd=["안덕면"]), "화순": dict(bjd=["안덕면"]), "산방산": dict(bjd=["안덕면"]), "용머리해안": dict(bjd=["안덕면"]),
    "오설록": dict(bjd=["안덕면"]), "고산": dict(bjd=["한경면"]), "수월봉": dict(bjd=["한경면"]), "차귀도": dict(bjd=["한경면"]),
    "저지": dict(bjd=["한경면"]), "우도": dict(bjd=["우도면"]), "추자도": dict(bjd=["추자면"]), "추자": dict(bjd=["추자면"]),
    "이호테우": dict(bjd=["이호일동"]), "제주대": dict(bjd=["아라일동"]), "제주대학교": dict(bjd=["아라일동"]),
}

# 접미사(읍·면·동) 없이 써도 인식할 동 이름 기본형(흔한 단어와 겹치는 중앙·천지·오라·대포 등은 제외)
_BASE_WHITELIST = {"노형", "외도", "이호", "도두", "삼양", "화북", "봉개", "건입", "용담", "예래", "강정", "법환", "서홍",
                   "동홍", "효돈", "색달", "보목", "토평", "하효", "신효", "상효", "회수", "하원", "도순", "대륜", "대천",
                   "호근", "서호", "오등", "해안", "용강", "회천", "도련", "도평", "내도", "도남", "영평", "월평", "아라"}
_ISLAND_WORDS = ["제주도 전체", "제주 전체", "제주전역", "제주 전역", "도 전체", "도내 전체", "제주도", "전 지역", "전지역"]
_NUM = {"일": "1", "이": "2", "삼": "3"}


@dataclass
class RegionMatch:
    level: str                       # bjd | hjd | ri | alias | dstrct | city | island
    name: str                        # 표시 이름
    bjd: list[str] = field(default_factory=list)       # 카드 데이터 법정동 이름
    bjd_keys: list[str] = field(default_factory=list)  # '월평동@제주시' 형식(동명이소 구분)
    hjd: list[str] = field(default_factory=list)       # 행정동 짧은 이름(예: 이도2동)
    emd_nm: list[str] = field(default_factory=list)    # 유동인구 emd_nm 형식(예: '제주시 이도2동')
    emd_cd: list[str] = field(default_factory=list)
    city: str | None = None
    dstrct: str | None = None
    dstrct_estimated: bool = True
    lon: float | None = None
    lat: float | None = None
    score: float = 0.0
    matched: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def region_sel_kwargs(self) -> dict:
        """analysis.common.RegionSel 생성 인자."""
        return dict(label=self.name, bjd=list(self.bjd), emd=list(self.emd_nm),
                    center=(self.lon, self.lat) if self.lon is not None else None, city=self.city,
                    district=self.dstrct)


# ----------------------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _data() -> dict:
    cat = load_catalog()
    ref = cat.reference
    derived = json.loads(REGION_DATA_PATH.read_text(encoding="utf-8")) if REGION_DATA_PATH.exists() else {}
    dbjd = {(b["name"], b["city"]): b for b in derived.get("bjd", [])}
    dhjd = {(h["name"], h["city"]): h for h in derived.get("hjd", [])}
    hjd = []
    for e in ref.get("emd43", []):
        city, short = e["name"].split()[0], e["name"].split()[-1]
        d = dhjd.get((short, city), {})
        ds, conf = DSTRCT_EST.get(short, (None, "low"))
        n_h = max(1, d.get("n_stores", 0))
        counts = d.get("bjd_counts") or {b: 1 for b in d.get("bjd", [])}
        # 행정동의 구성 법정동: 행정동 점포의 15% 이상을 차지하거나, 그 법정동 점포의 30% 이상이 이 행정동에 있는 경우
        members = []
        for bname, cnt in counts.items():
            bshare = dbjd.get((bname, city), {}).get("hjd_shares", {}).get(short, 0.0)
            if cnt / n_h >= 0.15 or bshare >= 0.3:
                members.append(bname)
        hjd.append(dict(name=short, full=e["name"], code=e["code"], city=city, lon=d.get("lon"), lat=d.get("lat"),
                        bjd=members, bjd_counts=counts, dstrct=ds, dstrct_confidence=conf,
                        dstrct_estimated=conf != "confirmed", n_stores=d.get("n_stores", 0)))
    hjd_by = {(h["name"], h["city"]): h for h in hjd}
    bjd = []
    for b in ref.get("bjd73", []):
        d = dbjd.get((b["name"], b["city"]), {})
        h = d.get("hjd") or (b["name"] if (b["name"], b["city"]) in hjd_by else None)
        hj = hjd_by.get((h, b["city"]), {})
        ds, conf = hj.get("dstrct"), hj.get("dstrct_confidence", "low")
        if b.get("note") in DISTRICTS7:          # 명세서 비고(월평동)
            ds, conf = b["note"], "confirmed"
        bjd.append(dict(name=b["name"], city=b["city"], key=b["key"], area_type=b["area_type"], code8=d.get("code8"),
                        hjd=h, hjd_shares=d.get("hjd_shares", {h: 1.0} if h else {}), lon=d.get("lon"),
                        lat=d.get("lat"), n_stores=d.get("n_stores", 0), dstrct=ds, dstrct_confidence=conf,
                        dstrct_estimated=conf != "confirmed"))
    for h in hjd:
        if not h["bjd"]:
            h["bjd"] = [b["name"] for b in bjd if b["hjd"] == h["name"] and b["city"] == h["city"]]
    by_name: dict[str, list[dict]] = {}
    for b in bjd:
        by_name.setdefault(b["name"], []).append(b)
    return dict(bjd=bjd, hjd=hjd, ri=derived.get("ri", []), overrides={}, bjd_by_name=by_name,
                bjd_by_key={b["key"]: b for b in bjd}, hjd_by_key={(h["name"], h["city"]): h for h in hjd},
                hjd_by_code={h["code"]: h for h in hjd})


def reload() -> None:
    _data.cache_clear()


def bjd_list() -> list[dict]:
    """법정동 74행(이름 73개 + 월평동 중복). 각 행: name, city, key, hjd, dstrct(추정), lon, lat …"""
    return [dict(b) for b in _data()["bjd"]]


def hjd_list() -> list[dict]:
    """행정동 43개: name(짧은 이름), full('제주시 한림읍'), code, city, bjd[], dstrct(추정), lon, lat."""
    return [dict(h) for h in _data()["hjd"]]


def districts7() -> list[str]:
    return list(DISTRICTS7)


def _find_bjd(name: str, city: str | None = None) -> list[dict]:
    name = name.strip()
    out = [b for b in _data()["bjd_by_name"].get(name, []) if city is None or b["city"] == city]
    if not out and "@" in name:
        b = _data()["bjd_by_key"].get(name)
        out = [b] if b else []
    return out


def get_bjd(name: str, city: str | None = None) -> dict | None:
    r = _find_bjd(name, city)
    return dict(r[0]) if r else None


def normalize_hjd(name: str) -> tuple[str | None, str]:
    """'제주시 일도1동' / '일도1동' / '일도 1동' → (시, '일도1동')."""
    s = re.sub(r"\s+", " ", str(name)).strip()
    city = None
    m = re.match(r"^(제주시|서귀포시)\s*(.+)$", s)
    if m:
        city, s = m.group(1), m.group(2)
    s = re.sub(r"\s+", "", s)
    return city, s


def get_hjd(name_or_code: str) -> dict | None:
    s = str(name_or_code).strip()
    if s in _data()["hjd_by_code"]:
        return dict(_data()["hjd_by_code"][s])
    city, short = normalize_hjd(s)
    if city and (short, city) in _data()["hjd_by_key"]:
        return dict(_data()["hjd_by_key"][(short, city)])
    for h in _data()["hjd"]:
        if h["name"] == short and (city is None or h["city"] == city):
            return dict(h)
    return None


def bjd_to_hjd(name: str, city: str | None = None) -> str | None:
    """법정동 → 행정동(점포 다수결). 예: 이도이동 → 이도2동, 아라일동 → 아라동."""
    b = get_bjd(name, city)
    return b["hjd"] if b else None


def hjd_to_bjd(name: str) -> list[str]:
    h = get_hjd(name)
    return list(h["bjd"]) if h else []


def bjd_to_dstrct(name: str, city: str | None = None) -> tuple[str | None, bool]:
    """법정동 → (7개 권역, estimated). 실데이터에서 학습한 값이 있으면 그 값(estimated=False)."""
    b = get_bjd(name, city)
    if not b:
        return None, True
    ov = _data()["overrides"].get(b["key"]) or _data()["overrides"].get(b["name"])
    if ov:
        return ov, False
    return b["dstrct"], b["dstrct_estimated"]


def hjd_to_dstrct(name: str) -> tuple[str | None, bool]:
    h = get_hjd(name)
    if not h:
        return None, True
    return h["dstrct"], h["dstrct_estimated"]


def dstrct_members(dstrct: str, level: str = "bjd") -> list[str]:
    if level == "hjd":
        return [h["name"] for h in _data()["hjd"] if hjd_to_dstrct(h["full"])[0] == dstrct]
    return [b["name"] for b in _data()["bjd"] if bjd_to_dstrct(b["name"], b["city"])[0] == dstrct]


def card_bjd_to_pop_emd(name: str, city: str | None = None) -> dict | None:
    """카드 법정동(예: 일도일동) → 유동인구 행정동 {'emd_cd','emd_nm'('제주시 일도1동'),'share'}."""
    b = get_bjd(name, city)
    if not b or not b["hjd"]:
        return None
    h = _data()["hjd_by_key"].get((b["hjd"], b["city"]))
    if not h:
        return None
    return dict(emd_cd=h["code"], emd_nm=h["full"], share=float(b["hjd_shares"].get(b["hjd"], 1.0)))


def pop_emd_to_card_bjd(emd: str) -> list[str]:
    """유동인구 행정동('제주시 일도1동' 또는 코드 50110510) → 카드 법정동 목록."""
    return hjd_to_bjd(emd)


def centroid(name: str, level: str = "bjd", city: str | None = None) -> tuple[float, float] | None:
    if level == "hjd":
        h = get_hjd(name)
        return (h["lon"], h["lat"]) if h and h["lon"] is not None else None
    b = get_bjd(name, city)
    return (b["lon"], b["lat"]) if b and b["lon"] is not None else None


def _hav(lon1, lat1, lon2, lat2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def neighbors(name: str, k: int = 4, city: str | None = None, same_type: bool = True) -> list[dict]:
    """가까운 법정동 k개(중심좌표 거리). same_type=True 면 읍면↔읍면, 동↔동끼리 비교(비교·통제지역 후보)."""
    b = get_bjd(name, city)
    if not b or b["lon"] is None:
        return []
    out = []
    for o in _data()["bjd"]:
        if o["key"] == b["key"] or o["lon"] is None or o["name"] in ("우도면", "추자면"):
            continue
        if same_type and o["area_type"] != b["area_type"]:
            continue
        out.append(dict(name=o["name"], city=o["city"], km=round(_hav(b["lon"], b["lat"], o["lon"], o["lat"]), 2)))
    return sorted(out, key=lambda x: x["km"])[:k]


# ---------------------------------------------------------------------------- 실데이터 권역 학습
def set_dstrct_overrides(mapping: dict[str, str]) -> None:
    """법정동(이름 또는 '이름@시') → 권역 실측 매핑 등록(추정값보다 우선)."""
    ov = _data()["overrides"]
    for k, v in mapping.items():
        if v in DISTRICTS7:
            ov[k] = v


def learn_dstrct_from_frame(df, bjd_col: str = "bjd", dstrct_col: str = "dstrct_se", city_col: str | None = "city") -> dict:
    """1-08/1-12/1-14 데이터프레임의 (bjd, dstrct_se) 실제 조합으로 권역 매핑을 학습해 등록하고 반환."""
    if df is None or bjd_col not in df or dstrct_col not in df:
        return {}
    cols = [bjd_col, dstrct_col] + ([city_col] if city_col and city_col in df else [])
    pairs = df[cols].dropna().drop_duplicates()
    mapping: dict[str, str] = {}
    for _, r in pairs.iterrows():
        d = str(r[dstrct_col]).strip()
        name = str(r[bjd_col]).strip()
        if d not in DISTRICTS7:
            continue
        city = str(r[city_col]).strip() if city_col and city_col in r else d.split()[0]
        mapping[f"{name}@{city}"] = d
        if name != "월평동":
            mapping[name] = d
    set_dstrct_overrides(mapping)
    return mapping


# ---------------------------------------------------------------------------- 자연어 인식
def _mk_from_bjd(names: list[str], level: str, label: str, score: float, matched: str, note: str = "",
                 city: str | None = None, lon=None, lat=None) -> RegionMatch:
    rows = []
    for n in names:
        rows += _find_bjd(n, city)
    if not rows:
        rows = []
    hjds, emd_nm, emd_cd, keys = [], [], [], []
    for b in rows:
        keys.append(b["key"])
        # 법정동이 여러 행정동에 걸치면(예: 서귀동→중앙·정방·천지동) 점포 비중 20% 이상 행정동을 모두 포함
        names_h = [b["hjd"]] + [k for k, v in sorted(b["hjd_shares"].items(), key=lambda x: -x[1])
                                if v >= 0.2 and k != b["hjd"]]
        for hn in names_h:
            if not hn:
                continue
            h = _data()["hjd_by_key"].get((hn, b["city"]))
            if h and h["name"] not in hjds:
                hjds.append(h["name"])
                emd_nm.append(h["full"])
                emd_cd.append(h["code"])
    cities = sorted({b["city"] for b in rows})
    ds = sorted({bjd_to_dstrct(b["name"], b["city"])[0] for b in rows} - {None})
    est = any(bjd_to_dstrct(b["name"], b["city"])[1] for b in rows)
    if lon is None and rows:
        pts = [(b["lon"], b["lat"]) for b in rows if b["lon"] is not None]
        if pts:
            lon = round(sum(p[0] for p in pts) / len(pts), 6)
            lat = round(sum(p[1] for p in pts) / len(pts), 6)
    return RegionMatch(level=level, name=label, bjd=list(dict.fromkeys(b["name"] for b in rows)), bjd_keys=keys,
                       hjd=hjds, emd_nm=emd_nm, emd_cd=emd_cd, city=cities[0] if len(cities) == 1 else None,
                       dstrct=ds[0] if len(ds) == 1 else None, dstrct_estimated=est, lon=lon, lat=lat,
                       score=score, matched=matched, note=note)


def _mk_from_hjd(h: dict, score: float, matched: str) -> RegionMatch:
    m = _mk_from_bjd(h["bjd"], "hjd", h["name"], score, matched, city=h["city"], lon=h["lon"], lat=h["lat"])
    m.hjd, m.emd_nm, m.emd_cd = [h["name"]], [h["full"]], [h["code"]]
    m.dstrct, m.dstrct_estimated = h["dstrct"], h["dstrct_estimated"]
    m.city = h["city"]
    return m


def resolve_region(text: str, limit: int = 8, fine_only: bool = False) -> list[RegionMatch]:
    """자연어(예: '애월에서 카페', '모슬포 버스', '일도1동', '제주시 서부')에서 지역 후보를 점수순으로 반환."""
    if not text:
        return []
    raw = str(text)
    ts = re.sub(r"\s+", "", raw)
    found: dict[tuple, RegionMatch] = {}
    spans: list[tuple[int, int]] = []

    def covered(i: int, j: int) -> bool:
        return any(a <= i and j <= b for a, b in spans)

    def add(key, m: RegionMatch, pos: int):
        m._pos = pos  # type: ignore[attr-defined]
        if key not in found or found[key].score < m.score:
            found[key] = m

    def iter_hits(word: str):
        start = 0
        while True:
            i = ts.find(word, start)
            if i < 0:
                return
            yield i
            start = i + 1

    city_hint = "서귀포시" if "서귀포" in ts else ("제주시" if "제주시" in ts else None)
    # 1) 별칭(긴 것 먼저)
    for alias in sorted(ALIASES, key=len, reverse=True):
        for i in iter_hits(alias):
            if covered(i, i + len(alias)):
                continue
            a = ALIASES[alias]
            m = _mk_from_bjd(a["bjd"], "alias", alias, 0.97, alias, a.get("note", ""), lon=a.get("lon"), lat=a.get("lat"))
            add(("alias", alias), m, i)
            spans.append((i, i + len(alias)))
    # 2) 행정동 정식 이름(숫자형: 일도1동 등)
    for h in _data()["hjd"]:
        for form in {h["name"], h["name"].replace("1", "일").replace("2", "이").replace("3", "삼")} if re.search(r"\d", h["name"]) else set():
            for i in iter_hits(form):
                if covered(i, i + len(form)):
                    continue
                if form != h["name"] and any(b["name"] == form for b in _data()["bjd"]):
                    continue  # '일도일동'은 법정동으로 처리
                add(("hjd", h["full"]), _mk_from_hjd(h, 1.0, form), i)
                spans.append((i, i + len(form)))
    # 3) 법정동 정식 이름
    names = sorted({b["name"] for b in _data()["bjd"]}, key=len, reverse=True)
    for n in names:
        for i in iter_hits(n):
            if covered(i, i + len(n)):
                continue
            rows = _find_bjd(n)
            if len(rows) > 1:   # 월평동
                for b in rows:
                    sc = 0.95 if (city_hint and b["city"] == city_hint) else 0.8
                    add(("bjd", b["key"]), _mk_from_bjd([n], "bjd", f"{n}({b['city']})", sc, n, city=b["city"],
                                                        note="동명 법정동이 제주시·서귀포시에 각각 있음"), i)
            else:
                add(("bjd", rows[0]["key"]), _mk_from_bjd([n], "bjd", n, 1.0, n), i)
            spans.append((i, i + len(n)))
    # 4) 행정동 중 법정동과 이름이 다른 것(예: 아라동, 삼양동, 대천동, 예래동 …)
    for h in _data()["hjd"]:
        if re.search(r"\d", h["name"]) or any(b["name"] == h["name"] for b in _data()["bjd"]):
            continue
        for i in iter_hits(h["name"]):
            if covered(i, i + len(h["name"])):
                continue
            add(("hjd", h["full"]), _mk_from_hjd(h, 1.0, h["name"]), i)
            spans.append((i, i + len(h["name"])))
    # 5) 리(里) 이름 — '리'를 뗀 형태가 읍·면·동 기본형(애월·한림·조천 …)과 같으면 읍면 쪽을 우선
    bases = {re.sub(r"(읍|면|동)$", "", b["name"]) for b in _data()["bjd"]}
    for r in sorted(_data()["ri"], key=lambda x: len(x["name"]), reverse=True):
        n = r["name"]
        for form in (n, n[:-1] if len(n) >= 3 else None):
            if not form or len(form) < 2 or (form != n and form in bases):
                continue
            for i in iter_hits(form):
                if covered(i, i + len(form)):
                    continue
                m = _mk_from_bjd([r["bjd"]], "ri", f"{r['bjd']} {n}", 0.9 if form == n else 0.75, form,
                                 note=f"{r['bjd']} {n}", lon=r["lon"], lat=r["lat"])
                add(("ri", r["bjd"], n), m, i)
                spans.append((i, i + len(form)))
    # 6) 접미사 없는 기본형(애월, 한림, 노형 …)
    for b in _data()["bjd"]:
        base = re.sub(r"(읍|면|동)$", "", b["name"])
        ok = b["name"].endswith(("읍", "면")) or base in _BASE_WHITELIST
        if not ok or len(base) < 2:
            continue
        for i in iter_hits(base):
            if covered(i, i + len(base)):
                continue
            add(("bjd", b["key"]), _mk_from_bjd([b["name"]], "bjd", b["name"], 0.85, base, city=b["city"]), i)
    for h in _data()["hjd"]:
        base = re.sub(r"(동)$", "", h["name"])
        if base in _BASE_WHITELIST and not any(b["name"] == h["name"] for b in _data()["bjd"]):
            for i in iter_hits(base):
                if not covered(i, i + len(base)):
                    add(("hjd", h["full"]), _mk_from_hjd(h, 0.85, base), i)
    # 7) 권역
    for d in DISTRICTS7:
        for form in (d.replace(" ", ""), d.replace(" ", "").replace("(", "").replace(")", "")):
            for i in iter_hits(form):
                members = dstrct_members(d)
                m = _mk_from_bjd(members, "dstrct", d, 0.8, form, note="7개 권역(법정동 구성은 추정, 실데이터 우선)")
                m.dstrct = d
                add(("dstrct", d), m, i)
                spans.append((i, i + len(form)))
    # 8) 시군구 / 제주 전체 (fine_only=True 면 생략)
    if not found and not fine_only:
        for city, words in (("서귀포시", ["서귀포시", "서귀포"]), ("제주시", ["제주시"])):
            for w in words:
                i = ts.find(w)
                if i >= 0:
                    m = _mk_from_bjd([b["name"] for b in _data()["bjd"] if b["city"] == city], "city", city, 0.5, w,
                                     city=city)
                    m.bjd = list(dict.fromkeys(b["name"] for b in _data()["bjd"] if b["city"] == city))
                    m.city = city
                    add(("city", city), m, i)
                    break
    if not found and not fine_only:
        for w in _ISLAND_WORDS + ["제주"]:
            i = ts.find(w.replace(" ", ""))
            if i >= 0:
                m = _mk_from_bjd([b["name"] for b in _data()["bjd"]], "island", "제주 전체", 0.3, w)
                m.bjd = list(dict.fromkeys(b["name"] for b in _data()["bjd"]))
                add(("island",), m, i)
                break
    out = sorted(found.values(), key=lambda m: (-m.score, getattr(m, "_pos", 0)))
    for m in out:
        if hasattr(m, "_pos"):
            del m._pos  # type: ignore[attr-defined]
    return out[:limit]


def region_vocab() -> list[str]:
    """플래너 프롬프트용 지역 어휘(법정동·행정동·권역·대표 별칭)."""
    v = [b["name"] for b in _data()["bjd"]] + [h["name"] for h in _data()["hjd"]] + DISTRICTS7 + list(ALIASES)
    return list(dict.fromkeys(v))
