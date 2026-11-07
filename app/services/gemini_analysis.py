"""Gemini conversation analysis: builds compact prompts, requests JSON, validates the response."""
import json
import logging
import re

from google.genai import types
from pydantic import ValidationError

from app.models.analysis import AnalysisResult
from app.models.session import Session
from app.services.gemini_client import GeminiClient, GeminiServiceError
from app.utils.validation import fmt_ts

log = logging.getLogger("repassist.analysis")

LANG_RULES = {
    "English": "Write all questions, summaries and reasons in English.",
    "Tamil": "Write all questions, summaries and reasons in Tamil (Unicode Tamil script). Keep names, dates, numbers and English hotel terms as spoken.",
    "English-Tamil (Mixed)": "The conversation mixes Tamil and English. Write questions, summaries and reasons in natural Tamil-English mixed style that an agent can read quickly. Keep names, dates and numbers as spoken.",
}

SYSTEM = """You are Sales Assist's silent analysis engine for hotel booking conversations between a Customer and a Hotel Booking Agent.
You never speak to the customer. You only evaluate the conversation transcript and return JSON.

Hard rules:
1. Judge each checklist criterion ONLY from what is actually said in the transcript. Never invent prices, rates, taxes, availability, policies, names, booking ids or completed actions.
2. Status values: MET (clear evidence), NOT_MET (not established), NOT_APPLICABLE (genuinely does not apply AND the conversation shows why, e.g. customer says no children - never because it was simply not discussed), NEEDS_REVIEW (conflicting, unclear or tentative evidence).
3. Every MET or NOT_APPLICABLE update MUST cite evidence_segment_ids (ids that exist in the transcript below) and an evidence_quote copied from those segments. No evidence -> do not mark MET.
4. A tentative or merely mentioned value is not "confirmed". Interest in a room is not a confirmed booking. If the agent says availability was checked, that is only a statement made in the call.
5. Corrections: if the customer changes a value (e.g. two rooms -> three rooms), set booking_updates to the LATEST value. The criterion stays MET if it was collected. Use NEEDS_REVIEW only when a contradiction is unresolved.
6. Only return criteria_updates for criteria whose status changes or is newly supported by the NEW segments. Use only criterion ids from the checklist.
7. Never output coverage percentages or scores.
8. Do not extract payment card numbers or security codes.
9. Speaker labels may be neutral (spk_1, Speaker 1, Unknown). Do not assume the first speaker is the agent. Only add speaker_hints when the content makes the role clear (cite segment ids from that speaker).
10. suggested_questions: short, polite, actionable follow-ups for criteria that are not yet MET or NOT_APPLICABLE (most important first, at most 5). Never assume unavailable inventory, invented prices or unconfirmed bookings.
11. booking_updates field names (use these when applicable): customer_name, check_in_date, check_out_date, nights, adults, children, rooms, room_type, bed_preference, special_requests, availability_discussed, room_rate, additional_charges, total_estimated_cost, contact_number, email, payment_info. value must be a short string exactly as established.
12. report_update.booking_status must be one of: Inquiry, Options Discussed, Pending Confirmation, Confirmed, Declined, Follow-up Required - or empty if unsupported. report_update.summary is a concise running summary of the whole conversation so far (max ~80 words). recommended_next_action is ONE clear action.

Return ONLY a JSON object with this shape:
{
 "evaluated_through_segment_id": "seg_xxx",
 "criteria_updates": [{"topic_id": "", "criterion_id": "", "status": "MET|NOT_MET|NOT_APPLICABLE|NEEDS_REVIEW", "evidence_segment_ids": ["seg_001"], "evidence_quote": "", "reason": ""}],
 "booking_updates": [{"field": "", "value": "", "confirmed": false, "source_segment_id": "seg_001"}],
 "suggested_questions": [{"criterion_id": "", "question": "", "priority": "high|medium|low"}],
 "speaker_hints": [{"speaker_key": "", "role": "", "evidence_segment_ids": []}],
 "report_update": {"summary": "", "recommended_next_action": "", "booking_status": ""}
}"""

