/* App shell: navigation, session flows (live / upload / view), configuration editor, recent sessions. */
window.RA = window.RA || {};
const $ = RA.$, el = RA.el;

RA.liveSocket = null;
RA.timer = null;
RA.recStart = null;
RA.current = null;
RA.configs = [];
RA.languages = [];

// ---------------- helpers ----------------
RA.closeModal = () => $("modal").classList.add("hidden");
RA.showError = (msg, id) => { const e = $(id || "start-error"); e.textContent = msg; e.classList.remove("hidden"); };
const hide = (id) => $(id).classList.add("hidden");

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* not json */ }
  if (!res.ok) {
    let msg = (body && body.detail) || `Request failed (${res.status})`;
    if (Array.isArray(msg)) msg = msg.map((m) => String(m.msg || m).replace(/^Value error, /, "")).join("; ");
    throw new Error(msg);
  }
  return body;
}

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("view-" + name).classList.remove("hidden");
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name || (name === "session" && b.dataset.view === "start")));
  if (name === "configs") loadConfigs().then(renderConfigList);
  if (name === "sessions") loadSessions();
  if (name === "start" || name === "upload") loadConfigs();
}

// ---------------- readiness banner ----------------
async function checkHealth() {
  try {
    const h = await api("/api/health");
    const b = $("banner");
    if (!h.configured) {
      b.textContent = "No Gemini API key found. Add GOOGLE_API_KEY (or GEMINI_API_KEY) to the .env file in the project folder and restart the server.";
      b.className = "banner";
    } else b.classList.add("hidden");
    RA.health = h;
  } catch { /* server unreachable */ }
}

// ---------------- configs in selects ----------------
async function loadConfigs() {
  const d = await api("/api/configurations");
  RA.configs = d.configurations; RA.languages = d.languages;
  for (const id of ["s-config", "u-config"]) {
    const sel = $(id), cur = sel.value;
    sel.replaceChildren(...RA.configs.map((c) => { const o = el("option", null, c.name); o.value = c.id; return o; }));
    if (cur) sel.value = cur;
  }
  for (const id of ["s-lang", "u-lang"]) {
    const sel = $(id), cur = sel.value;
    sel.replaceChildren(...RA.languages.map((l) => { const o = el("option", null, l); o.value = l; return o; }));
    if (cur) sel.value = cur;
  }
  syncLang("s-config", "s-lang"); syncLang("u-config", "u-lang");
}
function syncLang(cfgSel, langSel) {
  const c = RA.configs.find((x) => x.id === $(cfgSel).value);
  if (c) $(langSel).value = c.language;
}

// ---------------- session view plumbing ----------------
function tick() {
  if (!RA.recStart) return;
  $("d-timer").textContent = RA.fmt((Date.now() - RA.recStart) / 1000);
}
function stopTimer() { clearInterval(RA.timer); RA.timer = null; RA.recStart = null; }

function handleEvent(ev) {
  const D = RA.Dashboard;
  switch (ev.type) {
    case "snapshot":
      D.segments = ev.segments; D.interim = ev.interim; D.renderTranscript();
      D.render(ev.dashboard); D.renderSteps(ev.steps); afterStatus(ev.dashboard.session); break;
    case "dashboard": D.render(ev.dashboard); afterStatus(ev.dashboard.session); break;
    case "segment": D.addSegment(ev.segment); break;
    case "segments_all": D.segments = ev.segments; D.renderTranscript(); break;
    case "interim": D.setInterim(ev.segment); break;
    case "steps": D.renderSteps(ev.steps); break;
    case "status": if (D.info) { D.info.status = ev.status; D.renderHeader(D.info); afterStatus(D.info); } break;
    case "warning": D.alert(ev.message); break;
    case "error": D.alert(ev.message, true); break;
    case "service_error": D.alert(ev.message, true); break;
    case "connection": break;
    case "analysis_started": $("d-analyzing").classList.remove("hidden"); break;
    case "analysis_done": $("d-analyzing").classList.add("hidden"); break;
    case "finalized": D.renderSteps([]); stopMic(); break;
  }
}

