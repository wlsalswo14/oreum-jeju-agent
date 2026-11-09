"""제주데이터허브 Open API 실데이터 스냅샷 만들기(583·612·613).

센터 PC에 허브 키가 없어도 대표 시나리오를 실데이터로 재현할 수 있도록, Open API 응답을 parquet 으로 저장한다.
키는 .env(JEJU_HUB_PROJECT_KEY)에서만 읽고 어디에도 기록하지 않는다. 일일 쿼터(데이터별 1,000회)는 HubClient 가 지킨다.

    .venv\\Scripts\\python.exe tools\\fetch_hub_api.py pop    # 583 읍면동 일별 유동인구(시나리오 지역·기간)
    .venv\\Scripts\\python.exe tools\\fetch_hub_api.py stops  # 612 정류소 기본 정보(전체)
    .venv\\Scripts\\python.exe tools\\fetch_hub_api.py usage 20241114 20241117 ...  # 613 정류소별 일일 이용(날짜 목록)
    .venv\\Scripts\\python.exe tools\\fetch_hub_api.py build  # 받은 캐시 → data/public/*.parquet + PROVENANCE 갱신

출처 표기: '제주데이터허브 Open API, <데이터명>(조회 기간)'.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.config import settings  # noqa: E402
from app.hub.client import HubClient  # noqa: E402

PUB = ROOT / "data" / "public"
SNAP = ROOT / "data" / "cache" / "hub_snapshot"

# 대표 시나리오(실데이터) — 지역·기간
POP_JOBS = [
    # 축제: 2024 최남단 방어축제(2024.11.28~12.01, 대정읍) + 비교지역(안덕면·한경면), 행사 전 3주~후 1주
    {"emd": ["대정읍", "안덕면", "한경면"], "start": "20241107", "end": "20241208", "tag": "event_bangeo2024"},
    # 축제: 전년 같은 시기(전년 대비)
    {"emd": ["대정읍"], "start": "20231109", "end": "20231210", "tag": "event_bangeo2023"},
    # 상권: 애월읍 최근 12개월 + 그 전 12개월(최근 방문인구 추이)
    {"emd": ["애월읍"], "start": "20231021", "end": "20251020", "tag": "trade_aewol"},
]


def _client() -> HubClient:
    key = settings.hub_project_key
    if not key:
        raise SystemExit("JEJU_HUB_PROJECT_KEY 가 .env 에 없습니다.")
    return HubClient(key, quota_reserve=5)


def _save(name: str, rows: list[dict], meta: dict) -> None:
    SNAP.mkdir(parents=True, exist_ok=True)
    (SNAP / f"{name}.json").write_text(json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False), encoding="utf-8")


def _load(name: str) -> tuple[list[dict], dict]:
    p = SNAP / f"{name}.json"
    if not p.exists():
        return [], {}
    o = json.loads(p.read_text(encoding="utf-8"))
    return o["rows"], o["meta"]


def cmd_pop() -> None:
    hc = _client()
    for job in POP_JOBS:
        for emd in job["emd"]:
            name = f"583_{job['tag']}_{emd}"
            if (SNAP / f"{name}.json").exists():
                print("skip", name)
                continue
            t0 = time.time()
            r = hc.fetch("583", {"startDate": job["start"], "endDate": job["end"], "emd": emd}, max_pages=400)
            _save(name, r.records, {"start": job["start"], "end": job["end"], "emd": emd, "pages": r.pages})
            print(name, len(r.records), "rows", r.pages, "pages", f"{time.time() - t0:.0f}s", flush=True)


def cmd_stops() -> None:
    hc = _client()
    r = hc.fetch("612", {}, max_pages=200)
    _save("612_stops", r.records, {"fetched": date.today().isoformat(), "pages": r.pages})
    print("612", len(r.records), "rows", r.pages, "pages")


def cmd_usage(days: list[str]) -> None:
    hc = _client()
    for d in days:
        name = f"613_{d}"
        if (SNAP / f"{name}.json").exists():
            print("skip", name)
            continue
        t0 = time.time()
        r = hc.fetch("613", {"startDate": d, "endDate": d}, max_pages=200)
        _save(name, r.records, {"date": d, "pages": r.pages})
        print(name, len(r.records), "rows", r.pages, "pages", f"{time.time() - t0:.0f}s",
              hc.quota_status("613"), flush=True)


def cmd_build() -> None:
    prov_p = PUB / "PROVENANCE.json"
    prov = json.loads(prov_p.read_text(encoding="utf-8"))
    # 583
    rows = []
    jobs = []
    for p in sorted(SNAP.glob("583_*.json")):
        r, m = _load(p.stem)
        rows += r
        jobs.append(f"{m['emd']} {m['start']}~{m['end']}")
    if rows:
        df = pd.DataFrame(rows).drop_duplicates()
        for c in ("resdPop", "workPop", "visitPop"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.to_parquet(PUB / "pop583_daily.parquet", index=False)
        prov["datasets"]["583"] = {
            "dataset_id": "583", "title": "읍면동 단위 내국인 유동인구(Open API)",
            "source_url": "https://jejudatahub.net/data/view/data/583",
            "period": ", ".join(jobs), "rows_out": int(len(df)), "output": "pop583_daily.parquet",
            "processing": ["Open API 응답(baseDate·city·emd·gender·ageGroup·resdPop·workPop·visitPop)을 그대로 저장",
                           "대표 시나리오 지역·기간만 조회(tools/fetch_hub_api.py POP_JOBS)", "완전 중복 제거"],
        }
        print("583 →", len(df))
    # 612
    r, m = _load("612_stops")
    if r:
        df = pd.DataFrame(r).drop_duplicates("stationId")
        for c in ("longitude", "latitude"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.to_parquet(PUB / "stops612.parquet", index=False)
        prov["datasets"]["612"] = {
            "dataset_id": "612", "title": "버스 정류소 기본 정보(Open API)",
            "source_url": "https://jejudatahub.net/data/view/data/612", "period": f"{m.get('fetched')} 조회",
            "rows_out": int(len(df)), "output": "stops612.parquet",
            "processing": ["Open API 전체 조회 후 stationId 중복 제거", "경도·위도 숫자형 변환"],
        }
        print("612 →", len(df))
    # 613
    rows, days = [], []
    for p in sorted(SNAP.glob("613_*.json")):
        r, m = _load(p.stem)
        rows += r
        days.append(m["date"])
    if rows:
        df = pd.DataFrame(rows)
        df["userCount"] = pd.to_numeric(df["userCount"], errors="coerce").fillna(0).astype("int64")
        df = (df.groupby(["baseDate", "stationId", "stationName", "moveType", "priceType"], as_index=False)["userCount"].sum())
        df.to_parquet(PUB / "stop_usage613.parquet", index=False)
        prov["datasets"]["613"] = {
            "dataset_id": "613", "title": "일일 정류소별 버스 이용자 정보(Open API)",
            "source_url": "https://jejudatahub.net/data/view/data/613",
            "period": ", ".join(days), "rows_out": int(len(df)), "output": "stop_usage613.parquet",
            "processing": ["대표 시나리오 날짜(행사일·같은 요일 기준선)만 조회(데이터별 일일 1,000회 쿼터)",
                           "지불방법(카드·현금) 차원을 합산: 날짜×정류소×탑승구분×요금제 이용자수 합계"],
        }
        print("613 →", len(df), "days", len(days))
    prov_p.write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "pop":
        cmd_pop()
    elif cmd == "stops":
        cmd_stops()
    elif cmd == "usage":
        cmd_usage(sys.argv[2:])
    elif cmd == "build":
        cmd_build()
    else:
        raise SystemExit(__doc__)
