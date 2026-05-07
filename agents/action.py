"""Autonomous Action Agent + Human-in-the-Loop escalation.

Decision rule:
  - confidence >= CONFIDENCE_THRESHOLD  AND
  - cost_delta  <  AUTONOMOUS_COST_THRESHOLD  AND
  - action != "escalate"
  → execute autonomously (simulate: write to action_log)

  Otherwise → escalate to human, set escalation_required = True

Thresholds come from .env so they can be tuned without code changes:
  AUTONOMOUS_COST_THRESHOLD  (default 5000 USD)
  CONFIDENCE_THRESHOLD       (default 0.75)
"""

import os

from dotenv import load_dotenv

load_dotenv()

_COST_THRESHOLD = float(os.getenv("AUTONOMOUS_COST_THRESHOLD", "5000"))
_CONF_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))

_ACTION_DESCRIPTIONS: dict[str, str] = {
    "hold": "Shipment placed on hold pending further review.",
    "notify_customer": "Customer notification dispatched via automated email.",
    "reroute": "Alternative route calculated and order dispatched to carrier.",
    "expedite": "Expedite request sent to carrier; priority flag raised in TMS.",
    "mode_switch": "Mode-switch order issued; logistics team and carrier notified.",
    "escalate": "Flagged for human review — no autonomous action taken.",
}


def run_action(state: dict) -> dict:
    plan: dict[str, dict] = state.get("mitigation_plan", {})
    action_log: list[str] = []
    escalation_required = False

    for sid, entry in plan.items():
        action = entry.get("action", "hold")
        confidence = float(entry.get("confidence", 0.0))
        cost_delta = float(entry.get("cost_delta", 0))
        rationale = entry.get("rationale", "")
        severity = entry.get("severity", "LOW")

        auto_eligible = (
            action != "escalate"
            and confidence >= _CONF_THRESHOLD
            and cost_delta < _COST_THRESHOLD
        )

        if auto_eligible:
            detail = _ACTION_DESCRIPTIONS.get(action, "Action executed.")
            action_log.append(
                f"[AUTO] {sid} | action={action} | severity={severity} | "
                f"cost_delta=${cost_delta:,.0f} | conf={confidence:.2f} | "
                f"{detail} | {rationale}"
            )
        else:
            reason = _escalation_reason(action, confidence, cost_delta)
            action_log.append(
                f"[ESCALATE] {sid} | action={action} | severity={severity} | "
                f"{reason} | {rationale}"
            )
            escalation_required = True

    return {"action_log": action_log, "escalation_required": escalation_required}


def _escalation_reason(action: str, confidence: float, cost_delta: float) -> str:
    parts: list[str] = []
    if action == "escalate":
        parts.append("LLM recommended manual review")
    if confidence < _CONF_THRESHOLD:
        parts.append(f"low confidence ({confidence:.2f} < {_CONF_THRESHOLD})")
    if cost_delta >= _COST_THRESHOLD:
        parts.append(f"cost exceeds threshold (${cost_delta:,.0f} >= ${_COST_THRESHOLD:,.0f})")
    return " | ".join(parts) if parts else "manual review required"
