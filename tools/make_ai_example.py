"""docs/AI_전송_예시.md 생성 — 오름이 OpenAI(gpt-6-luna)에 보내는 '실제 요청 원문'을 기록용 스텁으로 캡처한다.

네트워크 호출 없이(키 불필요) 상권 대표 시나리오를 실데이터로 실행하면서, AI 클라이언트 자리에 요청을 기록만 하는
스텁을 끼워 넣는다. 스텁은 계획 1·2단계 응답을 돌려주고, 해석 단계에서는 빈 응답을 돌려주므로 오름은 규칙 기반 문장으로 자동 전환되고,
기록된 요청 원문과 계획 응답을 문서로 남긴다.

    .venv\\Scripts\\python.exe tools\\make_ai_example.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["OPENAI_API_KEY"] = "stub-not-a-real-key"

from app.agent import llm as llm_mod  # noqa: E402
from app.agent.orchestrator import Agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.session import SessionStore  # noqa: E402

QUESTION = "애월읍에서 카페를 창업하려고 하는데 상권이 어떤가요?"
CALLS: list[dict] = []

PLAN_JSON = {"mode": "trade_area", "region": "애월읍", "industry": "비알콜 음료점업", "event_name": "", "event_start": "",
             "event_end": "", "control_region": "", "user_type": "예비창업자",
             "purpose": "창업 사업계획서의 시장 분석 근거 자료로 활용", "clarify": [],
             "explanation": "애월읍 카페 업종의 매출·고객·경쟁·방문인구를 분석합니다."}

EMPTY_PLAN = {
    "mode": "unsupported",
    "region": "",
    "industry": "",
    "event_name": "",
    "event_start": "",
    "event_end": "",
    "control_region": "",
    "user_type": "기타",
    "purpose": "",
    "clarify": [],
    "explanation": "",
    "datasets": [],
}

FULL_PLAN = {
    **PLAN_JSON,
    "datasets": [
        {"id": "1-08", "reason": "읍면동·업종별 월별 카드 매출로 최신 매출 흐름 확인"},
        {"id": "hub:746", "reason": "센터 CSV 없이도 업종 매출·매장 수 분석"},
        {"id": "4-11", "reason": "읍면동 유동인구로 방문 수요 확인"},
    ],
}

STEP_1 = {
    "thought": "애월읍과 카페 업종을 표준 이름으로 확인하고 필요한 데이터를 찾겠습니다.",
    "action": "call_tools",
    "calls": [
        {"tool": "find_region", "arguments": json.dumps({"text": "애월읍"}, ensure_ascii=False)},
        {"tool": "find_industry", "arguments": json.dumps({"text": "카페"}, ensure_ascii=False)},
        {"tool": "search_datasets", "arguments": json.dumps({"query": "카페 매출 유동인구 경쟁 점포"}, ensure_ascii=False)},
    ],
    "plan": EMPTY_PLAN,
}

STEP_2 = {
    "thought": "확인한 지역·업종과 데이터로 계획을 확정합니다.",
    "action": "final",
    "calls": [],
    "plan": FULL_PLAN,
}


class _Rec:
    def create(self, **kw):
        CALLS.append(kw)
        idx = len(CALLS)
        if idx == 1:
            text = json.dumps(STEP_1, ensure_ascii=False)
        elif idx == 2:
            text = json.dumps(STEP_2, ensure_ascii=False)
        else:
            text = "{}"
        return NS(output_text=text, output=[], status="completed", model=kw.get("model"),
                  usage=NS(input_tokens=0, output_tokens=0))


class _Client:
    def __init__(self):
        self.responses = _Rec()


def main() -> None:
    settings.llm_enabled = True
    settings.llm_provider_env = "openai"           # 센터 운영 설정(OpenAI · gpt-6-luna · high)과 같은 요청을 캡처
    settings.llm_model_env = ""
    settings.llm_effort_plan = settings.llm_effort_write = "high"
    settings.hub_project_key = ""                     # 스냅샷만 사용(재현성)
    llm_mod.LLM._get_client = lambda self: _Client()  # 네트워크 대신 기록용 스텁
    import app.report.builder as B

    B.html_to_pdf = lambda *a, **k: None
    with tempfile.TemporaryDirectory() as td:
        s = SessionStore(Path(td)).create()
        a = Agent(s)
        a.plan(QUESTION)
        st = a.run()
        if st.get("error"):
            raise SystemExit(st["error"])
    plan_1 = CALLS[0] if len(CALLS) > 0 else None
    plan_2 = CALLS[1] if len(CALLS) > 1 else None
    write = CALLS[2] if len(CALLS) > 2 else None
    if not plan_1 or not plan_2 or not write:
        raise SystemExit(f"요청을 캡처하지 못했습니다({len(CALLS)}건)")
    user_w = write["input"]
    n_tables = len(re.findall(r"^### \[", user_w, flags=re.M))
    n_rows = sum(1 for ln in user_w.splitlines() if ln.startswith("| ") and not ln.startswith("| ---")) - n_tables
    from app.agent.tools import TOOLS

    action_schema = {"type": "object", "additionalProperties": False, "required": ["action", "tool", "arguments", "answer"],
                     "properties": {"action": {"type": "string", "enum": ["call_tool", "answer"]},
                                    "tool": {"type": "string", "enum": [t["name"] for t in TOOLS] + [""]},
                                    "arguments": {"type": "string"}, "answer": {"type": "string"}}}
    nl = chr(10)
    tools_sec = (
        "## 4) 후속 질문 Agent — 도구 선택(JSON 행동 스키마)" + nl * 2
        + "매 턴 AI는 아래 스키마로 **'도구 호출(call_tool)' 또는 '답변(answer)'** 을 고릅니다. 도구가 선택되면 오름이 센터 PC 안에서 계산하고, "
        "**반출 검사를 통과한 결과만** 다음 턴 입력의 `[도구 결과 n]`으로 붙입니다(최대 6턴). "
        "제공자(OpenAI·Claude·검증용 Codex)와 무관하게 같은 방식입니다." + nl * 2
        + "**행동 스키마(strict)**" + nl + "```json" + nl + json.dumps(action_schema, ensure_ascii=False, indent=1) + nl + "```" + nl * 2
        + "**도구 목록**(system에 함께 전달)" + nl + "```json" + nl
        + json.dumps([{k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS], ensure_ascii=False, indent=1)
        + nl + "```" + nl)

    def eff(c):
        return (c.get("reasoning") or {}).get("effort", "-")

    md = f"""# AI 전송 예시 — 오름이 OpenAI(gpt-6-luna)에 실제로 보내는 내용