function afterStatus(info) {
  if (RA.recStart === null && info.status === "RECORDING" && RA.Mic.stream) { RA.recStart = Date.now(); RA.timer = setInterval(tick, 500); }
  if (["COMPLETED", "FAILED"].includes(info.status)) {
    stopMic(); stopTimer();
    $("d-timer").textContent = RA.fmt(info.duration_s);
    if (info.status === "FAILED") D_steps_clear();
  }
}
function D_steps_clear() { RA.Dashboard.renderSteps([]); }

function stopMic() { RA.Mic.stop(); }

function openSocket(sid, onOpen) {
  if (RA.liveSocket) RA.liveSocket.close();
  const sock = new RA.Socket(sid, {
    onEvent: handleEvent,
    onClose: () => {
      if (RA.liveSocket === sock) {
        RA.liveSocket = null;
        if (RA.Mic.stream) { stopMic(); RA.Dashboard.alert("Connection to the server was lost. The transcript captured so far is preserved - see Recent Sessions.", true); }
      }
    },
  });
  RA.liveSocket = sock;
  sock.opened.then(onOpen).catch((e) => RA.Dashboard.alert(e.message, true));
  return sock;
}

RA.openSession = async function (sid, silent) {
  const snap = await api(`/api/sessions/${sid}`);
  RA.current = snap.dashboard.session;
  RA.Dashboard.reset();
  showView("session");
  handleEvent(snap);
  $("d-timer").textContent = RA.fmt(snap.dashboard.session.duration_s);
  const live = ["PROCESSING", "FINALIZING"].includes(snap.dashboard.session.status);
  if (live && !silent) openSocket(sid, () => {});
};

// ---------------- live flow ----------------
async function startLive(e) {
  e.preventDefault();
  hide("start-error");
  if (!$("s-consent").checked) return RA.showError("Please confirm recording consent before starting.");
  if (!RA.health || !RA.health.configured) return RA.showError("No Gemini API key is configured. See the banner above.");
  $("btn-start").disabled = true;
  try {
    await RA.Mic.open(); // permission first, so we never create an empty session
    const info = await api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("s-name").value, configuration_id: $("s-config").value, language: $("s-lang").value, consent: true }) });
    RA.current = info;
    RA.Dashboard.reset();
    showView("session");
    RA.Dashboard.renderHeader(info, "00:00");
    $("btn-stop").disabled = false;
    openSocket(info.id, async () => {
      RA.liveSocket.sendJSON({ type: "start" });
      await RA.Mic.start((buf) => RA.liveSocket && RA.liveSocket.sendBinary(buf), () => {
        RA.Dashboard.alert("The microphone was disconnected. Recording stopped; finalizing what was captured.", true);
        stopSession();
      });
    });
  } catch (err) {
    stopMic();
    RA.showError(err.message);
  } finally { $("btn-start").disabled = false; }
}

function stopSession() {
  $("btn-stop").disabled = true;
  stopMic();
  if (RA.liveSocket) RA.liveSocket.sendJSON({ type: "stop" });
}

// ---------------- upload flow ----------------
function audioDuration(file) {
  return new Promise((resolve) => {
    const a = new Audio(); const url = URL.createObjectURL(file);
    const done = (v) => { URL.revokeObjectURL(url); resolve(v); };
    a.onloadedmetadata = () => done(isFinite(a.duration) ? a.duration : 0);
    a.onerror = () => done(0);
    setTimeout(() => done(0), 4000);
    a.src = url;
  });
}

