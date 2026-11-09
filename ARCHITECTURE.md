# 오름(OREUM) 설계 문서 — 모듈 구조와 데이터 계약

> 제주빅데이터센터 방문 이용자(주로 기관·기업, 예비창업자)가 **질문 한 줄**로
> ① 창업·상권 분석 ② 축제·행사 효과 분석 ③ 버스 정류소·노선 진단을 수행하고,
> **반출 심사를 통과하는 보고서(PDF/DOCX/PNG)** 까지 받아가는 AI Agent.

## 0. 확정된 외부 제약 (게시판 공식 답변·반출 정책 근거)
- 센터 PC: Windows 11, i7-8700, GTX1060 3GB, RAM 32GB, Chrome/Edge, 1920×1080, 인터넷 가능, 고정 IP, DLP 적용.
- 실행 방식: 로컬 서버(127.0.0.1) + 브라우저 허용. 설치는 센터 담당자가 매뉴얼대로 수행(인터넷 설치 가능).
- 센터 데이터: Agent의 직접 DB 연결 불가. 이용자가 센터 조회 화면에서 **1개월 단위 CSV**를 내려받아 투입.
  CSV 형식: UTF-8, 헤더=명세서 영문 컬럼명, 구분자 `,`, 결측 `NA`, 파티션 컬럼(pt_year, pt_month) 포함 가능, 따옴표 혼재.
  (선택 컬럼만 들어갈 수도 있음 → 헤더 부분일치 인식 필요)
- 반출 불가 원본/원본 조회 결과는 외부 LLM 전송 금지. 집계·가공 결과는 반출 기준 충족 범위에서 전송 가능.
- 반출 기준(2026.09.01): 반출불가 데이터(카드·POS·유동인구·기업)는 원본·발췌 X, 단순집계는 **10 미만 수치 불포함 시** O
  (처리: 범주처리/삭제처리/비식별), 응용집계(차이·증감률·비율·순위·지수·모델링) O, 시각화는 PNG/JPG + 수치는 기준 따름.
  텍스트(csv/txt)·스프레드시트 반출은 원칙 불가 → **PDF/DOCX 보고서 + PNG 차트**로 반출. 역산 가능하면 반출 불가.
  반출 신청 시 자료별 출처(원본데이터 명칭)·활용 사유 상세 기재 필수. 출처 표기: "제주빅데이터센터, 데이터셋(이름, 기간)".
  교통(티머니)·환경·와이파이 데이터는 원본 포함 전부 반출 가능.
- 퇴실 시 반출 승인 자료 외 삭제. 보존 필요 시 매뉴얼에 명시.
- LLM API 키: 센터가 발급·설정(환경변수/설정파일). 제출물에 키 포함 금지.
- 대회 제공 「제주데이터허브 데이터 목록 xlsx」는 zip/GitHub 포함 금지. 명세서·허브 공개 데이터는 포함 가능.

## 1. 폴더 구조
```
oreum/
  app/
    config.py            설정(.env), 경로
    main.py              FastAPI 앱(라우트), 정적 파일 서빙
    session.py           작업공간(세션별 폴더), 퇴실 정리
    catalog/             데이터 계층
      spec_catalog.json  명세서 74종 → 기계가독 카탈로그 (tools/build_catalog.py 생성)
      catalog.py         카탈로그 로드/조회/헤더 인식
      regions.py         법정동·행정동·권역·시군구 매핑, 중심좌표
      industries.py      BC카드 42업종 ↔ 상가 업종 매핑, 자연어 별칭
      festivals.py       축제 목록 + 장소(읍면동) 큐레이션
    ingest/loader.py    CSV 인식·병합·검증 
    hub/
      client.py         제주데이터허브 Open API 클라이언트 
      public_data.py    번들된 허브 공개 데이터 로더 
    analysis/            분석 엔진: trade_area.py, event_impact.py, transit.py, common.py
    guard/               반출 안전장치: export_guard.py, llm_gate.py
    agent/               에이전트: llm.py, planner.py, plan_tools.py, prompts.py, orchestrator.py, narrator.py
    report/              보고서: charts.py, builder.py, templates/
  web/                   프런트엔드(정적 HTML/JS/CSS, 빌드 없음)
  data/
    public/              허브 공개 데이터 전처리본(parquet/csv, 출처 명시)
    public/              허브 실데이터 + Open API 스냅샷(583·612·613, tools/fetch_hub_api.py) — 시연·결과는 모두 여기서
    demo_center/         테스트 전용 형식 검증 CSV (run_tests.bat 이 tools/make_demo_data.py로 생성, 화면·보고서 미사용)
  tools/                 build_catalog.py, prepare_public.py, make_demo_data.py, eval_agent.py
  tests/
```

