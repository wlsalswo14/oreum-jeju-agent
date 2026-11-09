"""AI 연결 점검 — .env 의 키·모델로 '오름이 실제로 보내는 형태'의 요청을 1번 보내 확인한다.

    .venv\\Scripts\\python.exe tools\\check_ai.py      (또는 check_ai.bat 더블클릭)

- 실제 운영과 같은 코드 경로(app.agent.llm.LLM.json)로 호출: OpenAI Responses API · reasoning.effort · 엄격 JSON 스키마
- 키 값은 출력하지 않는다. 비용은 1원 미만.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.agent.llm import LLM, LLMUnavailable  # noqa: E402
from app.config import settings  # noqa: E402

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ok", "echo"],
    "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string"}},
}

HINTS = [
    ("인증 실패", "키가 틀렸거나 만료되었습니다. .env 의 OPENAI_API_KEY 를 다시 붙여 넣으세요(앞뒤 공백 없이)."),
    ("요청 한도", "결제(Billing) 등록·잔액 또는 사용 한도(Limits)를 확인하세요."),
    ("네트워크", "센터 방화벽·보안정책(DLP)에서 api.openai.com(HTTPS 443) 허용이 필요합니다."),
    ("API 오류(404)", "모델에 접근할 수 없습니다. OREUM_LLM_MODEL 값과 계정의 모델 접근 권한을 확인하세요(기본 gpt-6-luna)."),
    ("API 오류(400)", "요청 형식을 이 모델이 받지 않습니다. 오류 내용을 확인하고, 기본 모델(gpt-6-luna)·추론 강도(high)로 되돌려 보세요."),
    ("API 오류(403)", "프로젝트·조직 권한 또는 지역 제한을 확인하세요(키를 만든 프로젝트에 모델 사용 권한 필요)."),
    ("API 키 미설정", "키가 비어 있습니다. .env 의 OPENAI_API_KEY 에 키를 넣으세요."),
]


def main() -> int:
    p = settings.llm_provider
    print("오름 AI 연결 점검")
    print(f"  제공자: {p} · 모델: {settings.llm_model} · 추론 강도: 계획 {settings.llm_effort_plan} / 작성 {settings.llm_effort_write}")
    print(f"  키: {'설정됨' if settings.has_llm_key else '없음'} · AI 사용: {'켜짐' if settings.llm_enabled else '꺼짐(OREUM_LLM_ENABLED=0)'}")
    if not settings.llm_active:
        print("\n[확인] AI 키가 없거나 꺼져 있습니다 — 오름은 규칙 기반 문장으로 모든 기능이 동작합니다.")
        print("       AI를 쓰려면 .env 의 OPENAI_API_KEY 에 키를 넣고 다시 실행하세요.")
        return 1
    llm = LLM()
    t0 = time.time()
    try:
        out = llm.json("connection_check", "연결 확인용 요청입니다. 지시대로 JSON만 답하세요.",
                       "ok 는 true, echo 는 '오름' 으로 답하세요.", SCHEMA)
    except LLMUnavailable as e:
        msg = str(e)
        print(f"\n[실패] {msg}")
        for k, hint in HINTS:
            if k in msg:
                print(f"  → {hint}")
                break
        print("  분석은 규칙 기반 문장으로 계속 동작하지만, AI 해석·후속 질문 도구 선택은 쓰이지 않습니다.")
        return 1
    u = llm.usage_summary()
    ok = isinstance(out, dict) and out.get("ok") is True
    print(f"\n[{'정상' if ok else '확인'}] 응답 {time.time() - t0:.1f}초 · 입력 {u['input_tokens']} / 출력 {u['output_tokens']} 토큰"
          f" · 추정 비용 {u['cost_usd']}달러 · 응답 {out}")
    if ok:
        print("  엄격 JSON 스키마·추론 강도 설정이 그대로 받아들여졌습니다. 오름이 AI 모드로 동작합니다.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
