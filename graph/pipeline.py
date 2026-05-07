"""LangGraph pipeline — dynamic routing across 5 agent nodes.

Flow overview:

  ingest
    │
    ├─[no active shipments]──────────────────────────────────► END
    │
    ▼
  detect_risk
    │
    ├─[no at-risk shipments]────────────────────────────────► END
    │
    ▼
  analyze_impact
    │
    ├─[all severity == LOW]──► fast_mitigation (rules only) ─┐
    │                                                         │
    └─[any HIGH or MEDIUM]──► plan_mitigation (Groq LLM) ───┘
                                                              │
                                                              ▼
                                                       execute_actions
                                                              │
                                                              ▼
                                                             END

Routing decisions:
  _route_after_ingest  — skip everything when no shipment needs monitoring
  _route_after_risk    — skip impact/mitigation/action when nothing is at risk
  _route_after_impact  — skip LLM mitigation when all risks are low-severity

Node registry:
  ingest           → agents.ingestion.run_ingestion
  detect_risk      → agents.risk_detection.run_risk_detection
  analyze_impact   → agents.impact_analysis.run_impact_analysis
  plan_mitigation  → agents.mitigation.run_mitigation          (Groq LLM)
  fast_mitigation  → agents.mitigation.run_rule_mitigation     (rules only)
  execute_actions  → agents.action.run_action
"""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agents.action import run_action
from agents.impact_analysis import run_impact_analysis
from agents.ingestion import run_ingestion
from agents.mitigation import run_mitigation, run_rule_mitigation
from agents.risk_detection import run_risk_detection


class SupplyChainState(TypedDict):
    raw_erp: dict[str, Any]
    raw_transport: dict[str, Any]
    parsed_emails: list[dict]
    at_risk_shipments: list[dict]
    impact_scores: dict[str, dict]
    mitigation_plan: dict[str, dict]
    action_log: list[str]
    escalation_required: bool


def _empty_state() -> SupplyChainState:
    return {
        "raw_erp": {},
        "raw_transport": {},
        "parsed_emails": [],
        "at_risk_shipments": [],
        "impact_scores": {},
        "mitigation_plan": {},
        "action_log": [],
        "escalation_required": False,
    }


# ---------------------------------------------------------------------------
# Routing functions — each receives the full state and returns a string key
# ---------------------------------------------------------------------------

def _route_after_ingest(state: SupplyChainState) -> str:
    """Skip the entire pipeline if ingestion finds no active shipments.

    This happens when all shipments are 'delivered' — nothing to monitor.
    """
    has_active = bool(state["raw_transport"].get("shipments"))
    return "detect_risk" if has_active else "end"


def _route_after_risk(state: SupplyChainState) -> str:
    """Skip impact analysis, mitigation, and action if nothing is at risk.

    Risk detection may find that all active shipments are on-track — no signals
    fired, no emails flagged, GPS moving normally.  No point calling downstream
    LLM agents in that case.
    """
    return "analyze_impact" if state["at_risk_shipments"] else "end"


def _route_after_impact(state: SupplyChainState) -> str:
    """Route HIGH/MEDIUM severity to LLM mitigation; LOW-only to rule engine.

    If every at-risk shipment scored LOW severity, the business impact is
    minor enough that deterministic rules (notify_customer / hold) are
    sufficient — no Groq calls needed.  Any HIGH or MEDIUM case goes to the
    full LLM mitigation node for richer reasoning.
    """
    scores = state["impact_scores"]
    if not scores:
        return "end"
    all_low = all(v.get("severity") == "LOW" for v in scores.values())
    return "fast_mitigation" if all_low else "plan_mitigation"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_pipeline() -> object:
    graph: StateGraph = StateGraph(SupplyChainState)

    # Register nodes
    graph.add_node("ingest", run_ingestion)
    graph.add_node("detect_risk", run_risk_detection)
    graph.add_node("analyze_impact", run_impact_analysis)
    graph.add_node("plan_mitigation", run_mitigation)        # LLM path
    graph.add_node("fast_mitigation", run_rule_mitigation)   # rules path
    graph.add_node("execute_actions", run_action)

    # Entry point
    graph.set_entry_point("ingest")

    # Route 1 — after ingestion
    graph.add_conditional_edges(
        "ingest",
        _route_after_ingest,
        {"detect_risk": "detect_risk", "end": END},
    )

    # Route 2 — after risk detection
    graph.add_conditional_edges(
        "detect_risk",
        _route_after_risk,
        {"analyze_impact": "analyze_impact", "end": END},
    )

    # Route 3 — after impact analysis (LLM vs rule-based mitigation)
    graph.add_conditional_edges(
        "analyze_impact",
        _route_after_impact,
        {
            "plan_mitigation": "plan_mitigation",
            "fast_mitigation": "fast_mitigation",
            "end": END,
        },
    )

    # Both mitigation branches converge at execute_actions
    graph.add_edge("plan_mitigation", "execute_actions")
    graph.add_edge("fast_mitigation", "execute_actions")
    graph.add_edge("execute_actions", END)

    return graph.compile()


pipeline = _build_pipeline()
empty_state = _empty_state