## 2. 데이터 계층 계약 (catalog / ingest / hub)

### 2.1 `spec_catalog.json`
```json
{
  "version": "v13 (2026.11.04)",
  "datasets": {
    "1-08": {
      "id": "1-08", "name": "카드 이용 현황(읍면동)_v02", "category": "경제",
      "exportable": false, "provider": "제주특별자치도(비씨카드)",
      "description": "...", "period": "2023년 8월 ~ 현재", "update": "월",
      "grain": "month",            // month | day | hour | static
      "columns": [{"name":"base_year_month","kor":"년월","type":"STRING","example":"202308","note":"기준일자"}, ...],
      "measures": ["user_cnt","use_cnt","use_amt"],   // 합산 가능한 수치 컬럼
      "dims": ["dstrct_se","city","area_type","bjd","biz_cd","biz_nm","user_type","tour_type","age","sex"],
      "time_col": "base_year_month",
      "region_cols": {"bjd": "법정동", "dstrct_se": "권역", "city": "시군구"},
      "family": "card_bjd", "version_tag": "v02", "supersedes": "1-07"
    }
  },
  "reference": {
    "districts7": [...], "bjd73": [...], "emd43": [{"code":"50110250","name":"제주시 한림읍"}...],
    "biz42": [{"code":"G47119","name":"기타 대형 종합 소매업"}...],
    "card_time_bands": {"오전":"07~11시", ...}, "age_bands": {...}
  }
}
```
- `measures`/`dims`/`time_col`/`grain`/`family`는 build 스크립트가 규칙 기반으로 채우고, 핵심 데이터셋
  (1-08,1-12,1-14,1-17,1-19,4-10,4-11,3-01~3-04,5-02)은 수기 검증.

### 2.2 `catalog.py` API
```python
load_catalog() -> Catalog
Catalog.get(dataset_id) -> DatasetSpec            # dataclass/pydantic
Catalog.detect(columns: list[str]) -> DetectResult  # dataset_id, score(0~1), missing_cols, extra_cols, candidates[]
Catalog.exportable(dataset_id) -> bool
Catalog.describe_for_llm(dataset_ids) -> str      # 공개 명세 정보만(컬럼명/설명). 수치 없음
Catalog.search(text) -> list[DatasetSpec]         # 한글 키워드로 데이터셋 찾기
```
인식 규칙: 헤더 집합의 Jaccard + 핵심 식별 컬럼(예: `week`→요일별, `time`+card→시간대, `resd_pop_cnt`→유동인구) 가중.
v01/v02 구분(예: 4-10 vs 4-11은 dstrct_se 유무). 선택 컬럼만 있는 CSV도 최고 후보 + 누락 컬럼 보고.

### 2.3 `ingest/loader.py` API
```python
load_files(paths: list[Path]) -> IngestReport
IngestReport.datasets: dict[str, LoadedDataset]   # dataset_id → 병합 DataFrame
LoadedDataset: id, df(pd.DataFrame, 수치형 변환·NA 처리 완료), files[], months/days covered,
               missing_periods[], row_count, warnings[], source_label("제주빅데이터센터, 카드 이용 현황(읍면동)_v02(2024.01~2024.12)")
               is_synthetic(bool: 파일명/마커로 판단)
```
- 인코딩 자동(utf-8-sig → cp949), 따옴표/숫자 문자열 처리, `NA`→결측, 중복 행 제거(같은 파일 두 번 업로드 대비).
- 행 데이터를 화면/LLM에 노출하는 함수는 만들지 않는다(반출 정책: head() 출력 금지).

