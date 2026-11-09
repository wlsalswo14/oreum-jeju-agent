"""설치 자가 점검: 센터 담당자가 install.bat 직후 결과를 확인할 수 있도록 한글로 출력."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = True


def line(flag: bool, msg: str, hint: str = ""):
    global ok
    mark = "[정상]" if flag else "[확인]"
    print(f"  {mark} {msg}" + (f"  → {hint}" if hint and not flag else ""))


def main() -> int:
    global ok
    print("\n오름(OREUM) 자가 점검")
    print(f"  Python {sys.version.split()[0]}")
    try:
        import fastapi, pandas, matplotlib, docx, jinja2  # noqa: F401
        line(True, "필수 패키지 설치")
    except Exception as e:  # pragma: no cover
        line(False, f"필수 패키지 설치 ({e})", "install.bat 다시 실행")
        ok = False
    try:
        from app.catalog.catalog import load_catalog

        c = load_catalog()
        n = len(getattr(c, "datasets", {}) or {})
        line(n > 0, f"데이터 명세서 카탈로그 {n}종")
    except Exception as e:
        line(False, f"카탈로그 로드 실패({e})")
        ok = False
    pub = list((ROOT / "data" / "public").glob("*.parquet"))
    line(bool(pub), f"제주데이터허브 공개 데이터 {len(pub)}종")
    from matplotlib import font_manager

    has_font = any(f.name == "Malgun Gothic" for f in font_manager.fontManager.ttflist)
    line(has_font, "한글 글꼴(맑은 고딕)", "차트 글자가 깨질 수 있음")
    from app.report.builder import find_browser

    b = find_browser()
    line(bool(b), f"PDF 변환용 브라우저({Path(b).name if b else '없음'})", "Edge 또는 Chrome 설치 시 PDF 생성, 없으면 HTML/DOCX만 생성")
    from app.config import settings

    line(settings.has_llm_key, f"AI 연결 설정({settings.llm_provider} · {settings.llm_model})" if settings.has_llm_key
         else "AI 키 미설정(규칙 기반으로 전 기능 동작)",
         ".env 의 OPENAI_API_KEY 입력 후 check_ai.bat 으로 연결 점검(docs/AI_API_설정_안내.md)")
    print("\n  점검 완료." if ok else "\n  일부 항목을 확인하세요.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
