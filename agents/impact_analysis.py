"""Impact Analysis Agent.

Design rationale — why LLM here, not just formulas:

  Numerical calculations (buffer_days, days_so_breach) are kept as Python:
  they are unambiguous arithmetic and must feed the LLM as grounded facts.

  Severity assignment is replaced with Groq LLM reasoning because a formula
  cannot weigh tradeoffs a logistics expert would:
    - ENG-3201 (engine component) with 3d buffer is CRITICAL even at MEDIUM priority
    - SUSP-4402 on a LOW-priority ocean shipment with 12d buffer may just need a note
    - A carrier stuck at a port during peak season implies longer tails than GPS shows
    - The email_analysis summary from Agent 2 adds unstructured context the formula ignores

  Formula fallback activates when GROQ_API_KEY is absent or the LLM call fails.

Parallelism: one LLM call per at-risk shipment, submitted concurrently via
ThreadPoolExecutor(max_workers=4).  Calls are independent and I/O-bound.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from agents.utils import GROQ_AVAILABLE, GROQ_MODEL, SIM_NOW, call_with_retry, groq_client

_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(risk: dict, shp: dict, po: dict, inv: dict,
                   so_commitment: "datetime | None") -> dict:
    """Collect all measurable facts for one at-risk shipment."""
    current = float(inv.get("current_stock", 0))
    safety = float(inv.get("safety_stock", 0))
    daily = float(inv.get("daily_consumption", 1)) or 1.0
    buffer_days = max(current - safety, 0.0) / daily

    days_so_breach: "int | str" = (
        (so_commitment - SIM_NOW).days if so_commitment else "N/A"
    )

    return {
        "shipment_id": shp.get("shipment_id", ""),
        "mode": shp.get("mode", "unknown"),
        "carrier": shp.get("carrier", "unknown"),
        "status": shp.get("status", "unknown"),
        "risk_type": risk.get("risk_type", ""),
        "email_analysis": risk.get("email_analysis", ""),   # Agent 2 LLM summary
        "material": po.get("material", "unknown"),
        "priority": po.get("priority", "LOW"),
        "required_delivery_date": po.get("required_delivery_date", "unknown"),
        "dest_plant": shp.get("dest", "unknown"),
        "buffer_days": round(buffer_days, 1),
        "daily_consumption": int(daily),
        "days_so_breach": days_so_breach,
        # Keep raw values for formula fallback
        "_buffer_days_raw": buffer_days,
        "_days_so_breach_raw": (so_commitment - SIM_NOW).days if so_commitment else 999,
    }


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

def _assess_impact_llm(ctx: dict) -> dict:
    """Groq call — holistic business impact reasoning for one shipment."""
    so_str = (
        f"{ctx['days_so_breach']} days"
        if isinstance(ctx["days_so_breach"], int)
        else "not linked to a sales order"
    )
    email_line = (
        f"Email intelligence: {ctx['email_analysis']}"
        if ctx["email_analysis"]
        else "No email context available."
    )

    prompt = (
        "You are a senior logistics analyst. Assess the business impact of this supply chain disruption.\n\n"
        f"Shipment: {ctx['shipment_id']}\n"
        f"  Transport mode: {ctx['mode']} | Carrier: {ctx['carrier']}\n"
        f"  Current status: {ctx['status']} | Risk signals: {ctx['risk_type']}\n"
        f"  {email_line}\n\n"
        f"Material: {ctx['material']}\n"
        f"  PO priority: {ctx['priority']} | Required delivery: {ctx['required_delivery_date']}\n\n"
        f"Inventory at {ctx['dest_plant']}:\n"
        f"  Buffer above safety stock: {ctx['buffer_days']:.1f} days\n"
        f"  Daily consumption: {ctx['daily_consumption']} units/day\n\n"
        f"Customer commitment breach in: {so_str}\n\n"
        "Consider material criticality, transport mode risk profile, carrier reliability, "
        "buffer adequacy, and customer SLA when making your assessment.\n"
        "Return ONLY valid JSON — no other text:\n"
        '{"severity": "<HIGH|MEDIUM|LOW>", '
        '"impact_type": "<stockout_risk|so_breach_risk|production_stoppage|delivery_delay>", '
        '"urgency_score": <integer 1-10>, '
        '"key_concern": "<one concise sentence>", '
        '"recommended_days_to_act": <integer>}'
    )

    resp = call_with_retry(lambda: groq_client.chat.completions.create(  # type: ignore[union-attr]
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=150,
    ))
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


# ---------------------------------------------------------------------------
# Formula fallback (no LLM)
# ---------------------------------------------------------------------------

def _formula_impact(ctx: dict) -> dict:
    buffer_days = ctx["_buffer_days_raw"]
    days_so_breach = ctx["_days_so_breach_raw"]
    priority = ctx["priority"]

    if priority == "HIGH" or buffer_days < 5 or days_so_breach < 3:
        severity = "HIGH"
        urgency = 9
    elif priority == "MEDIUM" or buffer_days < 15 or days_so_breach < 10:
        severity = "MEDIUM"
        urgency = 5
    else:
        severity = "LOW"
        urgency = 2

    impact_type = "stockout_risk" if buffer_days < 15 else (
        "so_breach_risk" if days_so_breach < 10 else "delivery_delay"
    )
    act_in = max(1, min(int(buffer_days), int(days_so_breach))) if days_so_breach < 999 else max(1, int(buffer_days))

    return {
        "severity": severity,
        "impact_type": impact_type,
        "urgency_score": urgency,
        "key_concern": f"{severity} priority: {buffer_days:.1f}d buffer, SO breach in {days_so_breach}d (formula fallback).",
        "recommended_days_to_act": act_in,
    }


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

def run_impact_analysis(state: dict) -> dict:
    at_risk: list[dict] = state["at_risk_shipments"]
    if not at_risk:
        return {"impact_scores": {}}

    # Build lookup maps
    po_map: dict[str, dict] = {p["po_id"]: p for p in state["raw_erp"]["purchase_orders"]}
    inv_map: dict[tuple, dict] = {
        (r["plant"], r["material"]): r
        for r in state["raw_erp"]["inventory"]
    }
    shp_map: dict[str, dict] = {
        s["shipment_id"]: s for s in state["raw_transport"]["shipments"]
    }

    # material → earliest SO delivery commitment date
    so_by_material: dict[str, datetime] = {}
    for so in state["raw_erp"]["sales_orders"]:
        try:
            dt = datetime.strptime(so["delivery_commitment_date"], _FMT)
        except (ValueError, KeyError):
            continue
        mat = so["material"]
        if mat not in so_by_material or dt < so_by_material[mat]:
            so_by_material[mat] = dt

    # Build context dicts for every at-risk shipment
    contexts: dict[str, dict] = {}
    for risk in at_risk:
        sid = risk["shipment_id"]
        shp = shp_map.get(sid, {})
        po = po_map.get(shp.get("po_id", ""), {})
        material = po.get("material", "")
        inv = inv_map.get((shp.get("dest", ""), material), {})
        so_commitment = so_by_material.get(material)
        contexts[sid] = _build_context(risk, shp, po, inv, so_commitment)

    # --- Parallel LLM impact assessment ---
    impact_scores: dict[str, dict] = {}

    if GROQ_AVAILABLE:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(_assess_impact_llm, ctx): sid
                for sid, ctx in contexts.items()
            }
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    raw = future.result()
                    impact_scores[sid] = {
                        "severity": raw.get("severity", "LOW"),
                        "type": raw.get("impact_type", "delivery_delay"),
                        "urgency_score": raw.get("urgency_score", 1),
                        "key_concern": raw.get("key_concern", ""),
                        "recommended_days_to_act": raw.get("recommended_days_to_act", 7),
                        "days_at_risk": contexts[sid]["_days_so_breach_raw"]
                            if contexts[sid]["_days_so_breach_raw"] < 999
                            else round(contexts[sid]["_buffer_days_raw"], 1),
                        "buffer_days": contexts[sid]["buffer_days"],
                        "priority": contexts[sid]["priority"],
                        "decided_by": "llm",
                    }
                except Exception:
                    # LLM failed → formula fallback for this shipment
                    fb = _formula_impact(contexts[sid])
                    impact_scores[sid] = {**fb,
                        "days_at_risk": contexts[sid]["_days_so_breach_raw"]
                            if contexts[sid]["_days_so_breach_raw"] < 999
                            else round(contexts[sid]["_buffer_days_raw"], 1),
                        "buffer_days": contexts[sid]["buffer_days"],
                        "priority": contexts[sid]["priority"],
                        "decided_by": "formula_fallback",
                    }
    else:
        # No API key: formula for all
        for sid, ctx in contexts.items():
            fb = _formula_impact(ctx)
            impact_scores[sid] = {**fb,
                "days_at_risk": ctx["_days_so_breach_raw"]
                    if ctx["_days_so_breach_raw"] < 999
                    else round(ctx["_buffer_days_raw"], 1),
                "buffer_days": ctx["buffer_days"],
                "priority": ctx["priority"],
                "decided_by": "formula_no_key",
            }

    return {"impact_scores": impact_scores}
