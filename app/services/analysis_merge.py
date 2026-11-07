"""Safe, validated merging of a Gemini analysis result into session state.

Rules enforced here (never trusted from the model):
- MET / NOT_APPLICABLE need real evidence: cited segment ids must exist and the quote must be
  supported by their text. Unsupported updates are rejected, previous state is kept.
- Stale results (older revision than what is already applied for a criterion/field) are rejected.
- Booking values must cite an existing source segment.
"""
from app.models.analysis import AnalysisResult
from app.models.session import BookingField, CriterionState, Evidence, Session, now_iso
from app.services.coverage_engine import MET, NEEDS_REVIEW, NOT_APPLICABLE, NOT_MET
from app.utils.validation import fmt_ts, quote_supported

BOOKING_STATUSES = [
    "Inquiry",
    "Options Discussed",
    "Pending Confirmation",
    "Confirmed",
    "Declined",
    "Follow-up Required",
]


def criterion_ids(config: dict) -> set:
    return {c["id"] for t in config.get("topics", []) for c in t.get("criteria", [])}


def init_criteria(session: Session) -> None:
    for t in session.config.get("topics", []):
        for c in t.get("criteria", []):
            session.criteria.setdefault(c["id"], CriterionState())


def _evidence(session: Session, seg_ids: list, quote: str):
    """Return (Evidence, None) if the cited segments exist and support the quote, else (None, why)."""
    segs = []
    for sid in seg_ids:
        seg = session.segment_by_id(sid)
        if seg is None:
            return None, f"unknown segment {sid}"
        segs.append(seg)
    if not segs:
        return None, "no evidence segments"
    if not quote_supported(quote, " ".join(s.text for s in segs)):
        return None, "quote not supported by cited segment"
    first = segs[0]
    return (
        Evidence(
            text=quote.strip(),
            speaker=first.speaker,
            segment_id=first.id,
            segment_ids=[s.id for s in segs],
            timestamp=fmt_ts(first.start),
        ),
        None,
    )


def merge_analysis(session: Session, result: AnalysisResult, revision: int) -> list:
    """Merge `result` (issued as `revision`) into `session`. Returns a list of rejection notes."""
    rejected: list = []
    valid_ids = criterion_ids(session.config)
    now = now_iso()

    if revision < session.applied_revision:
        # whole response is older than something already applied: keep per-item checks below strict
        rejected.append("stale analysis result")

    for u in result.criteria_updates:
        if u.criterion_id not in valid_ids:
            rejected.append(f"unknown criterion {u.criterion_id}")
            continue
        cur = session.criteria.setdefault(u.criterion_id, CriterionState())
        if revision < cur.revision:
            rejected.append(f"stale update for {u.criterion_id}")
            continue

        if u.status == MET:
            ev, why = _evidence(session, u.evidence_segment_ids, u.evidence_quote)
            if ev is None:
                rejected.append(f"{u.criterion_id}: MET rejected ({why})")
                continue
            session.criteria[u.criterion_id] = CriterionState(
                status=MET, evidence=ev, reason=u.reason.strip(), revision=revision, evaluated_at=now
            )
        elif u.status == NOT_APPLICABLE:
            ev, why = _evidence(session, u.evidence_segment_ids, u.evidence_quote)
            if ev is None or len(u.reason.strip()) < 8:
                rejected.append(f"{u.criterion_id}: NOT_APPLICABLE rejected (needs reason and evidence)")
                continue
            session.criteria[u.criterion_id] = CriterionState(
                status=NOT_APPLICABLE, evidence=ev, reason=u.reason.strip(), revision=revision, evaluated_at=now
            )
        elif u.status == NEEDS_REVIEW:
            if not u.reason.strip():
                rejected.append(f"{u.criterion_id}: NEEDS_REVIEW rejected (no reason)")
                continue
            ev, _ = _evidence(session, u.evidence_segment_ids, u.evidence_quote)
            session.criteria[u.criterion_id] = CriterionState(
                status=NEEDS_REVIEW, evidence=ev or cur.evidence, reason=u.reason.strip(),
                revision=revision, evaluated_at=now,
            )
        elif u.status == NOT_MET:
            # Only meaningful as an invalidation of earlier evidence, and only with a stated reason.
            if cur.status in (MET, NOT_APPLICABLE, NEEDS_REVIEW) and u.reason.strip():
                session.criteria[u.criterion_id] = CriterionState(
                    status=NOT_MET, evidence=None, reason=u.reason.strip(), revision=revision, evaluated_at=now
                )

    for b in result.booking_updates:
        key = b.field.strip().lower().replace(" ", "_")
        val = b.value.strip()
        if not key or not val:
            continue
        if session.segment_by_id(b.source_segment_id) is None:
            rejected.append(f"booking '{key}': unknown source segment")
            continue
        cur = session.booking.get(key)
        if cur and revision < cur.revision:
            rejected.append(f"stale booking update for {key}")
            continue
        prev = list(cur.previous) if cur else []
        if cur and cur.value != val:
            prev.append(cur.value)  # keep the history of corrections
        session.booking[key] = BookingField(
            value=val, confirmed=b.confirmed, source_segment_id=b.source_segment_id,
            revision=revision, previous=prev,
        )

    # Only a result at least as new as everything applied may replace the narrative fields.
    if revision >= session.applied_revision:
        ru = result.report_update
        if ru.summary.strip():
            session.summary = ru.summary.strip()[:1500]
        if ru.recommended_next_action.strip():
            session.next_action = ru.recommended_next_action.strip()[:400]
        if ru.booking_status.strip() in BOOKING_STATUSES:
            session.booking_status = ru.booking_status.strip()
        for q in result.suggested_questions:
            if q.criterion_id in valid_ids and q.question.strip():
                session.question_pool[q.criterion_id] = {"question": q.question.strip()[:300], "priority": q.priority}
        for h in result.speaker_hints:
            _apply_speaker_hint(session, h, rejected)
        session.applied_revision = revision
    return rejected


def _apply_speaker_hint(session: Session, hint, rejected: list) -> None:
    """Adopt a model-proposed role for a raw speaker label only with supporting evidence."""
    roles = [r.lower() for r in session.config.get("roles", [])]
    if hint.role.strip().lower() not in roles:
        return
    if hint.speaker_key in session.speaker_map:  # manual correction always wins
        return
    ok = any(
        (s := session.segment_by_id(i)) is not None and s.speaker_key == hint.speaker_key
        for i in hint.evidence_segment_ids
    )
    if not ok:
        rejected.append(f"speaker hint for {hint.speaker_key} lacks evidence")
        return
    role = next(r for r in session.config["roles"] if r.lower() == hint.role.strip().lower())
    apply_speaker_role(session, hint.speaker_key, role, manual=False)


def apply_speaker_role(session: Session, speaker_key: str, role: str, manual: bool) -> None:
    if manual:
        session.speaker_map[speaker_key] = role
    for seg in session.segments:
        if seg.speaker_key == speaker_key:
            seg.speaker = role
    # evidence speaker labels follow the corrected role
    for st in session.criteria.values():
        if st.evidence:
            seg = session.segment_by_id(st.evidence.segment_id)
            if seg and seg.speaker_key == speaker_key:
                st.evidence.speaker = role
