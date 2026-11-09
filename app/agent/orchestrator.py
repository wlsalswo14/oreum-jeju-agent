"""에이전트 실행기(Plan → Data Guide → Ingest → Analyze → Guard → Narrate → Report).

LLM의 판단·자율 범위
- 판단: 질문 해석(분석 유형·지역·업종·행사·비교지역), 결과 해석 문장·제언 작성, 후속 질문 답변
- 자율 수행 안 함: 데이터 계산(로컬 결정적 코드), 반출 판정(규정 기반 코드), 파일 삭제·반출 신청(이용자 확인)
"""
from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..analysis import event_impact, trade_area, transit
from ..config import settings
from ..analysis.common import AnalysisResult
from ..guard import export_guard as eg
from ..guard.llm_gate import facts_from_results
from ..report import builder, charts
from ..session import Session
from . import resolve
from .llm import LLM
from .narrator import BUSINESS_TITLE, safe_write
from .planner import MODE_KO, Plan, make_plan

STATUS_KO = {"pass": "통과", "fixed": "처리 후 통과", "blocked": "차단"}


class UserFacingError(RuntimeError):
    """이용자에게 그대로 보여 줄 안내성 오류."""


class Agent:
    def __init__(self, session: Session):
        self.s = session
        self.llm = LLM(session)
        self.cache: dict = {}   # 마지막 분석의 메모리 데이터(후속 질문 도구용, 디스크 저장 안 함)

    # ------------------------------------------------------------------ 상태
    @property
    def st(self) -> dict:
        return self.s.state

    def progress(self, step: str, status: str, msg: str = ""):
        steps = self.st.setdefault("progress", [])
        for p in steps:
            if p["step"] == step:
                p.update(status=status, msg=msg, t=time.strftime("%H:%M:%S"))
                break
        else:
            steps.append({"step": step, "status": status, "msg": msg, "t": time.strftime("%H:%M:%S")})

    # ------------------------------------------------------------------ 1. 계획
    def plan(self, question: str) -> dict:
        self.st.clear()
        self.st["trace"] = []
        self.st["question"] = question
        self.progress("질문 해석", "running")
        plan = make_plan(question, self.llm, resolve.vocab(), self.st["trace"])
        resolved = resolve.resolve_plan(plan, question)
        self.st["plan"] = plan.to_dict()
        self.st["resolved"] = resolved
        self.progress("질문 해석", "done", f"{MODE_KO.get(plan.mode)} · 도구 {sum(1 for x in self.st['trace'] if x['kind']=='tool')}회")
        self.st["checklist"] = resolve.data_checklist(plan.mode, resolved)
        self._merge_ai_picks()
        self.refresh_checklist()
        return self.public_state()

    def update_params(self, changes: dict) -> dict:
        """이용자가 화면에서 지역·업종·기간 등을 수정."""
        plan = Plan(**{k: v for k, v in self.st["plan"].items() if k in Plan.__dataclass_fields__})
        for k, v in changes.items():
            if hasattr(plan, k):
                setattr(plan, k, v)
        resolved = resolve.resolve_plan(plan, self.st.get("question", ""), explicit=True)
        self.st["plan"] = plan.to_dict()
        self.st["resolved"] = resolved
        self.st["checklist"] = resolve.data_checklist(plan.mode, resolved)
        self._merge_ai_picks()
        self.refresh_checklist()
        return self.public_state()

    def _merge_ai_picks(self):
        picks = (self.st.get("plan") or {}).get("datasets") or []
        checklist = self.st.get("checklist", [])
        for item in checklist:
            ids = item.get("ids") or [item.get("id")]
            for pick in picks:
                if pick.get("id") in ids:
                    item["ai_pick"] = pick.get("reason", "")
        all_ids = set()
        for item in checklist:
            all_ids.update(item.get("ids") or [item.get("id")])
        for pick in picks:
            pid = pick.get("id")
            if pid and pid not in all_ids:
                checklist.append({
                    "id": pick["id"],
                    "ids": [pick["id"]],
                    "name": pick["name"],
                    "need": "suggested",
                    "status": "missing",
                    "how": ("제주데이터허브에서 내려받기" if pick.get("hub") else "센터 조회 화면에서 1개월 단위로 조회 → CSV 다운로드"),
                    "why": pick.get("reason", ""),
                    "ai_pick": pick.get("reason", ""),
                })
                all_ids.add(pid)

    # ------------------------------------------------------------------ 2. 데이터
    def ingest(self) -> dict:
        from ..ingest.loader import load_files

        files = sorted(p for p in self.s.upload_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
        rep = load_files(files) if files else None
        info = []
        if rep is not None:
            for did, ld in rep.datasets.items():
                info.append({
                    "id": did, "name": resolve.dataset_name(did), "rows": int(getattr(ld, "row_count", len(ld.df))),
                    "files": len(getattr(ld, "files", [])), "coverage": resolve.coverage_text(ld),
                    "missing": [str(x) for x in (getattr(ld, "missing_periods", []) or [])][:24],
                    "warnings": list(getattr(ld, "warnings", []) or [])[:5],
                    "synthetic": bool(getattr(ld, "is_synthetic", False)),
                    "source_label": getattr(ld, "source_label", resolve.dataset_name(did)),
                })
            unknown = [str(Path(getattr(u, 'path', u)).name) if not isinstance(u, str) else u
                       for u in (getattr(rep, "unrecognized", None) or [])]
            unknown += [f"{getattr(e, 'name', e)}: 읽기 오류({getattr(e, 'message', '')})" for e in (getattr(rep, "errors", None) or [])]
            dups = [getattr(d, "name", str(d)) for d in (getattr(rep, "duplicates", None) or [])]
            if dups:
                unknown.append(f"중복 파일 {len(dups)}개는 자동 제외됨: {', '.join(dups[:5])}{' 외' if len(dups) > 5 else ''}")
            self.st["unrecognized"] = unknown
        self.st["ingested"] = info
        self.refresh_checklist()
        return self.public_state()

    def refresh_checklist(self):
        have = {d["id"]: d for d in self.st.get("ingested", [])}
        for item in self.st.get("checklist", []):
            ids = item.get("ids", [item.get("id")])
            hit = [have[i] for i in ids if i in have]
            if item.get("auto") or item.get("need") == "auto":
                item["status"] = "auto"
            elif hit:
                item["status"] = "ok"
                item["have"] = "; ".join(h["coverage"] for h in hit)
            else:
                item["status"] = "missing"
                item.pop("have", None)

    # ------------------------------------------------------------------ 3~6. 실행
    def blocking_issues(self) -> list[str]:
        """실행 전에 반드시 채워야 할 항목(지역·업종·행사 기간 등)."""
        plan = self.st.get("plan") or {}
        rv = self.st.get("resolved") or {}
        issues = list(rv.get("issues") or [])
        if not plan:
            issues.append("먼저 질문을 입력해 분석 계획을 세워 주세요.")
        elif plan.get("mode") == "unsupported":
            issues.append("상권·축제·버스 분석 중 하나로 질문해 주세요.")
        return list(dict.fromkeys(issues))

    def run(self) -> dict:
        t0 = time.time()
        issues = self.blocking_issues()
        if issues:
            self.st["error"] = "분석 전에 확인이 필요합니다: " + " / ".join(issues)
            self.progress("오류", "error", "입력 확인 필요")
            return self.public_state()
        try:
            self._run()
            self.progress("완료", "done", f"{time.time() - t0:.0f}초")
        except UserFacingError as e:
            self.st["error"] = str(e)
            self.progress("오류", "error", "데이터 확인 필요")
        except Exception as e:  # 예상 못 한 오류도 이용자 언어로 안내하고, 기술 정보는 별도 보관
            self.st["error"] = ("분석 중 예상하지 못한 오류가 발생했습니다. 넣은 데이터의 종류·기간이 체크리스트와 맞는지 확인한 뒤 "
                                f"다시 실행해 주세요. (기술 정보: {type(e).__name__}: {e})")
            self.st["tb"] = traceback.format_exc()[-3000:]
            self.progress("오류", "error", "예상하지 못한 오류")
        return self.public_state()

    def _run(self):
        from ..ingest.loader import load_files

        plan = self.st["plan"]
        mode = plan["mode"]
        rv = self.st["resolved"]
        self.progress("데이터 불러오기", "running", "센터 CSV + 제주데이터허브(스냅샷에 없는 지역·기간은 Open API 조회, 1~2분)")
        files = sorted(p for p in self.s.upload_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
        rep = load_files(files) if files else None
        data: dict[str, pd.DataFrame] = {}
        synthetic = False
        labels: dict[str, str] = {}
        if rep is not None:
            for did, ld in rep.datasets.items():
                data[did] = ld.df
                synthetic |= bool(getattr(ld, "is_synthetic", False))
                labels[did] = getattr(ld, "source_label", did)
        pub = resolve.public_bundle(mode, rv)
        for k, v in pub.get("data", {}).items():
            data.setdefault(k, v)
        labels.update(pub.get("labels", {}))
        api_log = pub.get("log") or []
        self.st["hub_api_log"] = api_log
        self.progress("데이터 불러오기", "done", f"센터 데이터 {len(rep.datasets) if rep else 0}종 + 제주데이터허브 {len(pub.get('labels', {}))}종"
                      + (f" · Open API 직접 호출 {len(api_log)}건" if api_log else ""))

        self.progress("로컬 분석", "running")
        stops, usage, bus_src, stops_src = resolve.bus_inputs(data, pub)
        if mode == "trade_area":
            res = trade_area.analyze(data, rv["region_sel"], rv["industry_sel"], stores=pub.get("stores"),
                                     stops=stops, bus_usage=usage, bus_src=bus_src)
        elif mode == "event_impact":
            ev = rv["event"]
            res = event_impact.analyze(data, rv["region_sel"], date.fromisoformat(ev["start"]), date.fromisoformat(ev["end"]),
                                       ev["name"], control=rv.get("control_sel"), stops=stops, bus_usage=usage,
                                       venue=tuple(ev["venue"]) if ev.get("venue") else None, bus_src=bus_src,
                                       stops_src=stops_src)
        elif mode == "transit":
            res = transit.analyze(data, rv.get("region_sel"), stops, usage, src=bus_src, stops_src=stops_src,
                                  event_days=resolve.region_event_days(rv.get("region_sel")))
        else:
            raise UserFacingError("지원하지 않는 질문입니다. 상권·축제·버스 분석 질문으로 바꿔 주세요.")
        if not res.tables:
            need = [c["name"] for c in self.st.get("checklist", []) if c.get("need") == "required"]
            raise UserFacingError(
                "데이터 부족: 분석할 수 있는 자료가 없어 보고서를 만들지 않았습니다. "
                + (f"필수 데이터({', '.join(need)})의 기간·지역이 질문과 맞는지 확인해 주세요. " if need else "")
                + ("참고: " + " / ".join(res.notes) if res.notes else ""))
        self.progress("로컬 분석", "done", f"집계표 {len(res.tables)}개")

        self.progress("반출 기준 검사", "running")
        results = eg.check_all(res.tables)
        gmap = {r.table.key: r for r in results}
        n_fix = sum(r.status == "fixed" for r in results)
        n_block = sum(r.status == "blocked" for r in results)
        self.progress("반출 기준 검사", "done", f"통과 {len(results) - n_fix - n_block} · 처리 후 통과 {n_fix} · 차단 {n_block}")
        self.cache = {"data": data, "pub": pub, "rv": rv, "gmap": gmap, "mode": mode}

        self.progress("차트 생성", "running")
        chart_paths: dict[str, Path] = {}
        for spec in res.charts:
            gr = gmap.get(spec.table_key)
            if gr is None:
                continue
            p = charts.render(spec, gr, self.s.chart_dir)
            if p:
                chart_paths[spec.key] = p
        center = rv["region_sel"].center if rv.get("region_sel") else None
        mp = charts.render_map(res.map_points, f"{res.params.get('region', '')} 위치 지도", self.s.chart_dir, center)
        if mp:
            chart_paths["c.map"] = mp
        self.progress("차트 생성", "done", f"{len(chart_paths)}개(PNG)")

        kpis = self._kpi_values(res, gmap)
        self.progress("해석·보고서 문장 작성", "running")
        facts = facts_from_results(results)
        context = resolve.context_text(mode, rv, res)
        narrative, unverified, nmode = safe_write(
            self.llm, question=self.st["question"], mode=mode, context=context, facts=facts, notes=res.notes,
            cat_facts=res.facts, synthetic=synthetic, title=res.title, kpis=kpis, params=res.params,
            tables_present=set(gmap),
        )
        self.progress("해석·보고서 문장 작성", "done",
                      f"근거 미확인 수치 {len(unverified)}개" if unverified else "수치 근거 검증 통과")

        self.progress("보고서 생성", "running")
        out = self._build_outputs(res, results, gmap, chart_paths, kpis, narrative, unverified, nmode, synthetic,
                                  labels, plan)
        self.progress("보고서 생성", "done", ", ".join(Path(f).suffix[1:].upper() for f in out["files"].values() if f))
        self.st["result"] = out

    # ------------------------------------------------------------------ 보조
    def _kpi_values(self, res: AnalysisResult, gmap: dict) -> list[dict]:
        out = []
        for k in res.kpis:
            gr = gmap.get(k.table_key)
            if gr is None or not gr.ok:
                continue
            df = gr.table.df
            sel = pd.Series(True, index=df.index)
            for dim, val in k.row.items():
                if dim in df.columns:
                    sel &= df[dim].astype(str) == str(val)
            rows = df[sel]
            if rows.empty or k.col not in rows.columns:
                continue
            idx = rows.index[0]
            hidden = gr.suppressed_mask is not None and k.col in gr.suppressed_mask.columns and bool(gr.suppressed_mask.at[idx, k.col])
            v = rows.iloc[0][k.col]
            if hidden:
                txt = "비공개(<10)"
            elif pd.isna(v):
                continue
            else:
                txt = _fmt_kpi(float(v), k.hint)
            out.append({"label": k.label, "value": txt, "table": k.table_key})
        return out

    def _build_outputs(self, res, results, gmap, chart_paths, kpis, narrative, unverified, nmode, synthetic, labels, plan):
        out_dir = self.s.out_dir
        # 그림 배치: finding의 evidence 표에 해당하는 차트를 붙임
        used = set()
        key_of_table = {}
        for spec in res.charts:
            key_of_table.setdefault(spec.table_key, []).append(spec.key)
        findings = []
        for f in narrative.get("findings", []):
            figs = []
            for tk in f.get("evidence", []):
                for ck in key_of_table.get(tk, []):
                    if ck in chart_paths and ck not in used:
                        used.add(ck)
                        figs.append({"key": ck, "src": builder.img_data_uri(chart_paths[ck]),
                                     "caption": _caption(res, ck), "url": f"charts/{chart_paths[ck].name}"})
            findings.append({**f, "figures": figs})
        extra = [{"key": k, "src": builder.img_data_uri(p), "caption": _caption(res, k), "url": f"charts/{p.name}"}
                 for k, p in chart_paths.items() if k not in used]
        narrative = {**narrative, "findings": findings}
        tables_ctx = []
        for r in results:
            if not r.ok:
                continue
            disp = r.display_df(max_rows=40)
            tables_ctx.append({"key": r.table.key, "title": r.table.title, "status": r.status,
                               "status_ko": STATUS_KO[r.status], "note": r.table.note, "df": disp,
                               "html": _html_table(disp, {r.table.labels.get(c, c) for c in r.table.values})})
        guard_rows = [{"title": r.table.title, "status": r.status, "status_ko": STATUS_KO[r.status], "method": r.method,
                       "actions": " / ".join(r.actions), "basis": r.basis, "key": r.table.key,
                       "n_suppressed": r.n_suppressed, "n_cells": r.n_cells} for r in results]
        purpose = narrative.get("export_purpose") or plan.get("purpose", "")
        export_rows = eg.export_request_rows(results, labels, purpose)
        src_ids = list(dict.fromkeys(res.used_sources))
        sources = [{"id": s, "label": labels.get(s, resolve.dataset_name(s)), "grade": resolve.export_grade(s)} for s in src_ids]
        usage = self.llm.usage_summary()
        usage["mode"] = nmode
        ctx = {
            "title": res.title, "question": self.st["question"], "target": res.params.get("region", ""),
            "period": res.params.get("period") or (f"{res.params.get('start')}~{res.params.get('end')}" if res.params.get("start") else "-"),
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"), "synthetic": synthetic, "narrative": narrative,
            "kpis": kpis[:8], "tables": tables_ctx, "guard": guard_rows, "export_rows": export_rows, "sources": sources,
            "notes": res.notes, "ai": usage, "unverified": ", ".join(unverified), "extra_figures": extra,
            "business_title": BUSINESS_TITLE.get(res.mode, "활용 문안"),
        }
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        base = f"오름_{_safe(res.title)}_{stamp}"
        html_p = builder.render_html(ctx, out_dir / f"{base}.html")
        pdf_p = builder.html_to_pdf(html_p, out_dir / f"{base}.pdf")
        docx_p = builder.build_docx(ctx, chart_paths, out_dir / f"{base}.docx")
        pkg = builder.package_zip([pdf_p, docx_p, *chart_paths.values()], out_dir / f"{base}_반출패키지.zip")
        files = {"pdf": pdf_p.name if pdf_p else None, "docx": docx_p.name, "html": html_p.name, "zip": pkg.name}
        ui_tables = [{"key": t["key"], "title": t["title"], "status": t["status"], "status_ko": t["status_ko"],
                      "note": t["note"], "columns": [str(c) for c in t["df"].columns],
                      "rows": t["df"].astype(str).values.tolist()} for t in tables_ctx]
        ui_find = [{k: v for k, v in f.items() if k != "figures"} | {"figures": [{"url": g["url"], "caption": g["caption"]} for g in f["figures"]]}
                   for f in narrative["findings"]]
        result = {
            "mode": res.mode, "title": res.title, "params": _jsonable(res.params), "kpis": kpis,
            "narrative": {**{k: v for k, v in narrative.items() if k != "findings"}, "findings": ui_find},
            "extra_figures": [{"url": g["url"], "caption": g["caption"]} for g in extra],
            "tables": ui_tables, "guard": guard_rows, "export_rows": export_rows, "sources": sources,
            "notes": res.notes, "map_points": res.map_points[:1500], "usage": usage, "unverified": unverified,
            "synthetic": synthetic, "files": files, "business_title": ctx["business_title"],
            "llm_sent_tables": facts_from_results(results).table_keys,
        }
        (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        return result

    # ------------------------------------------------------------------ 후속 질문
    def followup(self, question: str) -> dict:
        res = self.st.get("result")
        if not res:
            return {"answer": "먼저 분석을 실행해 주세요."}
        if not self.llm.available:
            if not self.cache:
                return {"answer": "분석을 다시 실행한 뒤 질문해 주세요(서버가 다시 시작되어 메모리의 분석 데이터가 없습니다).", "tools": []}
            from .tools import ToolBox

            return ToolBox(self).rule_answer(question)
        facts_txt = "\n\n".join(
            f"### [{t['key']}] {t['title']}\n| " + " | ".join(t["columns"]) + " |\n" +
            "\n".join("| " + " | ".join(r) + " |" for r in t["rows"][:25]) for t in res["tables"])
        from .llm import LLMUnavailable
        from .narrator import unverified_numbers
        from .tools import TOOLS, ToolBox

        from .prompts import FOLLOWUP_LAST, FOLLOWUP_SYSTEM, FOLLOWUP_TOOL_FORMAT
        system = FOLLOWUP_SYSTEM
        user = (f"[분석 제목] {res['title']}\n[이전 결론] {res['narrative'].get('summary', '')}\n"
                f"[사용 가능한 표 키] {', '.join(t['key'] for t in res['tables'])}\n\n[근거 집계표(일부 행)]\n{facts_txt}\n\n[질문] {question}")
        if not self.cache:  # 서버 재시작 등으로 메모리 데이터가 없으면 도구 없이 답변
            try:
                ans = self.llm.text("followup", system, user, effort=settings.llm_effort_write, max_tokens=3000)
            except LLMUnavailable as e:
                return {"answer": f"지금은 답을 만들 수 없습니다. 잠시 후 다시 질문해 주세요. ({type(e).__name__})"}
            return {"answer": ans, "unverified": unverified_numbers(ans, facts_txt + question), "tools": [],
                    "usage": self.llm.usage_summary()}
        box = ToolBox(self)
        names = [t["name"] for t in TOOLS]
        action_schema = {
            "type": "object", "additionalProperties": False, "required": ["action", "tool", "arguments", "answer"],
            "properties": {
                "action": {"type": "string", "enum": ["call_tool", "answer"]},
                "tool": {"type": "string", "enum": names + [""]},
                "arguments": {"type": "string"},
                "answer": {"type": "string"},
            },
        }
        catalog = "\n".join(f"- {t['name']}: {t['description']} 입력 스키마: "
                             + json.dumps(t["input_schema"].get("properties", {}), ensure_ascii=False) for t in TOOLS)
        system_t = system + FOLLOWUP_TOOL_FORMAT.format(catalog=catalog)
        used, evidence, transcript, ans = [], [facts_txt, question], "", ""
        try:
            for i in range(6):
                last = i == 5
                sys_i = system_t + (FOLLOWUP_LAST if last else "")
                act = self.llm.json(f"followup_{i + 1}", sys_i, user + transcript, action_schema,
                                    effort=settings.llm_effort_write, max_tokens=4000)
                if act.get("action") == "call_tool" and act.get("tool") in names and not last:
                    try:
                        args = json.loads(act.get("arguments") or "{}")
                        if not isinstance(args, dict):
                            raise ValueError
                    except ValueError:
                        args = {}
                    out = box.run(act["tool"], args)
                    self.st.setdefault("trace", []).append({"t": time.strftime("%H:%M:%S"), "kind": "tool", "title": f"후속 질문 · {act['tool']}", "by": "도구", "detail": question[:60], "tool": act["tool"], "args": args, "sec": None})
                    used.append({"tool": act["tool"], "input": args})
                    evidence.append(out)
                    transcript += (f"\n\n[도구 호출 {i + 1}] {act['tool']} {json.dumps(args, ensure_ascii=False)}"
                                   f"\n[도구 결과 {i + 1}]\n{out}")
                    continue
                ans = (act.get("answer") or "").strip()
                break
        except Exception as e:  # 장애 시 규칙 기반 도구 응답으로 전환(서비스 중단 없음)
            out = box.rule_answer(question)
            out["note"] = f"자동 해석 실패로 기본 도구 응답({type(e).__name__})"
            return out
        if not ans:
            ans = "도구 호출을 여러 번 했지만 답을 정리하지 못했습니다. 질문을 조금 더 구체적으로 해 주세요."
        return {"answer": ans, "tools": used, "unverified": unverified_numbers(ans, "\n".join(evidence)),
                "usage": self.llm.usage_summary()}

    # ------------------------------------------------------------------ 화면용 상태
    def public_state(self) -> dict:
        st = self.st
        rv = st.get("resolved") or {}
        return {
            "question": st.get("question"), "plan": st.get("plan"), "resolved": resolve.public_resolved(rv),
            "checklist": st.get("checklist", []), "ingested": st.get("ingested", []),
            "unrecognized": st.get("unrecognized", []), "progress": st.get("progress", []),
            "result": st.get("result"), "error": st.get("error"),
            "trace": st.get("trace", []),
            "usage": self.llm.usage_summary(),
        }


def _fmt_kpi(v: float, hint: str) -> str:
    if hint == "원":
        if abs(v) >= 1e8:
            return f"{v / 1e8:,.1f}억 원"
        if abs(v) >= 1e6:
            return f"{v / 1e4:,.0f}만 원"
        return f"{v:,.0f}원"
    if hint in ("%p", "%±"):
        return f"{v:+,.1f}{hint[0]}{'p' if hint == '%p' else ''}"
    if hint == "%":
        return f"{v:,.1f}%"
    if hint in ("위", "개", "곳", "명"):
        return f"{v:,.0f}{hint}"
    return f"{v:,.1f}{hint}"


def _caption(res: AnalysisResult, chart_key: str) -> str:
    if chart_key == "c.map":
        return "위치 지도(정류소·동종 점포 좌표, 반출 가능·공개 데이터)"
    for c in res.charts:
        if c.key == chart_key:
            return c.title
    return chart_key


def _html_table(df: pd.DataFrame, num_cols: set | None = None) -> str:
    head = "".join(f'<th{" class=num" if num_cols and c in num_cols else ""}>{_esc(c)}</th>' for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c, v in zip(df.columns, r.values):
            s = str(v)
            cls = ' class="num"' if (num_cols is not None and c in num_cols) else ""
            cells.append(f"<td{cls}>{_esc(s)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><tr>{head}</tr>{''.join(rows)}</table>"


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in '\\/:*?"<>|').replace(" ", "_")[:40]


def _jsonable(d: dict) -> dict:
    return json.loads(json.dumps(d, ensure_ascii=False, default=str))
