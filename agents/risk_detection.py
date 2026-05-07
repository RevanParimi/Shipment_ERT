"""Shipment Risk Detection Agent.

Signal design rationale:

  Signal 1 — Explicit ERP/TMS status flag                    [STATIC RULE]
    status ∈ {"delayed", "customs_hold"}
    Reason: this is an authoritative system designation. The ERP set it — it IS
    delayed. An LLM cannot add information here; it can only re-read the flag.

  Signals 2 + 3 — Milestone slippage + GPS movement          [BATCH LLM]
    Facts computed in Python (unambiguous arithmetic):
      • worst_milestone: name of the milestone with the largest actual-vs-planned gap
      • milestone_delay_h: that gap in hours
      • gps_stuck: True if last 3 pings all have speed == 0
      • recent_speeds: list of last 3 GPS speeds
    These facts are sent in ONE batched Groq call covering all shipments.
    The LLM reasons about significance in context:
      • A 6h slip at "in_transit_hub1" for ocean (Maersk) is normal variance.
      • A 6h slip at "customs_clearance" for air (FedEx) risks a missed connection.
      • GPS freeze for a truck = stuck. For an ocean vessel = may be at anchor.
    Single batch call avoids N×2 individual calls while enabling comparative reasoning
    ("SHP-001's customs hold is more critical than SHP-004's hub delay because…").
    Formula fallback: old confidence formula activates when LLM is unavailable.

  Signal 4 — Email content                                   [PER-SHIPMENT LLM]
    Each shipment's emails are combined in one call. Per-shipment is required because
    email bodies are unique — they cannot be meaningfully batched.
    Parallel execution via ThreadPoolExecutor(max_workers=4).
    Fallback: keyword matching.
"""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from agents.utils import GROQ_AVAILABLE, GROQ_MODEL, call_with_retry, groq_client

_FMT = "%Y-%m-%d %H:%M:%S"

# Keyword fallback set (Signal 4 only, when LLM unavailable)
_DELAY_KEYWORDS = {
    "delay", "delayed", "hold", "stuck", "missed", "closure",
    "congestion", "alert", "urgent", "clearance", "issue",
}


# ---------------------------------------------------------------------------
# Signal 1 helper — deterministic
# ---------------------------------------------------------------------------

def _status_is_at_risk(status: str) -> bool:
    return status in ("delayed", "customs_hold")


# ---------------------------------------------------------------------------
# Signals 2 + 3 helpers — fact collection (Python) + batch LLM
# ---------------------------------------------------------------------------

def _collect_structured_facts(shp: dict, milestones: list[dict], gps: list[dict]) -> dict:
    """Extract measurable facts for one shipment — no LLM involved."""
    # Worst milestone delay
    worst_delay_h = 0.0
    worst_milestone = "none"
    for m in milestones:
        if m["status"] == "delayed" and m["actual_timestamp"] and m["planned_timestamp"]:
            try:
                planned = datetime.strptime(m["planned_timestamp"], _FMT)
                actual = datetime.strptime(m["actual_timestamp"], _FMT)
                h = (actual - planned).total_seconds() / 3600
                if h > worst_delay_h:
                    worst_delay_h = h
                    worst_milestone = m["milestone"]
            except ValueError:
                pass

    # GPS movement pattern
    recent = sorted(gps, key=lambda x: x["timestamp"])[-3:] if gps else []
    gps_stuck = len(recent) >= 3 and all(float(g["speed"]) == 0.0 for g in recent)
    recent_speeds = [round(float(g["speed"]), 1) for g in recent]

    return {
        "shipment_id": shp["shipment_id"],
        "mode": shp.get("mode", "unknown"),
        "carrier": shp.get("carrier", "unknown"),
        "status": shp.get("status", "unknown"),
        "worst_milestone": worst_milestone,
        "milestone_delay_h": round(worst_delay_h, 1),
        "gps_stuck": gps_stuck,
        "recent_speeds": recent_speeds,
    }


