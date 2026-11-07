/* Dashboard rendering. All dynamic text goes through textContent (no innerHTML) to avoid XSS. */
window.RA = window.RA || {};

RA.$ = (id) => document.getElementById(id);
RA.el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
};
RA.fmt = (sec) => {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = (x) => String(x).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
};

RA.Dashboard = {
  segments: [], interim: null, data: null, expanded: new Set(), stick: true, info: null,

  init() {
    const t = RA.$("transcript");
    t.addEventListener("scroll", () => {
      this.stick = t.scrollHeight - t.scrollTop - t.clientHeight < 40; // don't fight a user reading earlier text
      RA.$("jump-bottom").classList.toggle("hidden", this.stick);
    });
    RA.$("jump-bottom").onclick = () => { this.stick = true; this.scrollEnd(true); };
  },

  reset() {
    this.segments = []; this.interim = null; this.data = null; this.stick = true;
    RA.$("transcript").replaceChildren();
    RA.$("d-alerts").replaceChildren();
    RA.$("d-steps").classList.add("hidden");
    RA.$("diag").classList.add("hidden");
    this.renderTranscript();
  },

  scrollEnd(force) {
    const t = RA.$("transcript");
    if (force || this.stick) t.scrollTop = t.scrollHeight;
  },

  // ---------- transcript ----------
  segNode(seg) {
    const d = RA.el("div", "seg" + (this.info && this.info.audio_url ? " audio-seek" : ""));
    d.dataset.id = seg.id || "";
    d.append(RA.el("span", "ts", `[${RA.fmt(seg.start)}]`));
    const b = RA.el("button", "spk" + (/^(Unknown|Speaker \d+)$/.test(seg.speaker) ? " unk" : ""), seg.speaker + ":");
    b.title = "Click to correct the speaker label";
    b.onclick = (e) => { e.stopPropagation(); if (seg.speaker_key && seg.id) this.pickSpeaker(seg); };
    d.append(b, RA.el("span", "txt", seg.text));
    d.onclick = () => {
      const a = RA.$("audio");
      if (this.info && this.info.audio_url && a) { a.currentTime = seg.start || 0; a.play().catch(() => {}); }
    };
    return d;
  },

  renderTranscript() {
    const t = RA.$("transcript");
    t.replaceChildren();
    if (!this.segments.length && !this.interim) t.append(RA.el("div", "empty", "Transcript will appear here as people speak."));
    this.segments.forEach((s) => t.append(this.segNode(s)));
    this.renderInterim();
    this.scrollEnd();
  },

  addSegment(seg) {
    if (this.segments.some((s) => s.id === seg.id)) return; // dedupe
    const t = RA.$("transcript");
    if (!this.segments.length) t.replaceChildren();
    this.segments.push(seg);
    this.interim = null;
    const i = RA.$("interim-row");
    if (i) i.remove();
    t.append(this.segNode(seg));
    this.scrollEnd();
  },

  setInterim(seg) {
    this.interim = seg;
    const t = RA.$("transcript");
    if (!this.segments.length) { const e = t.querySelector(".empty"); if (e) e.remove(); }
    this.renderInterim();
    this.scrollEnd();
  },

  renderInterim() {
    let row = RA.$("interim-row");
    if (!this.interim) { if (row) row.remove(); return; }
    const n = this.segNode({ ...this.interim, id: "" });
    n.classList.add("interim");
    n.id = "interim-row";
    if (row) row.replaceWith(n); else RA.$("transcript").append(n);
  },

  pickSpeaker(seg) {
    const roles = (this.info && this.info.roles) || [];
    const box = RA.$("modal-box");
    box.replaceChildren(RA.el("h1", null, "Correct speaker label"),
      RA.el("p", "muted", `All segments labelled "${seg.speaker}" will be assigned to the role you choose.`));
    const row = RA.el("div", "row");
    [...roles, "Unknown"].forEach((r) => {
      const b = RA.el("button", r === seg.speaker ? "primary" : "", r);
      b.onclick = async () => {
        const res = await fetch(`/api/sessions/${this.info.id}/speakers`, { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ speaker_key: seg.speaker_key, role: r }) });
        if (res.ok) RA.closeModal(); else RA.showError("Could not update speaker label.");
        if (res.ok && !RA.liveSocket) RA.openSession(this.info.id, true); // static view: refresh
      };
      row.append(b);
    });
    const c = RA.el("button", "link", "Cancel"); c.onclick = RA.closeModal; row.append(c);
    box.append(row);
    RA.$("modal").classList.remove("hidden");
  },

  // ---------- header / status ----------
  renderHeader(info, timerText) {
    this.info = info;
    RA.$("d-name").textContent = info.name;
    RA.$("d-config").textContent = `${info.config_name} - ${info.language}`;
    const st = RA.$("d-status");
    const map = { RECORDING: ["Recording", "rec"], CONNECTING: ["Connecting...", "warn"], PROCESSING: ["Processing", "warn"],
      FINALIZING: ["Finalizing", "warn"], COMPLETED: ["Completed", "ok"], FAILED: ["Failed", "bad"], CREATED: ["Created", ""] };
    const [txt, cls] = map[info.status] || [info.status, ""];
    st.textContent = txt; st.className = "chip " + cls;
    const f = RA.$("d-final");
    f.textContent = info.finalized ? "Final results" : "Live provisional analysis";
    f.className = "chip " + (info.finalized ? "ok" : "warn");
    if (timerText) RA.$("d-timer").textContent = timerText;
    const audio = RA.$("audio"), wrap = RA.$("d-audio");
    if (info.audio_url && audio.getAttribute("data-src") !== info.audio_url) {
      audio.src = info.audio_url; audio.setAttribute("data-src", info.audio_url);
    }
    wrap.classList.toggle("hidden", !info.audio_url);
    RA.$("btn-retry").classList.toggle("hidden", info.status !== "FAILED");
    RA.$("btn-stop").classList.toggle("hidden", !(info.mode === "live" && ["CONNECTING", "RECORDING"].includes(info.status)));
    (info.warnings || []).forEach((w) => this.alert(w));
    if (info.error) this.alert(info.error, true);
  },

  alert(msg, isErr) {
    const box = RA.$("d-alerts");
    if ([...box.children].some((c) => c.textContent === msg)) return;
    box.append(RA.el("div", "alert" + (isErr ? " err" : ""), msg));
  },

  renderSteps(steps) {
    const ol = RA.$("d-steps");
    ol.replaceChildren();
    (steps || []).forEach((s) => ol.append(RA.el("li", s.state, s.label)));
    ol.classList.toggle("hidden", !steps || !steps.length);
  },

  // ---------- right-hand panels ----------
  render(data) {
    this.data = data;
    this.renderHeader(data.session);
    const ov = data.coverage.overall;
    RA.$("d-overall").textContent = ov.display;
    RA.$("d-overall-sub").textContent = ov.applicable ? `${ov.met} of ${ov.applicable} applicable criteria met` : "no applicable criteria";
    RA.$("d-overall-bar").style.width = (ov.percent || 0) + "%";
    this.renderCoverage(data.coverage.topics);
    this.renderQuestions(data.questions);
    this.renderReport(data.report);
    RA.$("diag").textContent = JSON.stringify(data.diagnostics || {}, null, 2);
  },

  renderCoverage(topics) {
    const box = RA.$("coverage");
    box.replaceChildren();
    topics.forEach((t) => {
      const wrap = RA.el("div", "topic");
      const btn = RA.el("button");
      btn.setAttribute("aria-expanded", this.expanded.has(t.id));
      const head = RA.el("div", "head");
      head.append(RA.el("span", null, t.name), RA.el("span", null, t.display));
      const bar = RA.el("div", "bar"); const i = RA.el("i"); i.style.width = (t.percent || 0) + "%"; bar.append(i);
      const sub = RA.el("div", "sub");
      sub.append(RA.el("span", null, t.applicable ? `${t.met} of ${t.applicable} criteria completed` : "No applicable criteria"),
        RA.el("span", "tag " + t.status.replace(/\s/g, ""), t.status));
      btn.append(head, bar, sub);
      btn.onclick = () => { this.expanded.has(t.id) ? this.expanded.delete(t.id) : this.expanded.add(t.id); this.renderCoverage(topics); };
      wrap.append(btn);
      if (this.expanded.has(t.id)) {
        const ul = RA.el("ul");
        t.criteria.forEach((c) => {
          const li = RA.el("li");
          li.append(RA.el("span", "sym " + c.status, { MET: "✓", NOT_MET: "○", NEEDS_REVIEW: "!", NOT_APPLICABLE: "–" }[c.status]));
          const b = RA.el("button", null, c.label + (c.status === "NOT_APPLICABLE" ? " (not applicable)" : c.status === "NEEDS_REVIEW" ? " (needs review)" : ""));
          if (c.evidence || c.reason) { b.title = "Show evidence"; b.onclick = () => this.showEvidence(c); } else b.style.cursor = "default";
          li.append(b); ul.append(li);
        });
        wrap.append(ul);
      }
      box.append(wrap);
    });
  },

  showEvidence(c) {
    const box = RA.$("modal-box");
    box.replaceChildren(RA.el("h1", null, c.label), RA.el("p", "muted", c.status.replace("_", " ")));
    if (c.evidence) {
      box.append(RA.el("blockquote", null, `“${c.evidence.text}”`),
        RA.el("p", "small muted", `${c.evidence.speaker || "Unknown"} · ${c.evidence.timestamp} · ${c.evidence.segment_id}`));
    }
    if (c.reason) box.append(RA.el("p", null, c.reason));
    const row = RA.el("div", "row");
    if (c.evidence) {
      const j = RA.el("button", null, "Show in transcript");
      j.onclick = () => { RA.closeModal(); const n = document.querySelector(`.seg[data-id="${c.evidence.segment_id}"]`);
        if (n) { n.scrollIntoView({ block: "center" }); n.classList.add("hl"); setTimeout(() => n.classList.remove("hl"), 2500); } };
      row.append(j);
    }
    const x = RA.el("button", "primary", "Close"); x.onclick = RA.closeModal; row.append(x);
    box.append(row);
    RA.$("modal").classList.remove("hidden");
  },

  renderQuestions(qs) {
    const ol = RA.$("questions");
    ol.replaceChildren();
    if (!qs.length) { ol.append(RA.el("li", "empty", "All checklist items are covered.")); return; }
    qs.forEach((q) => {
      const li = RA.el("li", q.needs_review ? "review" : "");
      li.append(RA.el("span", null, q.question), RA.el("div", "why", `${q.topic} · ${q.label}${q.needs_review ? " · needs clarification" : ""}`));
      ol.append(li);
    });
  },

  renderReport(r) {
    const box = RA.$("report");
    box.replaceChildren();
    r.sections.forEach((sec) => {
      const d = RA.el("div", "rsec");
      d.append(RA.el("h3", null, sec.title));
      sec.rows.forEach((row) => {
        const rr = RA.el("div", "rrow");
        rr.append(RA.el("span", "k", row.label));
        const v = RA.el("span", "v" + (row.known ? "" : " none"));
        v.append(document.createTextNode(row.value));
        if (row.known) v.append(RA.el("span", "c muted", row.confirmed ? " (confirmed)" : " (not confirmed)"));
        if (row.previous && row.previous.length) v.append(RA.el("span", "c muted", ` · changed from ${row.previous[row.previous.length - 1]}`));
        rr.append(v); d.append(rr);
      });
      box.append(d);
    });
    const st = RA.el("div", "rsec"); st.append(RA.el("h3", null, "Booking Status"));
    st.append(RA.el("span", "status-box", r.booking_status === "Unknown" ? "Not established" : r.booking_status));
    box.append(st);
    if (r.summary) { const s = RA.el("div", "rsec"); s.append(RA.el("h3", null, "Summary"), RA.el("div", null, r.summary)); box.append(s); }
    const o = RA.el("div", "rsec"); o.append(RA.el("h3", null, "Outstanding Information"));
    if (!r.outstanding.length) o.append(RA.el("div", "muted", "None - all items addressed."));
    else {
      o.append(RA.el("div", null, r.outstanding.slice(0, 8).map((x) => x.label).join(", ") + (r.outstanding.length > 8 ? ` and ${r.outstanding.length - 8} more` : "")));
    }
    box.append(o);
    const n = RA.el("div", "rsec"); n.append(RA.el("h3", null, "Recommended Next Action"));
    n.append(RA.el("div", "next", r.next_action || "Continue the conversation."));
    box.append(n);
    if (r.final) {
      const f = RA.el("div", "rsec"); f.append(RA.el("h3", null, "Booking Outcome"), RA.el("div", null, r.final.outcome || "Not established"));
      if ((r.final.questions_not_asked || []).length) {
        f.append(RA.el("h3", null, "Questions Not Asked"));
        const ul = RA.el("ul"); r.final.questions_not_asked.forEach((q) => ul.append(RA.el("li", null, q))); f.append(ul);
      }
      box.append(f);
    }
  },
};
