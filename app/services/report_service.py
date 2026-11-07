"""Dashboard snapshots, suggested-question selection, final report and HTML export."""
import base64
import html
from pathlib import Path
from typing import Optional

from app.models.session import Session
from app.services.coverage_engine import MET, NEEDS_REVIEW, NOT_APPLICABLE, compute_coverage
from app.utils.validation import fmt_ts

PRIO = {"high": 0, "medium": 1, "low": 2}

# Report panel layout: (key, label)
BOOKING_SECTIONS = [
    ("Booking Overview", [("customer_name", "Customer Name"), ("check_in_date", "Check-in Date"), ("check_out_date", "Check-out Date"),
                          ("nights", "Number of Nights"), ("adults", "Adults"), ("children", "Children"), ("rooms", "Number of Rooms")]),
    ("Room Requirements", [("room_type", "Preferred Room Type"), ("bed_preference", "Bed Preference"), ("special_requests", "Special Requests")]),
    ("Pricing and Availability", [("availability_discussed", "Availability Discussed"), ("room_rate", "Quoted Room Rate"),
                                  ("additional_charges", "Additional Charges"), ("total_estimated_cost", "Total Estimated Cost")]),
    ("Contact", [("contact_number", "Contact Number"), ("email", "Email")]),
]


def booking_rows(session: Session) -> list:
    """Sections of {label, value, confirmed}; unknown values are shown as 'Not discussed'."""
    sections = []
    known = set()
    for title, fields in BOOKING_SECTIONS:
        rows = []
        for key, label in fields:
            known.add(key)
            b = session.booking.get(key)
            rows.append({"key": key, "label": label, "value": b.value if b else "Not discussed",
                         "confirmed": bool(b and b.confirmed), "known": bool(b), "previous": list(b.previous) if b else []})
        sections.append({"title": title, "rows": rows})
    extra = [{"key": k, "label": k.replace("_", " ").title(), "value": b.value, "confirmed": b.confirmed, "known": True, "previous": list(b.previous)}
             for k, b in session.booking.items() if k not in known]
    if extra:
        sections.append({"title": "Other Details", "rows": extra})
    return sections


def states_for_engine(session: Session) -> dict:
    return {cid: {"status": st.status} for cid, st in session.criteria.items()}


def coverage(session: Session) -> dict:
    cov = compute_coverage(session.config, states_for_engine(session))
    for t in cov["topics"]:
        for c in t["criteria"]:
            st = session.criteria.get(c["id"])
            c["reason"] = st.reason if st else ""
            c["evidence"] = st.evidence.model_dump() if st and st.evidence else None
    return cov


def suggested_questions(session: Session, limit: int = 5) -> list:
    """Questions for criteria that still need work. Disappear automatically once a criterion is satisfied."""
    cands = []
    order = 0
    for t in session.config.get("topics", []):
        for c in t.get("criteria", []):
            order += 1
            st = session.criteria.get(c["id"])
            status = st.status if st else "NOT_MET"
            if status in (MET, NOT_APPLICABLE):
                continue
            pooled = session.question_pool.get(c["id"])
            text = (pooled or {}).get("question") or c.get("question") or c.get("instruction") or c["label"]
            prio = (pooled or {}).get("priority") or c.get("priority", "medium")
            rank = 0 if status == NEEDS_REVIEW else PRIO.get(prio, 1)  # conflicts first
            cands.append((rank, order, {"criterion_id": c["id"], "topic": t["name"], "label": c["label"],
                                        "question": text, "priority": "high" if status == NEEDS_REVIEW else prio,
                                        "needs_review": status == NEEDS_REVIEW}))
    cands.sort(key=lambda x: (x[0], x[1]))
    return [c[2] for c in cands[:limit]]