async function startUpload(e) {
  e.preventDefault();
  hide("upload-error");
  const file = $("u-file").files[0];
  if (!file) return RA.showError("Choose an audio file first.", "upload-error");
  if (!$("u-consent").checked) return RA.showError("Please confirm recording consent before uploading.", "upload-error");
  $("btn-upload").disabled = true;
  try {
    const fd = new FormData();
    fd.append("file", file); fd.append("name", $("u-name").value || file.name);
    fd.append("configuration_id", $("u-config").value); fd.append("language", $("u-lang").value);
    fd.append("consent", "true"); fd.append("duration", String(await audioDuration(file)));
    const info = await api("/api/sessions/upload", { method: "POST", body: fd });
    RA.current = info;
    RA.Dashboard.reset();
    showView("session");
    RA.Dashboard.renderHeader(info, "00:00");
    RA.Dashboard.renderSteps([{ key: "upload", label: "File uploaded", state: "done" }, { key: "transcribe", label: "Transcribing audio with Gemini", state: "active" }]);
    openSocket(info.id, () => {});
  } catch (err) {
    RA.showError(err.message + " You can fix the problem and try again.", "upload-error");
  } finally { $("btn-upload").disabled = false; }
}

// ---------------- recent sessions ----------------
async function loadSessions() {
  const d = await api("/api/sessions");
  const tb = document.querySelector("#sessions-table tbody");
  tb.replaceChildren();
  $("sessions-empty").classList.toggle("hidden", d.sessions.length > 0);
  d.sessions.forEach((s) => {
    const tr = el("tr");
    const cell = (t) => { const td = el("td", null, t); tr.append(td); return td; };
    cell(s.name); cell(s.config_name); cell(new Date(s.created_at).toLocaleString());
    cell(RA.fmt(s.duration_s)); cell(s.overall_display);
    const st = el("td"); st.append(el("span", "chip " + ({ COMPLETED: "ok", FAILED: "bad" }[s.status] || "warn"), s.status)); tr.append(st);
    const act = el("td"); const box = el("div", "actions");
    const mk = (label, fn, cls) => { const b = el("button", "small " + (cls || ""), label); b.onclick = fn; box.append(b); };
    mk("View", () => RA.openSession(s.id));
    mk("Export", () => { location.href = `/api/sessions/${s.id}/export`; });
    mk("Delete", async () => {
      if (!confirm(`Delete "${s.name}" and its transcript, report and audio from this computer?\n\nData already processed by Google Gemini is not deleted by this action.`)) return;
      try { await api(`/api/sessions/${s.id}`, { method: "DELETE" }); loadSessions(); } catch (e) { alert(e.message); }
    }, "danger");
    act.append(box); tr.append(act); tb.append(tr);
  });
}

// ---------------- configuration manager ----------------
function renderConfigList() {
  const box = $("config-list");
  box.replaceChildren();
  RA.configs.forEach((c) => {
    const row = el("div", "cfg");
    const info = el("div");
    info.append(el("b", null, c.name), el("div", "muted small", `${c.topics.length} topics, ${c.topics.reduce((n, t) => n + t.criteria.length, 0)} criteria - ${c.language}`));
    const act = el("div", "row"); act.style.margin = 0;
    const mk = (label, fn, cls) => { const b = el("button", "small " + (cls || ""), label); b.onclick = fn; act.append(b); };
    mk("Edit", () => openEditor(JSON.parse(JSON.stringify(c))));
    mk("Duplicate", async () => { await api(`/api/configurations/${c.id}/duplicate`, { method: "POST" }); await loadConfigs(); renderConfigList(); });
    mk("Delete", async () => {
      if (!confirm(`Delete configuration "${c.name}"? Existing sessions keep their own snapshot.`)) return;
      await api(`/api/configurations/${c.id}`, { method: "DELETE" }); await loadConfigs(); renderConfigList();
    }, "danger");
    row.append(info, act); box.append(row);
  });
}