def _batch_assess_structured_signals(facts_list: list[dict]) -> dict[str, float]:
    """Single Groq call — assesses significance of milestone + GPS facts for
    ALL shipments at once. Returns {shipment_id: confidence 0.0-1.0}.

    Batching lets the LLM reason comparatively across shipments and apply
    domain knowledge about modes, carriers, and milestone types.
    """
    lines = []
    for d in facts_list:
        milestone_str = (
            f"'{d['worst_milestone']}' delayed {d['milestone_delay_h']:.0f}h"
            if d["milestone_delay_h"] > 0 else "no milestone delays"
        )
        gps_str = (
            f"GPS frozen (last 3 speeds: {d['recent_speeds']})"
            if d["gps_stuck"] else f"GPS moving (last 3 speeds: {d['recent_speeds']})"
        )
        lines.append(
            f"  {d['shipment_id']}: mode={d['mode']}, carrier={d['carrier']}, "
            f"status={d['status']}, {milestone_str}, {gps_str}"
        )

    prompt = (
        "You are a supply chain risk analyst assessing structured operational signals.\n\n"
        "Assign a risk confidence (0.0-1.0) for each shipment based ONLY on its "
        "milestone delays and GPS movement. Apply domain knowledge:\n"
        "  - Transport mode matters: 6h air delay cascades; 6h ocean delay is noise.\n"
        "  - Milestone type matters: customs_clearance hold is more serious than hub transit.\n"
        "  - GPS freeze interpretation: truck stuck = high risk; ocean at port = moderate.\n"
        "  - Carrier context: use your knowledge of DHL, Maersk, FedEx, DB Schenker, Kuehne+Nagel.\n"
        "  - If a shipment has neither milestone delays nor GPS freeze, return 0.0.\n\n"
        "Shipments:\n" + "\n".join(lines) + "\n\n"
        "Return ONLY a JSON object mapping each shipment_id to its confidence:\n"
        "{\"SHP-PO-XXXX\": <float 0.0-1.0>, ...}"
    )

    resp = call_with_retry(lambda: groq_client.chat.completions.create(  # type: ignore[union-attr]
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=400,
    ))
    raw = json.loads(resp.choices[0].message.content or "{}")

    # Keep only valid shipment_id keys with numeric values
    return {
        sid: min(max(float(val), 0.0), 1.0)
        for sid, val in raw.items()
        if sid.startswith("SHP-") and isinstance(val, (int, float))
    }


def _formula_structured_score(facts: dict) -> float:
    """Fallback: original formula-based confidence for signals 2+3."""
    signals: list[float] = []
    if facts["milestone_delay_h"] > 0:
        signals.append(min(0.50 + facts["milestone_delay_h"] / 100.0, 0.95))
    if facts["gps_stuck"]:
        signals.append(0.75)
    return max(signals) if signals else 0.0


# ---------------------------------------------------------------------------
# Signal 4 helpers — per-shipment email LLM
# ---------------------------------------------------------------------------

def _analyze_emails_llm(emails: list[dict], sid: str) -> dict:
    """Single Groq call for all emails of one shipment combined."""
    email_block = "\n\n".join(
        f"Email {i + 1}:\n  Subject: {e.get('subject', '')}\n  Body: {e.get('body', '')}"
        for i, e in enumerate(emails)
    )
    prompt = (
        f"You are a supply chain analyst. Analyze the following email(s) for shipment {sid}.\n\n"
        f"{email_block}\n\n"
        "Determine whether these emails indicate a GENUINE ongoing delay "
        "(i.e. the issue has not yet been resolved as of the latest email).\n"
        "Return ONLY valid JSON:\n"
        '{"is_delay": <true|false>, '
        '"estimated_delay_hours": <integer or null>, '
        '"delay_type": "<customs|port_congestion|road_closure|missed_pickup|documentation|weather|resolved|none>", '
        '"confidence": <float 0.0-1.0>, '
        '"summary": "<one sentence>"}'
    )
    resp = call_with_retry(lambda: groq_client.chat.completions.create(  # type: ignore[union-attr]
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=150,
    ))
    return json.loads(resp.choices[0].message.content or "{}")


def _keyword_email_signal(emails: list[dict]) -> dict:
    """Fallback keyword-based email analysis (no LLM)."""
    matched = sum(
        1 for e in emails
        if any(
            kw in (e.get("subject", "") + " " + e.get("body", "")).lower()
            for kw in _DELAY_KEYWORDS
        )
    )
    if matched == 0:
        return {"is_delay": False, "confidence": 0.0, "delay_type": "none", "summary": ""}
    return {
        "is_delay": True,
        "confidence": min(0.40 + matched * 0.15, 0.85),
        "delay_type": "unknown",
        "summary": f"{matched} email(s) matched delay keywords (keyword fallback).",
    }


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