def outstanding(session: Session) -> list:
    out = []
    for t in session.config.get("topics", []):
        for c in t.get("criteria", []):
            st = session.criteria.get(c["id"])
            status = st.status if st else "NOT_MET"
            if status not in (MET, NOT_APPLICABLE):
                out.append({"topic": t["name"], "label": c["label"], "needs_review": status == NEEDS_REVIEW})
    return out


def dashboard(session: Session) -> dict:
    cov = coverage(session)
    return {
        "session": session_info(session),
        "coverage": cov,
        "questions": suggested_questions(session),
        "report": {
            "sections": booking_rows(session),
            "booking_status": session.booking_status,
            "summary": session.summary,
            "outstanding": outstanding(session),
            "next_action": session.next_action,
            "final": session.final_report if session.finalized else None,
        },
        "diagnostics": session.timings,
    }


def session_info(session: Session) -> dict:
    return {
        "id": session.id, "name": session.name, "config_name": session.config.get("name", ""),
        "language": session.language, "mode": session.mode, "status": session.status.value,
        "created_at": session.created_at, "duration_s": session.duration_s,
        "finalized": session.finalized, "provisional": not session.finalized,
        "warnings": session.warnings, "error": session.error,
        "audio_url": f"/api/sessions/{session.id}/audio" if session.audio_file else None,
        "roles": session.config.get("roles", []), "overall_display": session.overall_display,
        "speaker_map": session.speaker_map,
    }


def final_report(session: Session) -> dict:
    cov = coverage(session)
    fr = session.final_report or {}
    topics = []
    for t in cov["topics"]:
        topics.append({
            "name": t["name"], "display": t["display"], "applicable": t["applicable"], "met": t["met"], "status": t["status"],
            "incomplete": [c["label"] for c in t["criteria"] if c["status"] not in (MET, NOT_APPLICABLE)],
            "criteria": t["criteria"],
        })
    missing = fr.get("missing_information") or [f'{o["topic"]}: {o["label"]}' for o in outstanding(session)]
    return {
        "session": {"name": session.name, "configuration": session.config.get("name", ""), "language": session.language,
                    "date": session.created_at, "duration": fmt_ts(session.duration_s)},
        "summary": session.summary or "No summary available.",
        "booking": booking_rows(session),
        "overall": cov["overall"],
        "topics": topics,
        "missing_information": missing,
        "questions_not_asked": fr.get("questions_not_asked", []),
        "outcome": fr.get("outcome") or (f"{session.booking_status} (not finalized in the conversation)" if session.booking_status != "Unknown" else "Not established"),
        "booking_status": session.booking_status,
        "next_action": session.next_action or "Review the conversation and follow up with the customer.",
    }


CSS = """body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2937;margin:0;background:#f3f4f6}
main{max-width:960px;margin:24px auto;background:#fff;padding:32px;border-radius:8px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px;color:#1d4ed8}
.meta{color:#6b7280;font-size:13px}.bar{background:#e5e7eb;border-radius:4px;height:8px;overflow:hidden}.bar i{display:block;height:100%;background:#2563eb}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:6px 8px;border-bottom:1px solid #f0f0f0;text-align:left;vertical-align:top}
.t{font-size:14px;margin:6px 0}.t b{color:#1d4ed8}.ts{color:#6b7280;font-size:12px}.ok{color:#047857}.no{color:#b45309}.ev{color:#6b7280;font-size:12px;font-style:italic}
@media print{body{background:#fff}main{margin:0;padding:0;max-width:none}audio{display:none}}"""


