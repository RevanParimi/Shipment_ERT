"""FastAPI application — exposes the 5-agent supply chain pipeline via REST."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from graph.pipeline import empty_state, pipeline

app = FastAPI(
    title="Supply Chain AI Agent System",
    description=(
        "Autonomous freight visibility and disruption management system. "
        "POST /run to trigger the full 5-agent LangGraph pipeline."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory result store — sufficient for single-process demo
_last_result: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_result() -> dict:
    if not _last_result:
        raise HTTPException(
            status_code=404,
            detail="No pipeline run yet. POST /run first.",
        )
    return _last_result


def _escalated_ids(action_log: list[str]) -> set[str]:
    ids = set()
    for log in action_log:
        if log.startswith("[ESCALATE]"):
            # format: "[ESCALATE] SHP-PO-XXXX | ..."
            ids.add(log.split(" | ")[0].replace("[ESCALATE] ", "").strip())
    return ids


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "supply-chain-ai"}


@app.post("/run")
def run_pipeline():
    """Trigger the full 5-agent pipeline. Returns the complete analysis result."""
    global _last_result
    result = pipeline.invoke(empty_state())
    _last_result = dict(result)
    logs = _last_result.get("action_log", [])
    return {
        "at_risk_count": len(_last_result.get("at_risk_shipments", [])),
        "autonomous_actions": sum(1 for l in logs if l.startswith("[AUTO]")),
        "escalations": sum(1 for l in logs if l.startswith("[ESCALATE]")),
        "escalation_required": _last_result.get("escalation_required", False),
        "at_risk_shipments": _last_result.get("at_risk_shipments", []),
        "impact_scores": _last_result.get("impact_scores", {}),
        "mitigation_plan": _last_result.get("mitigation_plan", {}),
        "action_log": logs,
    }


@app.get("/shipments/at-risk")
def get_at_risk():
    """Return at-risk shipments detected in the last pipeline run."""
    result = _require_result()
    return result.get("at_risk_shipments", [])


@app.get("/escalations")
def get_escalations():
    """Return shipments that require human approval before action is taken."""
    result = _require_result()
    plan: dict[str, dict] = result.get("mitigation_plan", {})
    logs: list[str] = result.get("action_log", [])
    ids = _escalated_ids(logs)
    return {sid: plan[sid] for sid in ids if sid in plan}


@app.post("/escalations/{shipment_id}/approve")
def approve_escalation(shipment_id: str):
    """Human operator approves a flagged mitigation action, executing it immediately."""
    result = _require_result()
    plan: dict[str, dict] = result.get("mitigation_plan", {})
    if shipment_id not in plan:
        raise HTTPException(
            status_code=404,
            detail=f"Shipment '{shipment_id}' not found in the mitigation plan.",
        )
    entry = plan[shipment_id]
    log_entry = (
        f"[HUMAN_APPROVED] {shipment_id} | action={entry['action']} | "
        f"approved and executed by human operator | {entry.get('rationale', '')}"
    )
    _last_result.setdefault("action_log", []).append(log_entry)
    return {
        "message": f"Action '{entry['action']}' for {shipment_id} approved and executed.",
        "log": log_entry,
    }


@app.get("/summary")
def get_summary():
    """High-level dashboard summary of the last pipeline run."""
    result = _require_result()
    logs: list[str] = result.get("action_log", [])
    plan: dict[str, dict] = result.get("mitigation_plan", {})

    severity_counts: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for entry in plan.values():
        sev = entry.get("severity", "LOW")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    return {
        "at_risk_shipments": len(result.get("at_risk_shipments", [])),
        "autonomous_actions": sum(1 for l in logs if l.startswith("[AUTO]")),
        "escalations_pending": sum(1 for l in logs if l.startswith("[ESCALATE]")),
        "human_approved": sum(1 for l in logs if l.startswith("[HUMAN_APPROVED]")),
        "escalation_required": result.get("escalation_required", False),
        "severity_breakdown": severity_counts,
    }
