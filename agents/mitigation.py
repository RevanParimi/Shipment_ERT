"""Mitigation Decision Agent.

Uses Groq (llama-3.3-70b-versatile) to reason about the best corrective action
for each at-risk shipment.  Falls back to a deterministic rule engine when the
Groq API key is absent or the call fails, ensuring the pipeline never crashes.

By this stage the state already contains LLM-enriched signals from Agents 2 and 3:
  - risk["email_analysis"]   — LLM summary of email thread
  - impact["key_concern"]    — LLM holistic business impact sentence
  - impact["urgency_score"]  — LLM 1-10 urgency
These are passed into the mitigation prompt, giving the LLM full context.

Parallelism: per-shipment decisions are independent — submitted concurrently via
ThreadPoolExecutor(max_workers=4).

Actions and their estimated execution costs (USD):
  hold            →  $0       (wait-and-watch)
  notify_customer →  $50      (proactive comms)
  reroute         →  $1 200   (alternative lane)
  expedite        →  $2 500   (priority handling fee)
  mode_switch     →  $4 000   (e.g. ocean → air)
  escalate        →  $0       (human review — no autonomous cost)
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.utils import GROQ_AVAILABLE, GROQ_MODEL, call_with_retry, groq_client

_ACTION_COSTS: dict[str, int] = {
    "hold": 0,
    "notify_customer": 50,
    "reroute": 1_200,
    "expedite": 2_500,
    "mode_switch": 4_000,
    "escalate": 0,
}

_EMPTY_IMPACT: dict = {
    "severity": "LOW",
    "type": "delivery_delay",
    "days_at_risk": 0,
    "priority": "LOW",
    "buffer_days": 0,
    "urgency_score": 1,
    "key_concern": "",
}


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

def _build_prompt(shp: dict, po: dict, impact: dict, risk: dict) -> str:
    key_concern = impact.get("key_concern", "")
    email_summary = risk.get("email_analysis", "")
    urgency = impact.get("urgency_score", "N/A")

    return (
        "You are a supply chain decision AI. "
        "Given the enriched context below (risk signals from sensor data + LLM email analysis + LLM impact assessment), "
        "choose the single best mitigation action.\n\n"
        f"Shipment: {shp.get('shipment_id')}\n"
        f"  Carrier: {shp.get('carrier')} | Mode: {shp.get('mode')}\n"
        f"  Route: {shp.get('origin')} → {shp.get('dest')}\n"
        f"  Status: {shp.get('status')} | Location: {shp.get('current_location')}\n"
        f"  Planned arrival: {shp.get('planned_arrival')}\n\n"
        f"Purchase Order: {po.get('po_id', 'N/A')}\n"
        f"  Supplier: {po.get('supplier')} | Material: {po.get('material')}\n"
        f"  Priority: {po.get('priority')} | Required by: {po.get('required_delivery_date')}\n\n"
        f"Risk signals: {risk.get('risk_type')} (sensor confidence={risk.get('confidence')})\n"
        + (f"Email intelligence: {email_summary}\n" if email_summary else "")
        + f"\nImpact assessment (LLM):\n"
        f"  Severity: {impact.get('severity')} | Type: {impact.get('type')}\n"
        f"  Urgency score: {urgency}/10 | Buffer: {impact.get('buffer_days')}d\n"
        + (f"  Key concern: {key_concern}\n" if key_concern else "")
        + "\nRespond ONLY with valid JSON:\n"
        '{"action": "<hold|notify_customer|reroute|expedite|mode_switch|escalate>", '
        '"rationale": "<one concise sentence>", "confidence": <float 0.0-1.0>}'
    )


def _llm_decide(shp: dict, po: dict, impact: dict, risk: dict) -> tuple[str, str, float]:
    resp = call_with_retry(lambda: groq_client.chat.completions.create(  # type: ignore[union-attr]
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": _build_prompt(shp, po, impact, risk)}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=200,
    ))
    content = resp.choices[0].message.content or "{}"
    parsed = json.loads(content)
    return (
        parsed.get("action", "escalate"),
        parsed.get("rationale", ""),
        float(parsed.get("confidence", 0.5)),
    )


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

def _rule_decide(impact: dict, risk: dict) -> tuple[str, str, float]:
    severity = impact.get("severity", "LOW")
    risk_type = risk.get("risk_type", "")
    confidence = float(risk.get("confidence", 0.5))

    if severity == "HIGH":
        if "customs_hold" in risk_type or "gps_stuck" in risk_type:
            return "mode_switch", "High-severity disruption; switch to faster transport mode.", confidence
        return "expedite", "High-severity delay; expedite to meet delivery commitment.", confidence
    if severity == "MEDIUM":
        return "reroute", "Medium-severity delay; reroute via alternative lane.", confidence
    return "notify_customer", "Low-severity delay; notify customer proactively.", max(confidence, 0.70)


# ---------------------------------------------------------------------------
# Per-shipment worker (called inside ThreadPoolExecutor)
# ---------------------------------------------------------------------------

def _decide_one(risk: dict, shp: dict, po: dict, impact: dict) -> tuple[str, dict]:
    sid = risk["shipment_id"]

    if GROQ_AVAILABLE:
        try:
            action, rationale, confidence = _llm_decide(shp, po, impact, risk)
        except Exception as exc:
            action, rationale, confidence = "escalate", f"LLM error — escalating: {exc}", 0.0
    else:
        action, rationale, confidence = _rule_decide(impact, risk)

    return sid, {
        "action": action,
        "rationale": rationale,
        "confidence": round(float(confidence), 2),
        "cost_delta": _ACTION_COSTS.get(str(action), 0),
        "severity": impact.get("severity"),
        "decided_by": "llm" if GROQ_AVAILABLE else "rules",
    }


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

def run_mitigation(state: dict) -> dict:
    at_risk: list[dict] = state["at_risk_shipments"]
    if not at_risk:
        return {"mitigation_plan": {}}

    impact_scores: dict[str, dict] = state["impact_scores"]
    po_map: dict[str, dict] = {p["po_id"]: p for p in state["raw_erp"]["purchase_orders"]}
    shp_map: dict[str, dict] = {s["shipment_id"]: s for s in state["raw_transport"]["shipments"]}

    mitigation_plan: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                _decide_one,
                risk,
                shp_map.get(risk["shipment_id"], {}),
                po_map.get(shp_map.get(risk["shipment_id"], {}).get("po_id", ""), {}),
                impact_scores.get(risk["shipment_id"], _EMPTY_IMPACT),
            ): risk["shipment_id"]
            for risk in at_risk
        }
        for future in as_completed(futures):
            sid = futures[future]
            try:
                sid, entry = future.result()
                mitigation_plan[sid] = entry
            except Exception as exc:
                mitigation_plan[sid] = {
                    "action": "escalate",
                    "rationale": f"Processing error: {exc}",
                    "confidence": 0.0,
                    "cost_delta": 0,
                    "severity": "LOW",
                    "decided_by": "error",
                }

    return {"mitigation_plan": mitigation_plan}


# ---------------------------------------------------------------------------
# Fast-path node — rule-based only, no LLM (used for all-LOW-severity cases)
# ---------------------------------------------------------------------------

def run_rule_mitigation(state: dict) -> dict:
    """Rule-based mitigation for cases where every at-risk shipment is LOW severity.

    Called by the graph router instead of run_mitigation when the impact analysis
    confirms nothing is critical enough to warrant an LLM call.  Produces the same
    mitigation_plan shape so run_action works unchanged downstream.
    """
    at_risk: list[dict] = state["at_risk_shipments"]
    if not at_risk:
        return {"mitigation_plan": {}}

    impact_scores: dict[str, dict] = state["impact_scores"]
    mitigation_plan: dict[str, dict] = {}

    for risk in at_risk:
        sid = risk["shipment_id"]
        impact = impact_scores.get(sid, _EMPTY_IMPACT)
        action, rationale, confidence = _rule_decide(impact, risk)
        mitigation_plan[sid] = {
            "action": action,
            "rationale": rationale,
            "confidence": round(float(confidence), 2),
            "cost_delta": _ACTION_COSTS.get(str(action), 0),
            "severity": impact.get("severity"),
            "decided_by": "rules_fast_path",
        }

    return {"mitigation_plan": mitigation_plan}