def export_html(session: Session, audio_path: Optional[Path], embed_max_mb: float) -> str:
    r = final_report(session)
    e = html.escape
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Sales Assist Report - {e(session.name)}</title><style>{CSS}</style></head><body><main>"]
    out.append(f"<h1>{e(session.name)}</h1><div class='meta'>{e(r['session']['configuration'])} &middot; {e(r['session']['language'])} &middot; "
               f"{e(r['session']['date'])} &middot; Duration {e(r['session']['duration'])}"
               f"{'' if session.finalized else ' &middot; <b>Provisional (session not finalized)</b>'}</div>")
    if audio_path and audio_path.exists():
        if audio_path.stat().st_size <= embed_max_mb * 1024 * 1024:
            b64 = base64.b64encode(audio_path.read_bytes()).decode()
            out.append(f"<h2>Recording</h2><audio controls src='data:{e(session.audio_mime or 'audio/wav')};base64,{b64}'></audio>")
        else:
            out.append(f"<h2>Recording</h2><p class='meta'>Audio is not embedded: the file is larger than the {embed_max_mb:g} MB embed limit. "
                       "The recording remains in the application's local session folder.</p>")
    elif session.audio_file is None:
        out.append("<p class='meta'>No audio was stored for this session.</p>")
    out.append(f"<h2>Overall Coverage: {e(r['overall']['display'])}</h2><div class='meta'>{r['overall']['met']} of {r['overall']['applicable']} applicable criteria met</div>")
    out.append(f"<h2>Conversation Summary</h2><p>{e(r['summary'])}</p>")
    out.append(f"<h2>Booking Outcome</h2><p>{e(r['outcome'])}</p><p class='meta'>Status: {e(r['booking_status'])}</p>")
    out.append("<h2>Booking Information</h2><table>")
    for sec in r["booking"]:
        out.append(f"<tr><th colspan=2>{e(sec['title'])}</th></tr>")
        for row in sec["rows"]:
            tag = "" if not row["known"] else (" <span class='ok'>(confirmed)</span>" if row["confirmed"] else " <span class='no'>(not confirmed)</span>")
            out.append(f"<tr><td>{e(row['label'])}</td><td>{e(row['value'])}{tag}</td></tr>")
    out.append("</table><h2>Topic Coverage</h2>")
    cov = coverage(session)
    for t in cov["topics"]:
        pct = t["percent"] or 0
        out.append(f"<div class='t'><b>{e(t['name'])}</b> - {e(t['display'])} ({t['met']} of {t['applicable']} criteria) &middot; {e(t['status'])}"
                   f"<div class='bar'><i style='width:{pct:.2f}%'></i></div></div><table>")
        for c in t["criteria"]:
            ev = c.get("evidence")
            evtxt = f"<div class='ev'>&ldquo;{e(ev['text'])}&rdquo; - {e(ev['speaker'])} [{e(ev['timestamp'])}]</div>" if ev else ""
            rs = f"<div class='ev'>{e(c['reason'])}</div>" if c.get("reason") and not ev else ""
            out.append(f"<tr><td style='width:30%'>{e(c['label'])}</td><td>{e(c['status'].replace('_', ' '))}{evtxt}{rs}</td></tr>")
        out.append("</table>")
    out.append("<h2>Missing Information</h2>" + ("<ul>" + "".join(f"<li>{e(m)}</li>" for m in r["missing_information"]) + "</ul>" if r["missing_information"] else "<p>None.</p>"))
    out.append("<h2>Questions Not Asked</h2>" + ("<ul>" + "".join(f"<li>{e(m)}</li>" for m in r["questions_not_asked"]) + "</ul>" if r["questions_not_asked"] else "<p>None identified.</p>"))
    out.append(f"<h2>Recommended Next Action</h2><p>{e(r['next_action'])}</p><h2>Full Transcript</h2>")
    if session.segments:
        for s in session.segments:
            out.append(f"<div class='t'><span class='ts'>[{fmt_ts(s.start)}]</span> <b>{e(s.speaker)}:</b> {e(s.text)}</div>")
    else:
        out.append("<p>No transcript.</p>")
    out.append("<p class='meta' style='margin-top:32px'>Generated by Sales Assist. Audio and transcripts were processed by Google Gemini. "
               "AI-generated analysis may contain errors; the human agent is responsible for confirming bookings.</p></main></body></html>")
    return "".join(out)