### 2.4 `hub/public_data.py`
번들된 허브 공개 데이터(출처 표기 포함):
- `stores()` 상가(상권) 정보 [793] → 법정동·행정동·업종(대/중/소)·좌표
- `card_mashup()` 예비창업자 업종별 카드매출·매장수 매쉬업 [746] (2017~2021, 읍면동×업종×월 집계본)
- `card_pop_mashup()` 유동인구-카드매출 매쉬업 [748]
- `festivals()` VISIT JEJU 축제행사 [761] + 장소 큐레이션
- `bus_daily_timeband()` 일별 시간대별 버스 승차 [512]
### 2.5 `hub/client.py`
`HubClient(project_key).fetch(dataset_key, params, max_pages)` — `https://open.jejudatahub.net/api/proxy/{dataKey}/{projectKey}?...`
페이지네이션(hasMore/totCnt) 처리, 일일 쿼터(1000) 보호, 캐시(data/cache). 키 없으면 명확한 예외.
대상: 583 유동인구(Daaa1t3at3tt8a8DD3t55538t35Dab1t), 612 정류소 기본(DD11ab6a6t11D16baaa1a2tD26ata161),
613 정류소별 일일 이용자(1b1ta1a1ba6t6a1ttt3bD6b1tb1t1D3t), 614 노선별 일일 이용자(1t4tbb16b4t1t11tt11tDt1ab6114a1t).

## 3. 분석·안전장치·에이전트 

### 3.1 분석 및 안전장치 계약
- 분석 함수는 **집계 테이블(AggTable)** 만 반환: `name, df, kind(simple|applied), source_datasets, unit, note`.
- `export_guard.check(table)`: 데이터셋 반출등급 + 집계유형 → 10 미만 셀 탐지 → 범주처리(상위 단위 롤업) 또는 삭제처리 + **보완 억제(역산 방지)** → `GuardResult(status=pass|fixed|blocked, actions[], table_out)`.
- `llm_gate`: LLM로 나가는 모든 페이로드는 guard 통과 테이블 + 공개 명세만. 전송 내용 감사 로그(UI 공개).
- 에이전트 전체 흐름: Plan(계획 Agent or 규칙 기반) → Data Navigator(필요 데이터·조회 조건 안내) → Ingest → Analyze → Guard → Narrate(LLM 서술 + 수치 근거 검증) → Report(PDF/DOCX/PNG + 반출 신청서 초안) → Cleanup.
- 프롬프트 관리: 모든 에이전트 프롬프트는 `app/agent/prompts.py`에 집중 배치(계획 `PLAN_AGENT_SYSTEM`, 해석 `NARRATIVE_SYSTEM`, 후속 질문 `FOLLOWUP_SYSTEM`). 파일 머리에 단계별 요약표가 정의되어 있음.

### 3.2 계획 Agent 루프 (app/agent/planner.py)
- **실행 모델**: OpenAI `gpt-6-luna`(기본, 추론 강도 `high`, 엄격 JSON 스키마).
- **루프 제어**: 최대 3단계(라운드), 단계마다 도구를 최대 4개(`MAX_CALLS=4`)까지 자율 선택해 호출한 뒤 계획 확정. 3단계 도달 시 `action="final"`로 강제 확정.
- **도구 실행 및 프롬프트 주입**: AI가 `calls`에 지정한 도구를 로컬 PC에서 실행하고, 그 실행 결과를 다음 라운드 입력에 `[도구 결과]` 블록으로 덧붙여 전달(ReAct 패턴).
- **도구 5종 (`app/agent/plan_tools.py`)**:
  - `find_region`: 자연어 지역 표현 → 법정동·행정동·권역 표준 이름 조회
  - `find_industry`: 자연어 업종 표현 → 비씨카드 42업종 후보 조회
  - `find_festival`: 축제·행사 이름 → 제주데이터허브 축제 목록(761)의 회차·날짜·개최지 조회
  - `search_datasets`: 키워드 → 센터 명세서 71종 + 제주데이터허브 9종 검색 (반출 등급 포함)
  - `get_dataset`: 데이터 ID → 기간·갱신 주기·주요 컬럼·반출 등급 상세 조회
  - *모든 도구는 로컬 PC 내 사전·명세서 카탈로그만 조회하며, 원본 데이터 값은 보지 않음.*
