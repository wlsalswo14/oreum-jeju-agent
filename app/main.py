"""오름(OREUM) 로컬 웹 서버 — http://127.0.0.1:8765

센터 PC에서 run.bat 로 실행하면 브라우저가 자동으로 열린다.
외부 접속을 막기 위해 기본 바인딩은 127.0.0.1(로컬 전용).
"""
from __future__ import annotations

import mimetypes
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent.orchestrator import Agent
from .config import WEB_DIR, settings
from .session import store

app = FastAPI(title="오름 OREUM", version="1.0.0", docs_url=None, redoc_url=None)
_agents: dict[str, Agent] = {}
_running: dict[str, threading.Thread] = {}
_planning: dict[str, threading.Thread] = {}
MAX_UPLOAD_MB = 1024


def agent_of(sid: str) -> Agent:
    try:
        s = store.get(sid)
    except KeyError:
        raise HTTPException(404, "세션이 없습니다. 새로 시작해 주세요.")
    a = _agents.get(sid)
    if a is None:
        a = _agents[sid] = Agent(s)
    return a


class Q(BaseModel):
    question: str


class Params(BaseModel):
    changes: dict


@app.get("/api/status")
def status():
    from .agent import resolve

    return {
        "llm": {"active": settings.llm_active, "model": settings.llm_model, "has_key": settings.has_llm_key,
                "enabled": settings.llm_enabled},
        "hub_key": bool(settings.hub_project_key),
        "demo_scenarios": resolve.demo_scenarios(),
        "vocab": {k: v for k, v in resolve.vocab().items() if k in ("regions", "industries")},
        "min_cell": settings.min_cell,
        "version": app.version,
    }


@app.post("/api/session")
def new_session():
    s = store.create()
    _agents[s.sid] = Agent(s)
    return {"sid": s.sid}


@app.post("/api/{sid}/plan")
def plan(sid: str, q: Q):
    question = q.question.strip()[:500]
    if not question:
        raise HTTPException(400, "질문을 입력해 주세요.")
    t = _planning.get(sid)
    if t and t.is_alive():
        return {"planning": True}
    a = agent_of(sid)
    a.st.clear()
    a.st["question"] = question
    a.st["trace"] = []
    a.st["progress"] = [{"step": "질문 해석", "status": "running", "msg": "", "t": time.strftime("%H:%M:%S")}]

    def _do_plan():
        try:
            a.plan(question)
        except Exception as e:
            a.st["error"] = f"질문 해석 중 오류: {e}"
            a.progress("질문 해석", "error", str(e))

    t = threading.Thread(target=_do_plan, daemon=True)
    _planning[sid] = t
    t.start()
    return {"planning": True}


@app.post("/api/{sid}/params")
def params(sid: str, p: Params):
    return agent_of(sid).update_params(p.changes)


@app.post("/api/{sid}/upload")
async def upload(sid: str, files: list[UploadFile] = File(...)):
    a = agent_of(sid)
    n = 0
    for f in files:
        name = Path(f.filename or "upload.csv").name
        if not name.lower().endswith(".csv"):
            continue  # 센터 반입 규정: CSV 파일만
        dst = a.s.upload_dir / name
        with dst.open("wb") as out:
            shutil.copyfileobj(f.file, out, length=1024 * 1024)
        if dst.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
            dst.unlink(missing_ok=True)
            raise HTTPException(413, f"{name}: 파일이 너무 큽니다.")
        n += 1
    st = a.ingest()
    st["uploaded"] = n
    return st


@app.delete("/api/{sid}/upload/{name}")
def delete_upload(sid: str, name: str):
    a = agent_of(sid)
    p = (a.s.upload_dir / Path(name).name)
    p.unlink(missing_ok=True)
    return a.ingest()


@app.post("/api/{sid}/run")
def run(sid: str):
    a = agent_of(sid)
    t = _running.get(sid)
    if t and t.is_alive():
        return {"running": True}
    issues = a.blocking_issues()
    if issues:
        raise HTTPException(400, "분석 전에 확인이 필요합니다: " + " / ".join(issues))
    a.st.pop("result", None)
    a.st.pop("error", None)
    a.st["progress"] = [p for p in a.st.get("progress", []) if p["step"] == "질문 해석"]
    t = threading.Thread(target=a.run, daemon=True)
    _running[sid] = t
    t.start()
    return {"running": True}


@app.get("/api/{sid}/state")
def state(sid: str):
    a = agent_of(sid)
    st = a.public_state()
    t = _running.get(sid)
    st["running"] = bool(t and t.is_alive())
    tp = _planning.get(sid)
    st["planning"] = bool(tp and tp.is_alive())
    return st


@app.post("/api/{sid}/ask")
def ask(sid: str, q: Q):
    return agent_of(sid).followup(q.question.strip()[:500])


@app.get("/api/{sid}/audit")
def audit(sid: str):
    a = agent_of(sid)
    recs = a.s.read_audit()
    # 합계는 저장된 기록에서 계산(서버 재시작·세션 재접속 뒤에도 정확)
    usd = sum(float(r.get("usd") or 0) for r in recs)
    usage = {"calls": sum(1 for r in recs if not r.get("error")),
             "input_tokens": sum(int(r.get("input_tokens") or 0) for r in recs),
             "output_tokens": sum(int(r.get("output_tokens") or 0) for r in recs),
             "cost_usd": round(usd, 4), "cost_krw": int(round(usd * 1400))}
    return {"records": recs, "usage": usage}


@app.get("/api/{sid}/file/{name}")
def get_file(sid: str, name: str):
    a = agent_of(sid)
    p = (a.s.out_dir / Path(name).name).resolve()
    if not p.exists() or a.s.out_dir.resolve() not in p.parents:
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.get("/api/{sid}/charts/{name}")
def get_chart(sid: str, name: str):
    a = agent_of(sid)
    p = (a.s.chart_dir / Path(name).name).resolve()
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.post("/api/{sid}/keep/{name}")
def keep(sid: str, name: str):
    """반출 승인 후 보존이 필요한 결과물만 approved_outputs 로 이동(센터 보존 정책)."""
    try:
        dst = store.keep(sid, name)
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, "세션 또는 파일을 찾을 수 없습니다.")
    return {"kept": str(dst.name)}


@app.post("/api/{sid}/cleanup")
def cleanup(sid: str):
    """퇴실 정리: 업로드한 CSV·중간 결과·보고서·AI 감사기록 전체 삭제."""
    _agents.pop(sid, None)
    return store.cleanup(sid)


@app.exception_handler(Exception)
async def _err(request, exc):
    if isinstance(exc, HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return JSONResponse({"detail": f"서버 오류: {type(exc).__name__}: {exc}"}, status_code=500)


mimetypes.add_type("font/woff2", ".woff2")          # 동봉 글꼴(Windows 레지스트리에 없을 수 있음)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def main():
    import webbrowser

    import uvicorn

    url = f"http://{settings.host}:{settings.port}/"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  오름(OREUM) 실행 중 → {url}\n  종료: 이 창에서 Ctrl+C\n")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