def run_risk_detection(state: dict) -> dict:
    shipments: list[dict] = state["raw_transport"]["shipments"]
    all_milestones: list[dict] = state["raw_transport"]["milestones"]
    all_gps: list[dict] = state["raw_transport"]["gps_feed"]
    all_emails: list[dict] = state["parsed_emails"]

    # Build lookup maps
    milestone_map: dict[str, list] = defaultdict(list)
    for m in all_milestones:
        milestone_map[m["shipment_id"]].append(m)

    gps_map: dict[str, list] = defaultdict(list)
    for g in all_gps:
        gps_map[g["shipment_id"]].append(g)

    email_map: dict[str, list] = defaultdict(list)
    for e in all_emails:
        email_map[e["shipment_id"]].append(e)

    # -----------------------------------------------------------------------
    # Signals 2 + 3 — collect facts, then ONE batch LLM call
    # -----------------------------------------------------------------------
    all_facts: dict[str, dict] = {
        shp["shipment_id"]: _collect_structured_facts(shp, milestone_map[shp["shipment_id"]], gps_map[shp["shipment_id"]])
        for shp in shipments
    }

    # Only shipments with at least one signal worth assessing
    assessable = [f for f in all_facts.values() if f["milestone_delay_h"] > 0 or f["gps_stuck"]]
    structured_scores: dict[str, float] = {}

    if GROQ_AVAILABLE and assessable:
        try:
            structured_scores = _batch_assess_structured_signals(assessable)
        except Exception:
            # Batch LLM failed — fall back to formula for all
            for f in assessable:
                structured_scores[f["shipment_id"]] = _formula_structured_score(f)
    else:
        for f in assessable:
            structured_scores[f["shipment_id"]] = _formula_structured_score(f)

    # -----------------------------------------------------------------------
    # Signal 4 — email LLM (parallel, per-shipment)
    # -----------------------------------------------------------------------
    shipments_with_emails = [(sid, emails) for sid, emails in email_map.items() if emails]
    email_results: dict[str, dict] = {}

    if GROQ_AVAILABLE and shipments_with_emails:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_analyze_emails_llm, emails, sid): sid
                for sid, emails in shipments_with_emails
            }
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    email_results[sid] = future.result()
                except Exception:
                    email_results[sid] = _keyword_email_signal(email_map[sid])
    else:
        for sid, emails in shipments_with_emails:
            email_results[sid] = _keyword_email_signal(emails)

    # -----------------------------------------------------------------------
    # Combine all signals per shipment
    # -----------------------------------------------------------------------
    at_risk: list[dict] = []

    for shp in shipments:
        sid = shp["shipment_id"]
        signals: list[float] = []
        risk_types: list[str] = []
        facts = all_facts[sid]

        # Signal 1 — ERP status flag (deterministic, always correct)
        if _status_is_at_risk(shp["status"]):
            signals.append(0.90)
            risk_types.append(shp["status"])

        # Signals 2 + 3 — LLM-assessed significance (or formula fallback)
        score_23 = structured_scores.get(sid, 0.0)
        if score_23 > 0:
            signals.append(score_23)
            if facts["milestone_delay_h"] > 0:
                risk_types.append(
                    f"milestone_{facts['worst_milestone']}_delay_{int(facts['milestone_delay_h'])}h"
                )
            if facts["gps_stuck"]:
                risk_types.append("gps_stuck")

        # Signal 4 — email (LLM or keyword fallback)
        email_result = email_results.get(sid, {})
        if email_result.get("is_delay"):
            signals.append(float(email_result.get("confidence", 0.5)))
            risk_types.append(f"email_{email_result.get('delay_type', 'unknown')}")

        if signals:
            at_risk.append({
                "shipment_id": sid,
                "risk_type": "|".join(risk_types),
                "confidence": round(max(signals), 2),
                "email_analysis": email_result.get("summary", ""),
            })

    return {"at_risk_shipments": at_risk}