function openEditor(cfg) {
  const isNew = !cfg;
  cfg = cfg || { name: "", description: "", roles: ["Hotel Booking Agent", "Customer"], language: "English",
    topics: [{ name: "", criteria: [{ label: "", instruction: "" }] }] };
  const ed = $("config-editor");
  const draw = () => {
    ed.replaceChildren(el("h1", null, isNew ? "New Configuration" : "Edit Configuration"));
    const field = (label, value, set, tag) => {
      const l = el("label", null, label); const i = el(tag || "input"); i.value = value || ""; i.oninput = () => set(i.value); l.append(i); ed.append(l); return i;
    };
    field("Name", cfg.name, (v) => (cfg.name = v));
    field("Description", cfg.description, (v) => (cfg.description = v));
    field("Roles (comma separated)", cfg.roles.join(", "), (v) => (cfg.roles = v.split(",").map((x) => x.trim()).filter(Boolean)));
    const lang = el("label", null, "Language"); const sel = el("select");
    RA.languages.forEach((l) => { const o = el("option", null, l); o.value = l; sel.append(o); });
    sel.value = cfg.language; sel.onchange = () => (cfg.language = sel.value); lang.append(sel); ed.append(lang);

    cfg.topics.forEach((t, ti) => {
      const tb = el("div", "topic-ed");
      const nm = el("label", null, `Topic ${ti + 1} name`); const ni = el("input"); ni.value = t.name; ni.oninput = () => (t.name = ni.value); nm.append(ni); tb.append(nm);
      t.criteria.forEach((c, ci) => {
        const r = el("div", "crit-ed");
        const a = el("input"); a.placeholder = "Criterion label"; a.value = c.label; a.oninput = () => (c.label = a.value);
        const b = el("input"); b.placeholder = "Evaluation instruction"; b.value = c.instruction || ""; b.oninput = () => (c.instruction = b.value);
        const rm = el("button", "small danger", "Remove"); rm.type = "button"; rm.onclick = () => { t.criteria.splice(ci, 1); draw(); };
        r.append(a, b, rm); tb.append(r);
      });
      const row = el("div", "row");
      const addC = el("button", "small", "Add criterion"); addC.type = "button"; addC.onclick = () => { t.criteria.push({ label: "", instruction: "" }); draw(); };
      const delT = el("button", "small danger", "Delete topic"); delT.type = "button"; delT.onclick = () => { cfg.topics.splice(ti, 1); draw(); };
      row.append(addC, delT); tb.append(row); ed.append(tb);
    });
    const addT = el("button", "small", "Add topic"); addT.type = "button"; addT.onclick = () => { cfg.topics.push({ name: "", criteria: [{ label: "", instruction: "" }] }); draw(); };
    const err = el("div", "error hidden"); err.id = "cfg-error";
    const row = el("div", "row");
    const save = el("button", "primary", "Save"); save.type = "button";
    save.onclick = async () => {
      try {
        const saved = await api(isNew ? "/api/configurations" : `/api/configurations/${cfg.id}`, { method: isNew ? "POST" : "PUT",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
        await loadConfigs(); renderConfigList(); ed.classList.add("hidden"); void saved;
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    };
    const cancel = el("button", null, "Cancel"); cancel.type = "button"; cancel.onclick = () => ed.classList.add("hidden");
    row.append(save, cancel);
    ed.append(addT, err, row);
  };
  draw();
  ed.classList.remove("hidden");
  ed.scrollIntoView({ behavior: "smooth" });
}

// ---------------- init ----------------
document.addEventListener("DOMContentLoaded", () => {
  RA.Dashboard.init();
  document.querySelectorAll("#nav button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
  $("start-form").onsubmit = startLive;
  $("upload-form").onsubmit = startUpload;
  $("btn-goto-upload").onclick = () => showView("upload");
  $("btn-stop").onclick = stopSession;
  $("btn-export").onclick = () => RA.current && (location.href = `/api/sessions/${RA.current.id}/export`);
  $("btn-retry").onclick = async () => {
    try { await api(`/api/sessions/${RA.current.id}/retry`, { method: "POST" }); $("d-alerts").replaceChildren(); openSocket(RA.current.id, () => {}); }
    catch (e) { RA.Dashboard.alert(e.message, true); }
  };
  $("btn-diag").onclick = () => $("diag").classList.toggle("hidden");
  $("btn-new-config").onclick = () => openEditor(null);
  $("modal").onclick = (e) => { if (e.target.id === "modal") RA.closeModal(); };
  $("s-config").onchange = () => syncLang("s-config", "s-lang");
  $("u-config").onchange = () => syncLang("u-config", "u-lang");
  checkHealth();
  loadConfigs();
  const m = location.hash.match(/^#session=([\w-]+)$/); // deep link to a session
  if (m) RA.openSession(m[1]).catch(() => {});
});
