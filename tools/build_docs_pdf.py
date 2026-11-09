"""docs/*.md, README.md → docs/pdf/*.pdf (Edge 헤드리스 인쇄). 개발용 도구 — markdown 패키지가 필요하다.

    uv run --with markdown python tools\\build_docs_pdf.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOCS = {
    ROOT / "README.md": "README_설치·실행_안내.pdf",
    ROOT / "docs" / "사용자_매뉴얼.md": "사용자_매뉴얼.pdf",
    ROOT / "docs" / "AI_API_설정_안내.md": "AI_API_설정_안내.pdf",
    ROOT / "docs" / "데이터_활용_명세.md": "데이터_활용_명세.pdf",
    ROOT / "docs" / "결과_설명자료.md": "결과_설명자료.pdf",
    ROOT / "docs" / "AI_전송_예시.md": "AI_전송_예시.pdf",
}

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm; }
body { font-family: "Pretendard", "Malgun Gothic", sans-serif; color: #14171A; font-size: 10pt; line-height: 1.62;
       word-break: keep-all; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { font-size: 19pt; letter-spacing: -.03em; margin: 0 0 4mm; padding-bottom: 3mm; border-bottom: 1.5px solid #14171A; }
h2 { font-size: 13.5pt; margin: 8mm 0 2.5mm; letter-spacing: -.02em; }
h3 { font-size: 11.5pt; margin: 5mm 0 2mm; }
h2::before { content: ""; display: inline-block; width: 2.2mm; height: 2.2mm; background: #E8772E; margin-right: 2mm; vertical-align: middle; }
p, li { margin: 1.2mm 0; }
blockquote { margin: 3mm 0; padding: 2mm 4mm; background: #F5F6F7; border-left: 2px solid #E8772E; color: #3C434B; }
table { border-collapse: collapse; width: 100%; margin: 2.5mm 0; font-size: 8.8pt; }
th { text-align: left; border-bottom: 1.2px solid #14171A; padding: 1.4mm 1.8mm; color: #535B64; }
td { border-bottom: 1px solid #E3E6E9; padding: 1.4mm 1.8mm; vertical-align: top; }
code { font-family: "Cascadia Mono", Consolas, monospace; font-size: 8.6pt; background: #F5F6F7; padding: 0 1mm; border-radius: 1mm; }
pre { background: #F5F6F7; padding: 3mm; border-radius: 2mm; white-space: pre-wrap; font-size: 8pt; line-height: 1.45; }
pre code { background: none; padding: 0; }
hr { border: 0; border-top: 1px solid #E3E6E9; margin: 5mm 0; }
"""


def main() -> None:
    from app.report.builder import html_to_pdf

    out_dir = ROOT / "docs" / "pdf"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for src, name in DOCS.items():
            body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
            html = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{src.stem}</title>'
                    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">'
                    f"<style>{CSS}</style></head><body>{body}</body></html>")
            h = Path(td) / f"{src.stem}.html"
            h.write_text(html, encoding="utf-8")
            r = html_to_pdf(h, out_dir / name, timeout=120)
            print(name, "OK" if r else "실패")


if __name__ == "__main__":
    main()