> 상권 대표 시나리오("{QUESTION}")를 **제주데이터허브 실데이터**로 실행하면서 **오름 코드가 만든 요청 원문**을 그대로 옮긴 것입니다.
> 네트워크 호출 대신 기록용 스텁을 써서 만들었습니다(`tools/make_ai_example.py`).
> 실제 운영에서는 같은 내용이 왼쪽 메뉴의 「AI 전송 기록」에 호출마다 남습니다.

## 무엇을 보내고, 무엇을 보내지 않는가

| 보내는 것 | 보내지 않는 것 |
|---|---|
| 이용자 질문, 분석 조건(지역·업종·기간) | 원본 행(센터 CSV·허브 원자료의 개별 행) |
| 공개 명세 정보(지역·업종 이름 목록, 지표 정의) | 반출 검사에서 차단된 표, 10 미만 원값 |
| 반출 검사를 통과한 집계표 {n_tables}개(총 약 {max(n_rows, 0)}행) | API 키·개인정보·파일 경로 |

## 1) 계획 Agent — 1단계(도구 호출 결정)

- OpenAI Responses API · 모델 `{plan_1.get('model')}` · `reasoning.effort = {eff(plan_1)}` · `text.format = json_schema(strict)`

**system**
```
{plan_1.get('instructions', '').strip()}
```

**user**
```
{plan_1['input'].strip()}
```

**응답(Agent 행동 결정)**
```json
{json.dumps(STEP_1, ensure_ascii=False, indent=2)}
```

## 2) 계획 Agent — 2단계(도구 결과를 받고 확정)

- OpenAI Responses API · 모델 `{plan_2.get('model')}` · `reasoning.effort = {eff(plan_2)}` · `text.format = json_schema(strict)`

**system**
```
1단계와 같음
```

> 1단계에서 호출한 도구들의 실행 결과가 user 메시지 내 `[도구 결과]` 블록으로 포함되어 모델에 전달됩니다.

**user**
```
{plan_2['input'].strip()}
```

**응답(최종 계획 확정)**
```json
{json.dumps(STEP_2, ensure_ascii=False, indent=2)}
```

## 3) 해석·보고서 작성(Narrator) — 반출 검사 통과 집계표만

- 모델 `{write.get('model')}` · `reasoning.effort = {eff(write)}` · 응답 형식 엄격 JSON 스키마(headline·summary·findings·recommendations·business_text·limitations·export_purpose)
- 문장 속 수치(10 이상)는 이 집계표와 자동 대조하고, 근거가 없으면 한 번 다시 쓰게 합니다.

**system**
```
{write.get('instructions', '').strip()}
```

**user**(전송 원문)

{user_w.strip()}

{tools_sec}"""

    # API 키 및 로컬 절대 경로 제거 검사
    md = md.replace("stub-not-a-real-key", "[REDACTED_API_KEY]")
    md = re.sub(r"[A-Za-z]:\\[^\s`\'\"<>]+", "[LOCAL_PATH]", md)

    (ROOT / "docs" / "AI_전송_예시.md").write_text(md, encoding="utf-8")
    print("written", len(CALLS), "calls captured,", n_tables, "tables")


if __name__ == "__main__":
    main()