- **단계 스키마 (`STEP_SCHEMA`)**:
  - `thought`: 사용자에게 노출할 현재 판단 및 진행 이유 (한 문장)
  - `action`: 도구 호출 (`call_tools`) 또는 계획 확정 (`final`)
  - `calls`: 호출할 도구 목록 `[{"tool": ..., "arguments": "..."}]`
  - `plan`: 최종 확정 계획 객체 (`mode`, `region`, `industry`, `event_name`, `event_start`, `event_end`, `control_region`, `user_type`, `purpose`, `clarify`, `explanation`, `datasets: [{"id": ..., "reason": ...}]`).
- **계획 결과 검증**: AI가 제시한 `datasets`의 데이터 ID는 카탈로그로 검증하고 없는 ID는 버림(`_valid_datasets`).
- **예외 처리 및 안전 가드**:
  - 자동 분석 3종(상권·축제·버스) 외 질문: 거절하지 않고 `mode="unsupported"`(데이터 탐색)로 유도하여 관련 데이터·기간·갱신 주기·반출 등급·주요 항목을 안내.
  - 원본 반출·개인 식별·반출 기준 우회 요청: 계획하지 않고 사유를 안내.
  - AI 키가 없거나 AI 오류 시: 즉시 `rule_plan_traced`로 자동 전환. 규칙 모드에서도 동일한 도구를 정해진 순서(`find_region` → `find_industry`/`find_festival` → `search_datasets`)로 호출하여 작업 기록을 똑같이 남김.
- **실시간 추적 (`trace`) 규격**:
  - 각 단계는 `{"t": "HH:MM:SS", "kind": "think"|"tool"|"final"|"note", "title": str, "by": "AI"|"도구"|"규칙", "detail": str, "tool": str, "args": dict, "sec": float}` 형태로 누적.
- **비동기 실행 및 UI 폴링**:
  - 화면에서 계획 요청 시 `/api/{sid}/plan` 엔드포인트가 백그라운드 스레드에서 `make_plan`을 구동.
  - 클라이언트는 `/state`를 0.7초 간격으로 폴링하여 `planning`(진행 여부)과 `trace`(실시간 누적 기록)를 UI에 갱신.

### 3.3 체크리스트 병합 (_merge_ai_picks)
- `app/agent/orchestrator.py`의 `_merge_ai_picks`에서 분석 기본 체크리스트와 Agent가 고른 데이터(`plan.datasets`)를 병합:
  - Agent가 고른 데이터 ID가 기존 필수/권장 체크리스트 항목과 겹치면 해당 항목에 `ai_pick` 표시(고른 이유)를 부여.
  - 기존 체크리스트에 없는 데이터 ID인 경우 `need="suggested"`(AI 추천 항목)으로 신규 추가.

### 3.4 후속 질문 Agent 및 결과 탭
- **후속 질문 Agent (`orchestrator.followup`)**: 도구 4종(`compare_regions`, `rerun_analysis`, `get_table`, `explain_method`), 최대 6단계. 이제 후속 질문에서 사용한 도구 호출 기록도 세션의 '작업 과정' 탭에 남음.
- **결과 5개 탭**: 분석 결과, 제언·활용 문안, 근거 자료, 반출 검사, **작업 과정**(AI 호출 수, 도구 호출 수, 반출 검사 통과, AI 비용 요약 및 ① 계획 도구 기록 ② 실행 계산 단계 ③ AI에 보낸 것 ④ 후속 질문 도구).

## 4. 데모/평가 시나리오
- A. 창업·상권: "애월읍에서 카페 창업하려는데 상권 어때?" — 허브 실데이터 746·748·583·793 (센터 1-08,1-12,1-14,4-11 넣으면 최신·세부)
- B. 축제 효과: "2024 최남단 방어축제(대정읍, 2024.11.28~12.01) 지역경제 효과" — 허브 761·583·612·613 (센터 4-10/11,3-04,1-08 넣으면 시간대·소비)
- C. 버스 진단: "대정읍·모슬포 버스 정류소 이용 진단" — 허브 612·613(정류소), 512(제주 전역) (센터 3-03,3-04,3-02 넣으면 시간 단위·노선)
- 가상 데이터는 시연에 쓰지 않는다. 센터 CSV 경로의 인식·반출 검사는 테스트 전용 형식 검증 데이터로만 자동 시험한다.