FINAL_ADDENDUM = """
FINAL RECONCILIATION: you are given the FULL transcript. Re-evaluate EVERY criterion from scratch against the whole conversation (include updates for all criteria whose current state is wrong, including invalidations), and make report_update.summary a complete summary. Also add:
 "final_report": {"outcome": "the actual outcome established by the call (e.g. inquiry only, pending, confirmed, declined) - never claim a booking that was not finalized", "missing_information": ["..."], "questions_not_asked": ["relevant questions that should have been asked but were not adequately addressed"]}"""


def _seg_line(s) -> dict:
    return {"id": s.id, "speaker": s.speaker, "speaker_key": s.speaker_key, "time": fmt_ts(s.start), "text": s.text}


def build_prompt(session: Session, new_segments: list, context_segments: list, final: bool) -> str:
    checklist = [
        {"topic_id": t["id"], "criterion_id": c["id"], "label": c["label"], "instruction": c.get("instruction", "")}
        for t in session.config["topics"]
        for c in t["criteria"]
    ]
    state = {
        cid: {"status": st.status, "quote": (st.evidence.text if st.evidence else "")[:120]}
        for cid, st in session.criteria.items()
        if st.status != "NOT_MET"
    }
    booking = {k: {"value": b.value, "confirmed": b.confirmed} for k, b in session.booking.items()}
    payload = {
        "session_id": session.id,
        "language": session.language,
        "roles": session.config.get("roles", []),
        "checklist": checklist,
        "current_criterion_states_other_than_NOT_MET": state,
        "current_booking_information": booking,
        "current_summary": session.summary,
        "earlier_context_segments": [_seg_line(s) for s in context_segments],
        "NEW_segments_to_evaluate": [_seg_line(s) for s in new_segments],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_result(text: str) -> AnalysisResult:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    if not t:
        raise ValueError("empty response")
    data = json.loads(t)
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    return AnalysisResult.model_validate(data)


def _thinking(model: str):
    import os

    if "flash" not in (model or "").lower():
        return None  # pro models cannot disable thinking
    try:
        return types.ThinkingConfig(thinking_budget=int(os.getenv("ANALYSIS_THINKING_BUDGET", "0")))
    except Exception:  # noqa: BLE001
        return None


class GeminiAnalysis:
    def __init__(self, gemini: GeminiClient):
        self.gemini = gemini

    async def analyze(self, session: Session, new_segments: list, context_segments: list, final: bool = False) -> AnalysisResult:
        """Return a validated AnalysisResult. Raises GeminiServiceError on service/validation failure."""
        s = self.gemini.settings
        system = SYSTEM + "\n" + LANG_RULES.get(session.language, LANG_RULES["English"]) + (FINAL_ADDENDUM if final else "")
        prompt = build_prompt(session, new_segments, context_segments, final)
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0.1,
            # Flash models "think" by default (often 5-20 s per call). The checklist is structured extraction, so
            # turn thinking off for speed; ANALYSIS_THINKING_BUDGET=-1 restores dynamic thinking.
            thinking_config=_thinking(s.analysis_model),
        )
        last_err = "invalid response"
        for attempt in range(2):  # one limited retry for malformed JSON
            try:
                resp = await self.gemini.client.aio.models.generate_content(
                    model=s.analysis_model, contents=prompt, config=cfg
                )
            except GeminiServiceError:
                raise
            except Exception as e:  # noqa: BLE001
                err = self.gemini.classify(e)
                self.gemini.mark("analysis", False, err)
                raise err
            try:
                result = parse_result(resp.text)
                self.gemini.mark("analysis", True)
                return result
            except (ValueError, ValidationError, json.JSONDecodeError) as e:
                last_err = type(e).__name__
                log.warning("Invalid analysis JSON (attempt %d): %s", attempt + 1, last_err)  # no content logged
        raise GeminiServiceError("invalid_response", f"Gemini returned an invalid analysis response ({last_err}).")
