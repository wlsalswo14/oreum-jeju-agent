/* 오름(OREUM) 프런트엔드 — 빌드 도구 없이 동작하는 순수 JS */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let SID = sessionStorage.getItem("oreum_sid");
let STATUS = null;
let STATE = null;
let poll = null;
let planPoll = null;
let mapObj = null;
let traceShown = 0;
let curTab = "find";

const TOOL_KO = {
  find_region: "지역 찾기",
  find_industry: "업종 찾기",
  find_festival: "축제 찾기",
  search_datasets: "데이터 검색",
  get_dataset: "데이터 상세",
  compare_regions: "지역 비교",
  rerun_analysis: "조건 바꿔 재분석",
  get_table: "표 조회",
  explain_method: "방법 설명"
};

async function api(path, opt = {}) {
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch { }
  if (!r.ok) throw new Error((j && j.detail) || `요청 실패(${r.status})`);
  return j;
}
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
function toast(msg, ms = 2600) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms); }

async function ensureSession() {
  if (SID) { try { await api(`/api/${SID}/state`); return; } catch { SID = null; } }
  const j = await post("/api/session");
  SID = j.sid; sessionStorage.setItem("oreum_sid", SID);
}

function go(step) {
  document.body.dataset.step = step;
  for (let i = 1; i <= 4; i++) $(`#p${i}`).classList.toggle("hidden", i !== step);
  $$("#stepper li").forEach((li) => {
    const n = +li.dataset.step;
    li.classList.toggle("active", n === step);
    li.classList.toggle("done", n < step);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------- 상단 상태 ---------------- */
function renderChips() {
  const c = $("#chips");
  const llm = STATUS.llm;
  c.innerHTML = `
    <span class="chip">반출 기준: ${STATUS.min_cell} 미만 자동 처리</span>
    <span class="chip">세션 ${esc(SID?.slice(-6) || "")}</span>`;
}

function renderExamples() {
  const box = $("#examples");
  const sc = STATUS.demo_scenarios || [];
  box.innerHTML = sc.map((s) => `<button class="ex ${esc(s.id)}" data-q="${esc(s.question)}" data-sc="${esc(s.id)}"><span class="k">${esc(s.label)}</span><span class="q">${esc(s.question)}</span><span class="ic">${ICONS[s.id] || ICONS.trade}</span></button>`).join("");
  $$(".ex", box).forEach((b) => b.onclick = () => { $("#question").value = b.dataset.q; DEMO_HINT = b.dataset.sc; doPlan(); });
}
let DEMO_HINT = null;
const ICONS = {
  trade: '<svg viewBox="0 0 24 24"><path d="M4 10v9h16v-9M3 10l1.6-5h14.8L21 10a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0M10 19v-5h4v5"/></svg>',
  event: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4c4-2 7 2 11 0v8c-4 2-7-2-11 0"/><path d="M18.5 15.5l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z"/></svg>',
  transit: '<svg viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 11h14M8 21v-4M16 21v-4M8.5 14h.01M15.5 14h.01"/></svg>',
};

/* ---------------- 1. 계획 ---------------- */
function traceItemHtml(t, isNew = false) {
  const kind = esc(t.kind || "note");
  const tool = t.tool;
  const title = t.title || "";
  const detail = t.detail;
  const sec = t.sec;
  const chip = tool ? `<span class="tchip">${esc(TOOL_KO[tool] || tool)}</span>` : "";
  const det = detail ? `<div class="tdet">${esc(detail)}</div>` : "";
  const s = (t.kind !== "tool" && sec != null) ? `${esc(sec)}초` : "";
  const cls = `tr k-${kind}${isNew ? " new" : ""}`;
  return `<li class="${cls}"><span class="tdot"></span><div class="tbody"><div class="ttl">${chip}${esc(title)}</div>${det}</div><span class="tsec">${s}</span></li>`;
}

function traceHtml(trace, live, animate = false) {
  const items = (trace || []).map((t) => traceItemHtml(t, animate)).join("");
  const wait = live ? '<li class="tr k-wait"><span class="tdot"></span><div class="tbody"><div class="ttl">오름이 확인하는 중…</div></div></li>' : "";
  const cls = animate ? "trace" : "trace static";
  return `<ol class="${cls}">${items}${wait}</ol>`;
}

function startPlanPoll() {
  if (planPoll) { clearInterval(planPoll); planPoll = null; }
  const b = $("#btnPlan");
  b.disabled = true; b.classList.add("loading"); $(".lbl", b).textContent = "오름이 확인하는 중…";
  $("#p1").classList.add("has-plan");
  const planBox = $("#planBox");
  if (!planBox.querySelector(".plan.working")) {
    planBox.innerHTML = `<div class="plan working"><div class="plan-head"><span class="spark"></span><span class="mode-badge">작업 중</span><span class="src-badge">오름이 도구로 지역·업종·데이터를 직접 확인합니다</span></div>${traceHtml(STATE?.trace || [], true)}</div>`;
    traceShown = (STATE?.trace || []).length;
  }

  planPoll = setInterval(async () => {
    try {
      const st = await api(`/api/${SID}/state`);
      STATE = st;
      const trace = st.trace || [];
      if (trace.length > traceShown) {
        const wait = $("#planBox .trace .k-wait");
        const newHtml = trace.slice(traceShown).map((t) => traceItemHtml(t, true)).join("");
        if (wait) {
          wait.insertAdjacentHTML("beforebegin", newHtml);
        } else {
          const el = $("#planBox .trace");
          if (el) el.insertAdjacentHTML("beforeend", newHtml);
        }
        traceShown = trace.length;
      }
      renderTimeline();
      if (!st.planning) {
        clearInterval(planPoll);
        planPoll = null;
        b.disabled = false; b.classList.remove("loading"); $(".lbl", b).textContent = "분석 계획 세우기";
        if (st.error) toast(st.error);
        else renderPlan();
      }
    } catch (e) {
      clearInterval(planPoll);
      planPoll = null;
      b.disabled = false; b.classList.remove("loading"); $(".lbl", b).textContent = "분석 계획 세우기";
      toast(e.message);
    }
  }, 700);
}

async function doPlan() {
  const q = $("#question").value.trim();
  if (!q) { toast("질문을 입력해 주세요."); return; }
  traceShown = 0;
  if (planPoll) { clearInterval(planPoll); planPoll = null; }
  const b = $("#btnPlan");
  b.disabled = true; b.classList.add("loading"); $(".lbl", b).textContent = "오름이 확인하는 중…";
  try {
    await post(`/api/${SID}/plan`, { question: q });
    $("#p1").classList.add("has-plan");
    $("#planBox").innerHTML = `<div class="plan working"><div class="plan-head"><span class="spark"></span><span class="mode-badge">작업 중</span><span class="src-badge">오름이 도구로 지역·업종·데이터를 직접 확인합니다</span></div>${traceHtml([], true)}</div>`;
    startPlanPoll();
  } catch (e) {
    b.disabled = false; b.classList.remove("loading"); $(".lbl", b).textContent = "분석 계획 세우기";
    toast(e.message);
  }
}

function opt(list, val) {
  return `<option value="" ${val ? "" : "selected"}>업종을 선택하세요</option>` +
    list.map((x) => `<option ${x === val ? "selected" : ""}>${esc(x)}</option>`).join("");
}

function renderPlan() {
  const p = STATE.plan, r = STATE.resolved || {};
  const v = STATUS.vocab || { regions: [], industries: [] };
  let fields = "";
  if (p.mode === "trade_area") {
    fields = `
      <div class="field"><label>지역(법정동·읍면)</label><input id="f_region" list="dl_regions" value="${esc(r.region || "")}"></div>
      <div class="field"><label>업종(비씨카드 42업종)</label><select id="f_industry">${opt(v.industries, r.industry || "")}</select></div>
      <div class="field"><label>비교 범위</label><input value="제주 전체 법정동(같은 업종)" disabled></div>
      <div class="field"><label>이용자 유형</label><input value="${esc(p.user_type)}" disabled></div>`;
  } else if (p.mode === "event_impact") {
    fields = `
      <div class="field"><label>행사명</label><input id="f_event" value="${esc(r.event_name || p.event_name || "")}"></div>
      <div class="field"><label>시작일</label><input id="f_start" type="date" value="${esc(r.event_start || "")}"></div>
      <div class="field"><label>종료일</label><input id="f_end" type="date" value="${esc(r.event_end || "")}"></div>
      <div class="field"><label>행사 지역(읍면동)</label><input id="f_region" list="dl_regions" value="${esc(r.region || "")}"></div>
      <div class="field"><label>비교 지역(이웃 읍면)</label><input id="f_control" list="dl_regions" value="${esc(r.control || "")}"></div>`;
  } else if (p.mode === "transit") {
    fields = `
      <div class="field"><label>지역(비우면 제주 전역)</label><input id="f_region" list="dl_regions" value="${esc(r.region || "")}"></div>
      <div class="field"><label>반경(km)</label><input id="f_radius" type="number" step="0.5" min="0.5" max="10" value="${esc(r.radius_km || 3)}"></div>`;
  }
  const issues = r.issues || [];
  const clar = [...(p.clarify || []).filter((c) => issues.length), ...issues];
  const warns = r.warnings || [];
  const opts = r.event_options || [];

  let dsHtml = "";
  if (p.datasets && p.datasets.length) {
    const list = p.datasets.map((d) => {
      const id = esc(d.id ?? "");
      const name = esc(d.name ?? "");
      const grade = esc(d.grade ?? "");
      const hub = !!d.hub;
      const reason = esc(d.reason ?? "");
      const cols = Array.isArray(d.columns) ? d.columns.slice(0, 6).join(", ") : "";
      const extra = p.mode === "unsupported" ? `<div class="dsc-m">기간 ${esc(d.period ?? "")} · ${esc(d.update ?? "")} 갱신 · 주요 항목 ${esc(cols)}</div>` : "";
      return `<div class="dsc"><div class="dsc-top"><span class="dsc-id">${id}</span><span class="dsc-g ${hub ? "hub" : ""}">${grade}</span></div><div class="dsc-n">${name}</div><div class="dsc-r">${reason}</div>${extra}</div>`;
    }).join("");
    dsHtml = `<div class="ds"><div class="ds-h">오름이 고른 데이터 <span class="muted">${p.datasets.length}종 · 명세서에서 확인</span></div><div class="ds-list">${list}</div></div>`;
  }

  const tr = STATE.trace || [];
  const n = tr.filter((x) => x.kind === "tool").length;
  const thinkFinalSec = tr
    .filter((x) => x.kind === "think" || x.kind === "final")
    .reduce((sum, x) => sum + (Number(x.sec) || 0), 0);
  const 총초 = Math.round(thinkFinalSec);
  const traceDetails = `<details class="trace-box"><summary>오름이 한 일 · 도구 ${n}회${총초 ? " · " + 총초 + "초" : ""}</summary>${traceHtml(tr, false)}</details>`;

  $("#p1").classList.add("has-plan");
  $("#planBox").innerHTML = `
  <div class="plan">
    <div class="plan-head"><span class="spark" aria-hidden="true"></span><span class="mode-badge">${esc(p.mode_ko)}</span><span class="src-badge">${p.mode === "unsupported" ? "자동 분석 범위 밖 · 필요한 데이터를 찾아 안내합니다" : "질문을 이렇게 이해했습니다 · 값을 고치면 바로 반영돼요"}</span></div>
    <div class="plan-exp">${esc(p.explanation || "")}</div>
    ${dsHtml}
    ${fields ? `<div class="plan-grid">${fields}</div>` : ""}
    <datalist id="dl_regions">${(v.regions || []).map((x) => `<option value="${esc(x)}">`).join("")}</datalist>
    ${clar.map((c) => `<div class="clarify">확인 필요: ${esc(c)}</div>`).join("")}
    ${opts.length ? `<div class="opts small"><span class="muted">축제 목록의 다른 회차로 바로 채우기</span>${opts.map((o, i) => `<button class="ghost" data-opt="${i}">${esc(o.start)}~${esc(o.end)}</button>`).join("")}</div>` : ""}
    <div class="notes">${warns.map((w) => `<div>참고: ${esc(w)}</div>`).join("")}
    ${r.summary ? `<div>해석 결과: ${esc(r.summary)}</div>` : ""}</div>
    <div class="plan-actions">
      ${p.mode === "unsupported"
        ? '<div class="small muted">센터 조회 화면에서 위 데이터를 1개월 단위로 조회해 직접 분석할 수 있습니다. 자동 분석은 상권·축제·버스 질문에서 동작합니다.</div>'
        : '<button class="primary" id="btnToData">이 계획으로 데이터 준비 →</button>'}
    </div>
    ${traceDetails}
  </div>`;
  $$("#planBox .plan-grid input:not([disabled]), #planBox .plan-grid select").forEach((el) => el.onchange = () => applyParams());
  $$("[data-opt]").forEach((b) => b.onclick = () => {
    const o = opts[+b.dataset.opt]; $("#f_start").value = o.start; $("#f_end").value = o.end; applyParams();
  });
  const btnToData = $("#btnToData");
  if (btnToData) {
    btnToData.onclick = async () => {
      await applyParams(true);
      const left = STATE.resolved?.issues || [];
      if (left.length) { renderPlan(); toast("먼저 '확인 필요' 항목을 채워 주세요."); return; }
      go(2); renderData();
    };
  }
}

async function applyParams(silent) {
  const ch = {};
  const g = (id) => $(id)?.value;
  if ($("#f_region")) ch.region = g("#f_region");
  if ($("#f_industry")) ch.industry = g("#f_industry");
  if ($("#f_event")) ch.event_name = g("#f_event");
  if ($("#f_start")) ch.event_start = g("#f_start");
  if ($("#f_end")) ch.event_end = g("#f_end");
  if ($("#f_control")) ch.control_region = g("#f_control");
  if ($("#f_radius")) ch.radius_km = g("#f_radius");
  try {
    STATE = await post(`/api/${SID}/params`, { changes: ch });
    if (silent !== true) { renderPlan(); toast("수정 내용을 반영했습니다."); }
  } catch (e) { toast(e.message); }
}

/* ---------------- 2. 데이터 ---------------- */
function renderData() {
  const rank = (need) => (need === "auto" ? 0 : need === "suggested" ? 2 : 1);
  const items = [...(STATE.checklist || [])].sort((a, b) => rank(a.need) - rank(b.need));
  $("#checklist").innerHTML = items.map((it) => {
    let tag = "";
    if (it.need === "suggested") {
      tag = '<span class="tag ai">AI 추천</span>';
    } else {
      tag = it.need === "required" ? '<span class="tag req">필수</span>' : it.need === "auto" ? '<span class="tag auto">자동 포함</span>' : '<span class="tag rec">권장</span>';
      if (it.ai_pick) {
        tag += '<span class="tag ai">AI 선택</span>';
      }
    }
    const st = it.status === "ok" ? "✓" : it.status === "auto" ? "●" : "";
    return `<div class="ck ${it.status}">
      <div class="st">${st}</div>
      <div><div class="nm">${esc(it.name)} ${tag}</div>
        ${it.how ? `<div class="how">${it.how}</div>` : ""}
        ${it.why ? `<div class="why">${esc(it.why)}</div>` : ""}
        ${it.ai_pick ? `<div class="aiwhy">오름이 고른 이유: ${esc(it.ai_pick)}</div>` : ""}
        ${it.have ? `<div class="have">들어옴: ${esc(it.have)}</div>` : ""}</div>
      <div class="small muted">${esc(it.id || "")}</div></div>`;
  }).join("") || '<div class="muted">필요한 데이터가 없습니다.</div>';
  const auto = items.filter((it) => it.need === "auto");
  $("#demoBox").innerHTML = auto.length ? `
    <b>센터 CSV가 없어도 바로 분석됩니다.</b> 오름에 들어 있는 <b>제주데이터허브 공개 실데이터</b>
    ${auto.length}종으로 먼저 분석하고, 센터 CSV를 넣으면 최신·세부 분석으로 바뀝니다.
    <div class="small muted" style="margin-top:4px">센터 조회 화면: 데이터 선택 → 기간(1개월) → 조건 → 조회 → CSV 다운로드</div>` :
    `<span class="small">센터 조회 화면: 데이터 선택 → 기간(1개월) → 조건 → 조회 → CSV 다운로드</span>`;
  renderIngested();
}

function renderIngested() {
  const ing = STATE.ingested || [];
  const un = STATE.unrecognized || [];
  $("#ingested").innerHTML = (ing.length || un.length) ? `<div class="files">
    ${ing.map((d) => `<div class="file"><div class="h"><span>${esc(d.name)} ${d.synthetic ? '<span class="syn">가상</span>' : ""}</span><span class="muted">${esc(d.id)}</span></div>
      <div>${esc(d.coverage)} · 파일 ${d.files}개 · ${Number(d.rows).toLocaleString()}행(원본 행은 화면에 표시하지 않음)</div>
      ${d.missing?.length ? `<div class="w">기간 사이 빈 달 ${d.missing.length}개: ${esc(d.missing.slice(0, 6).join(", "))}${d.missing.length > 6 ? " …" : ""} <span class="muted">(체크리스트 기간에 해당하면 추가로 받아 주세요)</span></div>` : ""}
      ${(d.warnings || []).filter((w) => !/누락 기간|빠진 기간/.test(w)).map((w) => `<div class="w">${esc(w)}</div>`).join("")}</div>`).join("")}
    ${un.map((u) => `<div class="file"><div class="h"><span class="w">${u.startsWith("중복 파일") ? "알림" : "인식하지 못한 파일"}</span></div>${esc(u)}</div>`).join("")}
  </div>` : "";
}

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  toast(`${files.length}개 파일을 읽는 중…`, 6000);
  try {
    STATE = await api(`/api/${SID}/upload`, { method: "POST", body: fd });
    renderData(); toast("파일을 인식했습니다.");
  } catch (e) { toast(e.message); }
}


/* ---------------- 3. 실행 ---------------- */
const RUN_STEPS = ["질문 해석", "데이터 불러오기", "로컬 분석", "반출 기준 검사", "차트 생성", "해석·보고서 문장 작성", "보고서 생성", "완료"];
async function doRun() {
  const miss = (STATE.checklist || []).filter((c) => c.need === "required" && c.status === "missing");
  if (miss.length && !confirm(`필수 데이터 ${miss.length}종이 없습니다(${miss.map((m) => m.name).join(", ")}).\n있는 데이터만으로 분석할까요?`)) return;
  try { await post(`/api/${SID}/run`); } catch (e) { toast(e.message, 6000); return; }
  go(3);
  $$(".err, #runSteps ~ .actions").forEach((x) => x.remove());
  clearInterval(poll);
  poll = setInterval(async () => {
    try {
      const st = await api(`/api/${SID}/state`);
      STATE = st; renderRun(); renderTimeline();
      if (!st.running) { clearInterval(poll); if (st.result) { curTab = "find"; go(4); renderResult(); } else if (st.error) renderRunError(); }
    } catch (e) { clearInterval(poll); toast(e.message); }
  }, 900);
}

function renderRun() {
  const pr = Object.fromEntries((STATE.progress || []).map((p) => [p.step, p]));
  $("#runSteps").innerHTML = RUN_STEPS.map((s) => {
    const p = pr[s]; const cls = p ? p.status : "";
    const ic = cls === "done" ? "✓" : cls === "running" ? "…" : cls === "error" ? "!" : "";
    return `<li class="${cls}"><span class="ic">${ic}</span><span>${esc(s)}</span><span class="msg">${esc(p?.msg || "")}</span></li>`;
  }).join("");
}
function renderRunError() {
  $("#runSteps").insertAdjacentHTML("afterend", `<div class="err">분석 중 오류가 발생했습니다: ${esc(STATE.error)}\n데이터 구성을 확인한 뒤 다시 실행해 주세요.</div><div class="actions"><button class="ghost" onclick="go(2)">← 데이터로 돌아가기</button></div>`);
}

function renderTimeline() {
  const pr = STATE?.progress || [];
  $("#timeline").innerHTML = pr.length ? pr.map((p) => `<li><span class="tm">${esc(p.t)}</span><span>${esc(p.step)}${p.msg ? " · " + esc(p.msg) : ""}</span></li>`).join("") : '<li class="muted">아직 기록이 없습니다.</li>';
}

/* ---------------- 4. 결과 ---------------- */
function chartUrl(u) { return `/api/${SID}/${u}`; }
function fileUrl(n) { return `/api/${SID}/file/${encodeURIComponent(n)}`; }

function showTab(t) {
  curTab = t;
  const R = STATE?.result;
  const hasMap = (R?.map_points || []).some((p) => isFinite(p.lat) && isFinite(p.lon));
  $$("#tabs .tab").forEach((x) => x.classList.toggle("on", x.dataset.t === t));
  ["find", "rec", "data", "guard", "flow"].forEach((k) => $(`#t_${k}`)?.classList.toggle("hidden", k !== t));
  if (t === "data" && hasMap) drawMap(R?.map_points || []);
  if (t === "flow") renderFlow();
}

function renderResult() {
  const R = STATE.result; const N = R.narrative;
  const g = R.guard || [];
  const cnt = (s) => g.filter((x) => x.status === s).length;
  const f = R.files || {};
  const hasMap = (R.map_points || []).some((p) => isFinite(p.lat) && isFinite(p.lon));
  $("#result").innerHTML = `
  <div class="res-head">
    <div><div class="meta">${esc(R.params?.region || "")} · ${esc(R.params?.period || (R.params?.start ? R.params.start + "~" + R.params.end : ""))}</div><h2>${esc(R.title)}</h2></div>
    <div class="dlwrap">
      <button class="dl main" id="btnDl" aria-expanded="false">내려받기 <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>
      <div class="dlmenu hidden" id="dlMenu">
        ${f.pdf ? `<a href="${fileUrl(f.pdf)}">PDF 보고서<span>제출·인쇄용</span></a>` : `<a href="${fileUrl(f.html)}" target="_blank">보고서(HTML)<span>열어서 인쇄 → PDF 저장</span></a>`}
        <a href="${fileUrl(f.docx)}">DOCX<span>한글·워드에서 편집</span></a>
        <a href="${fileUrl(f.zip)}">반출 패키지(ZIP)<span>PDF·DOCX·PNG 차트 묶음</span></a>
      </div>
    </div>
  </div>
  ${R.synthetic ? '<div class="syn-banner">⚠ 시연용 가상(SYNTHETIC) 센터 데이터로 만든 결과입니다. 실제 수치가 아닙니다.</div>' : ""}
  <div class="reply"><span class="spark" aria-hidden="true"></span><div>
    <div class="headline">${esc(N.headline)}</div>
    <p class="summary">${esc(N.summary)}</p></div></div>
  <div class="kpis">${(R.kpis || []).map((k) => `<div class="kpi"><div class="l">${esc(k.label)}</div><div class="v">${esc(k.value)}</div></div>`).join("")}</div>
  <div class="tabs" id="tabs">
    <button class="tab on" data-t="find">분석 결과</button>
    <button class="tab" data-t="rec">제언·활용 문안</button>
    <button class="tab" data-t="data">근거 자료</button>
    <button class="tab" data-t="guard">반출 검사 (${cnt("pass") + cnt("fixed")}/${g.length})</button>
    <button class="tab" data-t="flow">작업 과정</button>
  </div>
  <div id="t_find">
    ${N.findings.map((x, i) => `<div class="finding"><div class="t"><span class="n">${i + 1}</span><span>${esc(x.title)}</span></div><div class="d">${esc(x.detail)}</div>
      ${x.evidence?.length ? `<div class="ev">근거 표: ${x.evidence.map(esc).join(", ")}</div>` : ""}
      ${x.figures?.length ? `<div class="figs">${x.figures.map((fg) => `<figure><img src="${chartUrl(fg.url)}" alt="${esc(fg.caption)}"><figcaption>${esc(fg.caption)}</figcaption></figure>`).join("")}</div>` : ""}</div>`).join("")}
    ${(R.extra_figures || []).length ? `<h3>참고 그림</h3><div class="figs">${R.extra_figures.map((fg) => `<figure><img src="${chartUrl(fg.url)}"><figcaption>${esc(fg.caption)}</figcaption></figure>`).join("")}</div>` : ""}
    ${(N.limitations || []).length || (R.notes || []).length ? `<h3>한계 및 유의사항</h3><ul class="note-list">${[...(N.limitations || []), ...(R.notes || [])].map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
  </div>
  <div id="t_rec" class="hidden">
    <div class="recs">${N.recommendations.map((r) => `<div class="rec"><div class="t">${esc(r.title)}</div>${esc(r.detail)}</div>`).join("")}</div>
    <h3>${esc(R.business_title)} <button class="ghost copy" data-copy="bp">복사</button></h3>
    <div class="bp" id="bp">${esc(N.business_text)}</div>
  </div>
  <div id="t_data" class="hidden">
    ${hasMap ? `<div id="map"></div><div class="small muted" style="margin:6px 0 4px">정류소(파랑, 크기=이용량)·동종 점포(주황)·행사장(빨강). 좌표는 반출 가능·공개 데이터만 사용합니다.</div>` : ""}
    ${R.tables.map((t) => `
    <div class="tt">${esc(t.title)} <span class="key">${esc(t.key)}</span> <span class="badge ${t.status}">${esc(t.status_ko)}</span></div>
    <div class="tbl-wrap"><table class="t"><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>
    ${t.rows.map((r) => `<tr>${r.map((c) => `<td class="${String(c).startsWith("비공개") ? "hid" : ""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</table></div>
    ${t.note ? `<div class="small muted">${esc(t.note)}</div>` : ""}`).join("")}</div>
  <div id="t_guard" class="hidden">
    <div class="guard-sum"><div class="gs pass"><div class="n">${cnt("pass")}</div>그대로 통과</div><div class="gs fixed"><div class="n">${cnt("fixed")}</div>처리 후 통과(범주·삭제처리)</div><div class="gs blocked"><div class="n">${cnt("blocked")}</div>차단(보고서·AI 제외)</div></div>
    <div class="tbl-wrap"><table class="t"><tr><th>자료</th><th>판정</th><th>가공 방식</th><th>처리 내역</th></tr>
      ${g.map((x) => `<tr><td>${esc(x.title)}</td><td><span class="badge ${x.status}">${esc(x.status_ko)}</span></td><td>${esc(x.method)}</td><td style="white-space:normal">${esc(x.actions)}</td></tr>`).join("")}</table></div>
    <h3>반출 신청서 초안 <button class="ghost copy" data-copy="exp">복사</button></h3>
    <div class="tbl-wrap"><table class="t" id="exp"><tr><th>자료</th><th>출처(원본데이터 명칭)</th><th>가공 방식</th><th>활용 사유</th></tr>
      ${(R.export_rows || []).map((x) => `<tr><td>${esc(x["자료"])}</td><td style="white-space:normal">${esc(x["출처(원본데이터 명칭)"])}</td><td>${esc(x["가공 방식"])}</td><td style="white-space:normal">${esc(x["활용 사유"])}</td></tr>`).join("")}</table></div>
    <div class="small muted">근거: 제주빅데이터센터 데이터 반출 정책(2026.09.01 개정). 반출은 센터 시스템에서 신청하며, 승인 후 마이페이지에서 내려받습니다. AI에 전달된 표: ${esc((R.llm_sent_tables || []).join(", ") || "없음")}</div>
  </div>
  <div id="t_flow" class="hidden"></div>
  <div id="answers"></div>
  <div class="ask-dock">
    <div class="ask2"><input id="q2" placeholder="이어서 물어보세요 — 예) 한림읍이랑 비교해 줘 / 상권 매력도 지수는 어떻게 계산해?"><button class="primary" id="btnAsk">질문</button></div>
  </div>`;
  $$("#tabs .tab").forEach((b) => b.onclick = () => showTab(b.dataset.t));
  $$(".copy").forEach((b) => b.onclick = () => { navigator.clipboard.writeText($("#" + b.dataset.copy).innerText); toast("복사했습니다."); });
  $("#btnAsk").onclick = ask;
  $("#btnDl").onclick = (e) => { e.stopPropagation(); const m = $("#dlMenu"); m.classList.toggle("hidden"); $("#btnDl").setAttribute("aria-expanded", String(!m.classList.contains("hidden"))); };
  $("#q2").onkeydown = (e) => { if (e.key === "Enter") ask(); };
  showTab(curTab);
}

function renderFlow() {
  const el = $("#t_flow");
  if (!el || !STATE?.result) return;
  const R = STATE.result;
  const usage = { ...(STATE.usage || R.usage || {}), mode: R.usage?.mode };
  const guard = R.guard || [];
  const trace = STATE.trace || [];
  const progress = STATE.progress || [];

  const isLlm = usage.mode === "llm" || usage.calls > 0;
  const aiVal = isLlm ? `${usage.calls || 0}회` : "규칙";
  const aiDesc = isLlm ? `${esc(usage.model || "")} · 추론 high` : "AI 미연결(규칙 기반)";

  const toolCnt = trace.filter((x) => x.kind === "tool").length;
  const guardPass = guard.filter((x) => x.status !== "blocked").length;

  const costVal = usage.calls > 0 ? `약 ${(usage.cost_krw || 0).toLocaleString()}원` : "0원";
  const costDesc = usage.calls > 0
    ? `입력 ${(usage.input_tokens || 0).toLocaleString()} · 출력 ${(usage.output_tokens || 0).toLocaleString()} 토큰`
    : "외부 전송 없음";

  const planTrace = trace.filter((x) => !String(x.title || "").startsWith("후속 질문"));
  const followTrace = trace.filter((x) => String(x.title || "").startsWith("후속 질문"));

  const progressItems = progress.map((p) => traceItemHtml({
    kind: p.status === "error" ? "note" : (p.step === "완료" ? "final" : "tool"),
    title: p.step,
    detail: p.msg,
    tool: "",
    sec: null
  })).join("");

  const sentTables = (R.llm_sent_tables || []).join(", ") || "없음";

  el.innerHTML = `
    <div class="flow-sum">
      <div class="fs"><div class="n">${aiVal}</div>${aiDesc}</div>
      <div class="fs"><div class="n">${toolCnt}회</div>지역·업종·데이터 확인</div>
      <div class="fs"><div class="n">${guardPass}/${guard.length}</div>표 통과(처리 포함)</div>
      <div class="fs"><div class="n">${costVal}</div>${costDesc}</div>
    </div>
    <h3>① 계획 — 오름이 도구로 확인한 것</h3>
    ${traceHtml(planTrace, false)}
    <h3>② 실행 — 이 PC에서 계산</h3>
    <ol class="trace static">${progressItems}</ol>
    <h3>③ AI에 보낸 것</h3>
    <p class="small">원본 행은 보내지 않았습니다. 반출 검사를 통과한 집계표만 보냈습니다: ${esc(sentTables)}</p>
    <button class="ghost" id="btnFlowAudit">AI 전송 기록 전체 보기</button>
    ${followTrace.length ? `<h3>④ 후속 질문에서 쓴 도구</h3>${traceHtml(followTrace, false)}` : ""}
  `;
  const b = $("#btnFlowAudit", el);
  if (b) b.onclick = () => $("#btnAudit")?.click();
}

function drawMap(points) {
  if (!window.L) { $("#map").innerHTML = '<div class="muted" style="padding:20px">지도 라이브러리를 불러오지 못했습니다. 보고서의 정적 지도를 확인하세요.</div>'; return; }
  if (mapObj) { mapObj.remove(); mapObj = null; }
  mapObj = L.map("map");
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(mapObj);
  const pts = points.filter((p) => isFinite(p.lat) && isFinite(p.lon));
  if (!pts.length) { mapObj.setView([33.38, 126.55], 10); return; }
  const maxv = Math.max(1, ...pts.filter((p) => p.type === "stop").map((p) => p.value || 0));
  pts.forEach((p) => {
    const color = p.type === "stop" ? "#1E6FB8" : p.type === "venue" ? "#C0392B" : "#F28C28";
    const r = p.type === "stop" ? 4 + 14 * Math.sqrt((p.value || 0) / maxv) : p.type === "venue" ? 11 : 4;
    L.circleMarker([p.lat, p.lon], { radius: r, color: "#fff", weight: 1, fillColor: color, fillOpacity: .8 })
      .bindTooltip(esc(p.name) + (p.value != null ? ` · 일평균 승차 ${Number(p.value).toFixed(1)}명` : "")).addTo(mapObj);
  });
  mapObj.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lon])).pad(0.15));
}

async function ask() {
  const q = $("#q2").value.trim(); if (!q) return;
  $("#btnAsk").disabled = true;
  $("#answers").insertAdjacentHTML("beforeend", `<div class="answer" id="ans_pending"><b>Q. ${esc(q)}</b>\n답변을 작성하는 중…</div>`);
  $("#ans_pending").scrollIntoView({ behavior: "smooth", block: "center" });
  try {
    const j = await post(`/api/${SID}/ask`, { question: q });
    const tl = (j.tools || []).map((t) => `${t.tool}(${Object.values(t.input || {}).filter((v) => v && (!Array.isArray(v) || v.length)).map((v) => Array.isArray(v) ? v.join("·") : v).join(", ")})`);
    const how = "계산은 이 PC에서 수행, 반출 검사를 통과한 결과만 사용";
    $("#ans_pending").outerHTML = `<div class="answer"><b>Q. ${esc(q)}</b>\n${mdLite(j.answer)}${tl.length ? `\n<span class="small muted">에이전트가 사용한 도구: ${esc(tl.join(" → "))} · ${how}</span>` : ""}${j.unverified?.length ? `\n\n※ 근거 표에서 확인되지 않은 수치: ${esc(j.unverified.join(", "))}` : ""}</div>`;
    $("#q2").value = "";
    try {
      STATE = await api(`/api/${SID}/state`);
      if (curTab === "flow") renderFlow();
    } catch { }
  } catch (e) { $("#ans_pending").outerHTML = `<div class="err">${esc(e.message)}</div>`; }
  finally { $("#btnAsk").disabled = false; }
}

/* 도구 결과(마크다운 표·제목)를 안전하게 HTML로 */
function mdLite(text) {
  const lines = String(text || "").split("\n");
  let html = "", tbl = [];
  const flush = () => {
    if (!tbl.length) return;
    const rows = tbl.filter((r) => !/^\|\s*-+/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    html += `<div class="tbl-wrap" style="max-height:none;margin:6px 0"><table class="t"><tr>${rows[0].map((c) => `<th>${esc(c)}</th>`).join("")}</tr>` +
      rows.slice(1).map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("") + "</table></div>";
    tbl = [];
  };
  for (const ln of lines) {
    if (ln.trim().startsWith("|")) { tbl.push(ln.trim()); continue; }
    flush();
    if (/^###\s/.test(ln)) html += `<b>${esc(ln.replace(/^###\s*(\[[^\]]*\]\s*)?/, ""))}</b>\n`;
    else if (/^- 가공 방식/.test(ln)) html += `<span class="small muted">${esc(ln.slice(2))}</span>\n`;
    else html += esc(ln) + "\n";
  }
  flush();
  return html.replace(/\n{3,}/g, "\n\n");
}

/* ---------------- 모달 ---------------- */
function modal(html) { $("#modalBody").innerHTML = html; $("#modal").classList.remove("hidden"); }
$("#modalX").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };

$("#btnAudit").onclick = async () => {
  try {
    const j = await api(`/api/${SID}/audit`);
    const u = j.usage;
    modal(`<h2>AI 전송 기록</h2><p class="lead">이 세션에서 외부 AI에 보낸 내용 전체입니다. 원본 데이터 행은 보내지 않으며, 반출 검사를 통과한 집계표와 공개 명세 정보만 포함됩니다.</p>
      <div class="small">호출 ${u.calls}회 · 입력 ${u.input_tokens.toLocaleString()} / 출력 ${u.output_tokens.toLocaleString()} 토큰 · 추정 비용 약 ${u.cost_krw.toLocaleString()}원 ($${u.cost_usd})</div>
      ${j.records.length ? j.records.map((r) => `<div class="audit-item"><b>${esc(r.ts)} · ${esc(r.purpose)}</b> · ${esc(r.model)} · ${r.input_tokens ?? "-"}/${r.output_tokens ?? "-"} 토큰 ${r.error ? `<span class="w">오류: ${esc(r.error)}</span>` : ""}
        <details><summary>보낸 내용 보기</summary><pre>${esc(r.sent_user)}</pre></details>
        <details><summary>받은 내용 보기</summary><pre>${esc(r.received)}</pre></details></div>`).join("") : '<p class="muted">아직 AI 호출이 없습니다.</p>'}`);
  } catch (e) { toast(e.message); }
};

$("#btnRules").onclick = () => modal(`<h2>데이터 보호 원칙</h2>
  <ul>
    <li>원본 데이터는 이 PC 밖으로 나가지 않습니다.</li>
    <li>AI에는 반출 기준을 통과한 집계값만 전달합니다. 보낸 내용은 〈AI 전송 기록〉에서 그대로 볼 수 있습니다.</li>
    <li>10 미만 수치는 자동으로 묶거나 비공개 처리하고, 합계로 거꾸로 계산되는 값도 함께 가립니다.</li>
    <li>자리를 떠나기 전 〈퇴실 정리〉로 업로드 파일·결과·AI 기록을 모두 삭제합니다.</li>
  </ul>`);

$("#btnHelp").onclick = () => modal(`<h2>오름 사용 안내</h2>
  <ol>
    <li><b>질문하기</b> — "애월읍 카페 창업 상권", "방어축제 효과", "모슬포 버스 정류소 진단"처럼 평소 말로 질문합니다.</li>
    <li><b>데이터 준비</b> — CSV가 없어도 제주데이터허브 실데이터로 바로 분석됩니다. 더 최신·세밀하게 보려면 화면에 나온 센터 데이터를 한 달 단위 CSV로 내려받아 끌어다 놓으세요(종류·기간 자동 인식).</li>
    <li><b>분석 실행</b> — 모든 계산은 이 PC에서 합니다. 결과표마다 반출 기준(10 미만 수치 등)을 자동 검사·처리합니다.</li>
    <li><b>보고서·반출</b> — PDF·DOCX·PNG를 받고, '반출 신청서 초안'을 복사해 센터 시스템의 반출 신청에 붙여 넣습니다.</li>
    <li><b>퇴실 정리</b> — 자리를 떠나기 전 '퇴실 정리'를 누르면 업로드 파일·결과·AI 기록이 모두 삭제됩니다.</li>
  </ol>
  <p class="small muted">문의: 분석실 안내데스크</p>
  <p class="small muted">배경: 저작권 없는 제주 해안·돌하르방 실제 사진(Pixabay, Pixabay Content License)의 파도를 영상화. 출처 web/vendor/bg/CREDITS.txt</p>`);

$("#btnCleanup").onclick = async () => {
  if (!confirm("업로드한 CSV, 분석 결과, 보고서, AI 전송 기록을 모두 삭제합니다.\n반출 신청을 마친 뒤 진행하세요. 계속할까요?")) return;
  try {
    const j = await post(`/api/${SID}/cleanup`);
    sessionStorage.removeItem("oreum_sid"); SID = null; STATE = null;
    await ensureSession(); renderChips();
    $("#planBox").innerHTML = ""; $("#question").value = ""; $("#p1").classList.remove("has-plan"); autosize();
    $("#timeline").innerHTML = '<li class="muted">아직 기록이 없습니다.</li>';
    go(1); toast(`정리 완료: 파일 ${j.deleted_files}개 삭제`);
  } catch (e) { toast(e.message); }
};

/* ---------------- 배경 영상: 움직임 줄이기 설정이면 재생하지 않음 ---------------- */
(() => {
  const v = $("#bgVideo"); if (!v) return;
  const rm = matchMedia("(prefers-reduced-motion: reduce)");
  const sync = () => { if (rm.matches || document.hidden) v.pause(); else v.play().catch(() => { }); };
  rm.addEventListener("change", sync); document.addEventListener("visibilitychange", sync); sync();
})();

/* ---------------- 메뉴(사이드바) 토글 ---------------- */
const NARROW = () => innerWidth <= 1100;
function setRail(open, save = true) {
  document.body.classList.toggle("rail-closed", !open);
  $("#btnMenu").setAttribute("aria-expanded", String(open));
  if (save && !NARROW()) { try { localStorage.setItem("oreum_rail", open ? "1" : "0"); } catch { } }
}
(() => { let pref = null; try { pref = localStorage.getItem("oreum_rail"); } catch { } setRail(NARROW() ? false : pref !== "0", false); })();
$("#btnMenu").onclick = () => setRail(document.body.classList.contains("rail-closed"));
$("#rail").addEventListener("click", (e) => { if (NARROW() && e.target.closest("button, li")) setRail(false, false); });
document.addEventListener("click", (e) => { const m = $("#dlMenu"); if (m && !m.classList.contains("hidden") && !e.target.closest(".dlwrap")) m.classList.add("hidden"); });

/* ---------------- 이벤트 ---------------- */
$("#btnPlan").onclick = doPlan;
$("#question").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); doPlan(); } };
function autosize() { const t = $("#question"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; }
$("#question").addEventListener("input", autosize);
$("#btnNew").onclick = () => {
  go(1); $("#p1").classList.remove("has-plan"); $("#planBox").innerHTML = "";
  $("#question").value = ""; autosize(); $("#question").focus();
};
$("#btnRun").onclick = doRun;
$$("[data-go]").forEach((b) => b.onclick = () => go(+b.dataset.go));
$$("#stepper li").forEach((li) => li.onclick = () => {
  const n = +li.dataset.step;
  if (n === 1) go(1);
  else if (n === 2 && STATE?.plan) { go(2); renderData(); }
  else if (n === 4 && STATE?.result) { go(4); renderResult(); }
});
const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => upload(e.dataTransfer.files));
$("#fileInput").onchange = (e) => upload(e.target.files);

(async function init() {
  try {
    STATUS = await api("/api/status");
    await ensureSession();
    renderChips(); renderExamples();
    const st = await api(`/api/${SID}/state`);
    STATE = st;
    if (st.planning) {
      $("#question").value = st.question || "";
      autosize();
      startPlanPoll();
    } else if (st.plan) {
      $("#question").value = st.question || "";
      autosize();
      renderPlan();
      renderTimeline();
    }
    if (st.result) { go(4); renderResult(); } else go(1);
  } catch (e) { toast("서버에 연결할 수 없습니다: " + e.message, 8000); }
})();
