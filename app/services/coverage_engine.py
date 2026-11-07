"""Mathematical coverage engine.

Pure Python, no LLM calls, no domain (hotel) knowledge: it works on any valid configuration.
Gemini decides which criteria are met; this module alone computes percentages.

    coverage % = applicable criteria MET / applicable criteria * 100

NOT_APPLICABLE criteria leave the denominator; NEEDS_REVIEW counts as not met.
"""
from typing import Any, Optional

MET, NOT_MET, NOT_APPLICABLE, NEEDS_REVIEW = "MET", "NOT_MET", "NOT_APPLICABLE", "NEEDS_REVIEW"
VALID_STATES = (MET, NOT_MET, NOT_APPLICABLE, NEEDS_REVIEW)


def _status_of(states: dict, cid: str) -> str:
    st = states.get(cid)
    if st is None:
        return NOT_MET
    status = st.get("status") if isinstance(st, dict) else getattr(st, "status", NOT_MET)
    return status if status in VALID_STATES else NOT_MET


def format_percent(p: Optional[float]) -> str:
    """Round only for display (2 decimals, trailing zeros removed). None -> 'N/A'."""
    if p is None:
        return "N/A"
    s = f"{p:.2f}".rstrip("0").rstrip(".")
    return f"{s}%"


def topic_status(applicable: int, met: int, review: int) -> str:
    if applicable == 0:
        return "Not Applicable"
    if met == applicable:
        return "Complete"
    if review > 0:
        return "Needs Review"
    if met > 0:
        return "In Progress"
    return "Not Started"


def compute_coverage(config: dict, states: dict) -> dict:
    """Return per-topic and overall coverage for a config snapshot and criterion states."""
    seen: set = set()  # a criterion id is only ever counted once (no double counting)
    topics_out = []
    tot_app = tot_met = 0
    for topic in config.get("topics", []):
        applicable = met = review = 0
        crits = []
        for c in topic.get("criteria", []):
            cid = c["id"]
            if cid in seen:
                continue
            seen.add(cid)
            status = _status_of(states, cid)
            if status != NOT_APPLICABLE:
                applicable += 1
            if status == MET:
                met += 1
            if status == NEEDS_REVIEW:
                review += 1
            crits.append({"id": cid, "label": c.get("label", cid), "status": status})
        pct = (met / applicable * 100.0) if applicable else None
        topics_out.append(
            {
                "id": topic["id"],
                "name": topic.get("name", topic["id"]),
                "applicable": applicable,
                "met": met,
                "percent": pct,
                "display": format_percent(pct),
                "status": topic_status(applicable, met, review),
                "criteria": crits,
            }
        )
        tot_app += applicable
        tot_met += met
    overall = (tot_met / tot_app * 100.0) if tot_app else None  # never an average of rounded topic values
    return {
        "overall": {
            "applicable": tot_app,
            "met": tot_met,
            "percent": overall,
            "display": format_percent(overall),
        },
        "topics": topics_out,
    }
