"""제주데이터허브 Open API 클라이언트 (urllib 전용).

    HubClient(project_key).fetch("583", {"startDate": "20241128", "endDate": "20241201", "emd": "대정읍"})

- URL: https://open.jejudatahub.net/api/proxy/{dataKey}/{projectKey}?{params}
- 페이지네이션: 허브 API 기술문서(공통 사항) 기준 `number`(페이지 번호, 기본 1), `limit`(페이지당 건수, 기본 10·최대 100),
  응답 {"totCnt": N, "hasMore": bool, "data": [...]}. hasMore=false·빈 페이지·누적 건수≥totCnt·max_pages 에서 멈추고,
  서버가 페이지 번호를 무시해 같은 페이지를 반복 반환하면(무한 루프) 즉시 중단한다.
- 일일 쿼터(데이터별 1,000회) 보호: data/cache/hub_quota.json 에 날짜·dataKey 별 호출 수 기록, 여유분(reserve) 남기고 차단.
- 디스크 캐시: data/cache/hub/<dataKey>/<hash>.json — 종료일이 과거인 조회는 영구, 그 외 cache_ttl_hours.
- 프로젝트 키: 인자 또는 환경변수 JEJU_HUB_PROJECT_KEY(.env). 없으면 HubKeyMissing. 키는 로그·예외 메시지에 노출하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

BASE_URL = "https://open.jejudatahub.net/api/proxy/{data_key}/{project_key}"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
MAX_LIMIT = 100
HARD_MAX_PAGES = 2000


@dataclass(frozen=True)
class HubDataset:
    id: str
    data_key: str
    name: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()


DATASETS: dict[str, HubDataset] = {
    "583": HubDataset("583", "Daaa1t3at3tt8a8DD3t55538t35Dab1t", "읍면동 단위 내국인 유동인구", ("startDate", "endDate"),
                      ("city", "emd", "gender", "ageGroup"),
                      ("baseDate", "city", "emd", "gender", "ageGroup", "resdPop", "workPop", "visitPop")),
    "612": HubDataset("612", "DD11ab6a6t11D16baaa1a2tD26ata161", "버스 정류소 기본 정보", (), ("stationName",),
                      ("stationId", "stationType", "stationName", "stationAddress", "direction", "longitude", "latitude",
                       "localInfo")),
    "613": HubDataset("613", "1b1ta1a1ba6t6a1ttt3bD6b1tb1t1D3t", "일일 정류소별 버스 이용자 정보", ("startDate", "endDate"),
                      ("moveType", "priceType", "payType"),
                      ("baseDate", "stationId", "stationName", "moveType", "priceType", "payType", "userCount")),
    "614": HubDataset("614", "1t4tbb16b4t1t11tt11tDt1ab6114a1t", "일일 노선별 버스 이용자 정보", ("startDate", "endDate"),
                      ("moveType", "priceType", "payType"),
                      ("baseDate", "routeName", "routeSubName", "moveType", "priceType", "payType", "userCount")),
}
ALIASES = {"population": "583", "pop": "583", "유동인구": "583", "station": "612", "stops": "612", "정류소": "612",
           "station_usage": "613", "stop_usage": "613", "정류소이용": "613", "route_usage": "614", "노선이용": "614"}


class HubError(RuntimeError):
    pass


class HubKeyMissing(HubError):
    pass


class HubAuthError(HubError):
    pass


class HubQuotaExceeded(HubError):
    pass


class HubHTTPError(HubError):
    pass


class HubResponseError(HubError):
    pass


@dataclass
class HubResult:
    dataset: HubDataset
    params: dict
    records: list[dict] = field(default_factory=list, repr=False)
    total: int | None = None
    pages: int = 0
    from_cache: int = 0
    api_calls: int = 0
    complete: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def source_label(self) -> str:
        p = self.params
        period = ""
        if p.get("startDate") and p.get("endDate"):
            s, e = str(p["startDate"]), str(p["endDate"])
            period = f"{s[:4]}.{s[4:6]}.{s[6:8]}~{e[:4]}.{e[4:6]}.{e[6:8]}"
        return f"제주데이터허브, {self.dataset.name}(Open API{', ' + period if period else ''})"

    def to_frame(self):
        import pandas as pd

        df = pd.DataFrame(self.records, columns=list(self.dataset.fields) or None)
        df.attrs.update(source_id=f"hub:{self.dataset.id}", source_label=self.source_label,
                        source_url=f"https://jejudatahub.net/data/view/data/{self.dataset.id}")
        return df


def _default_transport(url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "OREUM/1.0 (+jeju ai agent)", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if hasattr(e, "read") else b""


def _fmt_date(v) -> str:
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y%m%d")
    s = "".join(ch for ch in str(v) if ch.isdigit())
    if len(s) != 8:
        raise ValueError(f"날짜는 YYYYMMDD 형식이어야 합니다: {v!r}")
    return s


class HubClient:
    def __init__(self, project_key: str | None = None, cache_dir: Path | str | None = None, daily_quota: int = 1000,
                 quota_reserve: int = 20, timeout: float = 30.0, page_size: int = MAX_LIMIT,
                 cache_ttl_hours: float = 24.0, retries: int = 2,
                 transport: Callable[[str, float], tuple[int, bytes]] | None = None,
                 sleep: Callable[[float], None] = time.sleep, today: Callable[[], date] = date.today):
        self._key = (project_key if project_key is not None else os.environ.get("JEJU_HUB_PROJECT_KEY", "")).strip()
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.daily_quota = int(daily_quota)
        self.quota_reserve = int(quota_reserve)
        self.timeout = timeout
        self.page_size = max(1, min(int(page_size), MAX_LIMIT))
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.retries = retries
        self.transport = transport or _default_transport
        self.sleep = sleep
        self.today = today

    # --------------------------------------------------------------------------- 키·쿼터
    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def _redact(self, s: str) -> str:
        return s.replace(self._key, "***") if self._key else s

    @staticmethod
    def dataset(dataset_key: str) -> HubDataset:
        k = str(dataset_key).strip()
        k = ALIASES.get(k, k).replace("hub:", "")
        if k in DATASETS:
            return DATASETS[k]
        for d in DATASETS.values():
            if d.data_key == k:
                return d
        raise HubError(f"지원하지 않는 허브 API 데이터: {dataset_key} (지원: {', '.join(DATASETS)})")

    def _quota_path(self) -> Path:
        return self.cache_dir / "hub_quota.json"

    def _quota_load(self) -> dict:
        p = self._quota_path()
        try:
            q = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (ValueError, OSError):
            q = {}
        today = self.today().isoformat()
        return {today: q.get(today, {})}

    def quota_status(self, dataset_key: str) -> dict:
        d = self.dataset(dataset_key)
        used = self._quota_load()[self.today().isoformat()].get(d.data_key, 0)
        return dict(date=self.today().isoformat(), used=used, limit=self.daily_quota,
                    remaining=max(0, self.daily_quota - used), reserve=self.quota_reserve)

    def _quota_take(self, d: HubDataset) -> None:
        q = self._quota_load()
        day = self.today().isoformat()
        used = q[day].get(d.data_key, 0)
        if used >= self.daily_quota - self.quota_reserve:
            raise HubQuotaExceeded(f"[{d.id}] 오늘 API 호출 한도에 도달했습니다({used}/{self.daily_quota}, 여유분 "
                                   f"{self.quota_reserve} 보존). 캐시된 결과를 쓰거나 내일 다시 시도하세요.")
        q[day][d.data_key] = used + 1
        self._quota_path().parent.mkdir(parents=True, exist_ok=True)
        self._quota_path().write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")

    # --------------------------------------------------------------------------- 캐시
    def _cache_file(self, d: HubDataset, params: dict, number: int, limit: int) -> Path:
        key = json.dumps({"p": params, "n": number, "l": limit}, sort_keys=True, ensure_ascii=False)
        return self.cache_dir / "hub" / d.data_key / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def _cache_get(self, path: Path, params: dict) -> dict | None:
        if not path.exists():
            return None
        try:
            c = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        end = params.get("endDate")
        historical = bool(end) and end < self.today().strftime("%Y%m%d")
        if not historical:
            age = datetime.now() - datetime.fromisoformat(c.get("fetched_at", "1970-01-01T00:00:00"))
            if age > self.cache_ttl:
                return None
        return c.get("response")

    def _cache_put(self, path: Path, params: dict, number: int, resp: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": datetime.now().isoformat(timespec="seconds"), "params": params,
                                    "page": number, "response": resp}, ensure_ascii=False), encoding="utf-8")

    # --------------------------------------------------------------------------- 요청
    def _request(self, d: HubDataset, params: dict, number: int, limit: int) -> dict:
        if not self._key:
            raise HubKeyMissing("제주데이터허브 프로젝트 키가 설정되지 않았습니다. .env 또는 환경변수 JEJU_HUB_PROJECT_KEY 에 "
                                "센터가 발급한 프로젝트 키를 입력하세요(허브 마이페이지 > 프로젝트 관리).")
        q = dict(params)
        q.update(number=number, limit=limit)
        url = BASE_URL.format(data_key=d.data_key, project_key=urllib.parse.quote(self._key, safe="")) + "?" + \
            urllib.parse.urlencode(q)
        last = None
        for attempt in range(self.retries + 1):
            self._quota_take(d)
            try:
                status, body = self.transport(url, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = HubHTTPError(f"[{d.id}] 네트워크 오류: {self._redact(str(e))}")
            else:
                if status in (401, 403):
                    raise HubAuthError(f"[{d.id}] 거부됨(HTTP {status}) — 오늘 호출 한도(데이터별 1,000회)를 넘었거나, "
                                       "프로젝트 키가 유효하지 않거나, 해당 데이터가 프로젝트에 담기지 않았습니다.")
                if status == 429:
                    raise HubQuotaExceeded(f"[{d.id}] 허브 서버가 호출 한도 초과(HTTP 429)를 응답했습니다.")
                if 200 <= status < 300:
                    try:
                        resp = json.loads(body.decode("utf-8-sig"))
                    except (ValueError, UnicodeDecodeError) as e:
                        raise HubResponseError(f"[{d.id}] JSON 응답 해석 실패: {e}") from None
                    if not isinstance(resp, dict) or not isinstance(resp.get("data", []), list):
                        msg = resp.get("message") if isinstance(resp, dict) else ""
                        raise HubResponseError(f"[{d.id}] 예상과 다른 응답 형식{': ' + str(msg) if msg else ''}")
                    return resp
                last = HubHTTPError(f"[{d.id}] HTTP {status}: {self._redact(body[:200].decode('utf-8', 'ignore'))}")
                if status < 500:
                    raise last
            if attempt < self.retries:
                self.sleep(1.0 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _prepare(self, d: HubDataset, params: dict | None) -> dict:
        p = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        for k in ("number", "limit"):
            p.pop(k, None)
        missing = [k for k in d.required if k not in p]
        if missing:
            raise HubError(f"[{d.id}] 필수 파라미터 누락: {', '.join(missing)}")
        unknown = [k for k in p if k not in d.required + d.optional]
        if unknown:
            raise HubError(f"[{d.id}] 알 수 없는 파라미터: {', '.join(unknown)} (허용: {', '.join(d.required + d.optional)})")
        for k in ("startDate", "endDate"):
            if k in p:
                p[k] = _fmt_date(p[k])
        if "startDate" in p and "endDate" in p and p["startDate"] > p["endDate"]:
            raise HubError(f"[{d.id}] startDate 가 endDate 보다 늦습니다.")
        return {k: str(v) for k, v in sorted(p.items())}

    def fetch(self, dataset_key: str, params: dict | None = None, max_pages: int | None = 50,
              use_cache: bool = True) -> HubResult:
        """전 페이지 조회(최대 max_pages). 반환 HubResult.records(list[dict]), .to_frame()."""
        d = self.dataset(dataset_key)
        p = self._prepare(d, params)
        res = HubResult(dataset=d, params=p)
        limit = self.page_size
        cap = min(int(max_pages or HARD_MAX_PAGES), HARD_MAX_PAGES)
        prev_sig = None
        number = 1
        while number <= cap:
            cf = self._cache_file(d, p, number, limit)
            resp = self._cache_get(cf, p) if use_cache else None
            if resp is not None:
                res.from_cache += 1
            else:
                resp = self._request(d, p, number, limit)
                res.api_calls += 1
                if use_cache:
                    self._cache_put(cf, p, number, resp)
            data = resp.get("data") or []
            res.pages += 1
            if resp.get("totCnt") is not None:
                try:
                    res.total = int(resp["totCnt"])
                except (TypeError, ValueError):
                    pass
            sig = hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if prev_sig is not None and sig == prev_sig and data:
                res.warnings.append("직전 페이지와 같은 데이터가 반환되어 중단(서버가 페이지 번호를 무시하는 것으로 보임)")
                break
            prev_sig = sig
            res.records.extend(data)
            has_more = resp.get("hasMore")
            if not data or has_more is False:
                res.complete = True
                break
            if res.total is not None and len(res.records) >= res.total:
                res.complete = True
                break
            if has_more is None and len(data) < limit:
                res.complete = True
                break
            number += 1
        else:
            res.warnings.append(f"최대 {cap}페이지까지만 조회(전체 {res.total or '?'}건 중 {len(res.records)}건)")
        if res.total is not None and res.complete and len(res.records) < res.total:
            res.warnings.append(f"응답 건수({len(res.records)})가 totCnt({res.total})보다 적음")
        return res


def fetch(dataset_key: str, params: dict | None = None, **kw) -> HubResult:
    """환경변수 키로 바로 조회하는 단축 함수."""
    return HubClient().fetch(dataset_key, params, **kw)
