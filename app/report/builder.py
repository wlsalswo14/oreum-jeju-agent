"""보고서 생성: HTML → PDF(Edge/Chrome 헤드리스 인쇄), DOCX(편집용).

반출 정책상 보고서(PDF·한글/워드)와 그림파일(PNG)은 반출 장려 형식이고,
텍스트(csv/txt)·스프레드시트(엑셀)는 원칙적으로 반출 불가이므로 만들지 않는다.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def find_browser() -> str | None:
    cands = [settings.browser_path] if settings.browser_path else []
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA", "")
    cands += [
        rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Google\Chrome\Application\chrome.exe",
        rf"{pf86}\Google\Chrome\Application\chrome.exe",
        rf"{local}\Google\Chrome\Application\chrome.exe",
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        p = shutil.which(name)
        if p:
            return p
    return None


def img_data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def render_html(ctx: dict, out: Path) -> Path:
    html = _env.get_template("report.html").render(**ctx)
    out.write_text(html, encoding="utf-8")
    return out


def html_to_pdf(html_path: Path, pdf_path: Path, timeout: int = 60, retries: int = 1) -> Path | None:
    """Edge/Chrome 헤드리스 인쇄로 PDF 생성.

    헤드리스 브라우저가 PDF를 다 쓴 뒤에도 종료되지 않는 경우가 있어, 파일이 완성(크기 안정)되면 즉시 프로세스를
    정리하고 반환한다. 호출마다 임시 프로필을 따로 써서 이전 실행과 충돌하지 않게 한다.
    """
    import tempfile
    import time

    browser = find_browser()
    if not browser:
        return None
    for _ in range(retries + 1):
        pdf_path.unlink(missing_ok=True)
        user_dir = Path(tempfile.mkdtemp(prefix="oreum_pdf_"))
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-first-run",
            f"--user-data-dir={user_dir}", "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri(),
        ]
        try:
            subprocess.run(cmd, timeout=timeout, capture_output=True, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            # PDF는 다 썼는데 브라우저가 안 끝나는 경우: 이 임시 프로필로 뜬 프로세스만 정리
            if os.name == "nt":
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
                                + user_dir.name + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            time.sleep(1)
        except OSError:
            pass
        finally:
            shutil.rmtree(user_dir, ignore_errors=True)
        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            return pdf_path
    return None


def build_docx(ctx: dict, chart_paths: dict[str, Path], out: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Malgun Gothic"
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
    st.font.size = Pt(10)
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2.0)
        s.top_margin = s.bottom_margin = Cm(1.8)

    p = doc.add_paragraph()
    r = p.add_run("OREUM · 제주 지역경제 분석 AI Agent")
    r.font.color.rgb = RGBColor(0xF2, 0x8C, 0x28)
    r.bold = True
    doc.add_heading(ctx["title"], level=0)
    doc.add_paragraph(f"분석 대상: {ctx['target']} · 분석 기간: {ctx['period']} · 작성: {ctx['created']}")
    doc.add_paragraph(f"질문: {ctx['question']}")
    if ctx.get("synthetic"):
        w = doc.add_paragraph().add_run("※ 대회 시연용 가상(SYNTHETIC) 센터 데이터로 작성된 보고서입니다(실제 수치 아님).")
        w.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
        w.bold = True
    n = ctx["narrative"]
    h = doc.add_paragraph().add_run(n["headline"])
    h.bold = True
    h.font.size = Pt(13)
    doc.add_paragraph(n["summary"])
    if ctx.get("kpis"):
        t = doc.add_table(rows=1, cols=len(ctx["kpis"]))
        t.style = "Light Grid Accent 1"
        for i, k in enumerate(ctx["kpis"]):
            c = t.rows[0].cells[i]
            c.text = f"{k['label']}\n{k['value']}"
    doc.add_heading("1. 주요 분석 결과", level=1)
    for i, f in enumerate(n["findings"], 1):
        doc.add_paragraph().add_run(f"{i}. {f['title']}").bold = True
        doc.add_paragraph(f["detail"])
        for fig in f.get("figures", []):
            pth = chart_paths.get(fig["key"])
            if pth and Path(pth).exists():
                doc.add_picture(str(pth), width=Cm(16))
                cap = doc.add_paragraph(fig["caption"])
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading("2. 제언", level=1)
    for rcm in n["recommendations"]:
        doc.add_paragraph(f"{rcm['title']} — {rcm['detail']}", style="List Bullet")
    if n.get("business_text"):
        doc.add_heading(f"3. {ctx.get('business_title', '활용 문안')}", level=1)
        doc.add_paragraph(n["business_text"])
    doc.add_heading("한계 및 유의사항", level=1)
    for l in list(n["limitations"]) + list(ctx.get("notes", [])):
        doc.add_paragraph(l, style="List Bullet")
    doc.add_heading("부록. 분석 집계표(반출 검사 통과본)", level=1)
    for tb in ctx["tables"]:
        doc.add_paragraph().add_run(f"[{tb['key']}] {tb['title']} ({tb['status_ko']})").bold = True
        df = tb["df"]
        tbl = doc.add_table(rows=1, cols=len(df.columns))
        tbl.style = "Light List Accent 1"
        for j, c in enumerate(df.columns):
            tbl.rows[0].cells[j].text = str(c)
        for _, row in df.head(30).iterrows():
            cells = tbl.add_row().cells
            for j, v in enumerate(row.values):
                cells[j].text = str(v)
    doc.add_heading("부록. 반출 신청서 초안", level=1)
    for r_ in ctx["export_rows"]:
        doc.add_paragraph(f"{r_['자료']} | 출처: {r_['출처(원본데이터 명칭)']} | 활용 사유: {r_['활용 사유']}", style="List Bullet")
    ai = ctx["ai"]
    if ai.get("calls"):
        doc.add_paragraph(f"AI 활용: {ai['model']} · 호출 {ai['calls']}회 · 추정 비용 약 {ai['cost_krw']}원. "
                          "원본 데이터는 AI에 전송하지 않았고 반출 검사를 통과한 집계표만 사용함.")
    doc.save(out)
    return out


def package_zip(files: list[Path], out: Path) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f and Path(f).exists():
                arc = f"charts/{Path(f).name}" if Path(f).suffix == ".png" else Path(f).name
                z.write(f, arc)
    return out
