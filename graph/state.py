from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineState:
    # --- populated by Data Ingestion Agent ---
    raw_erp: dict[str, Any] = field(default_factory=dict)          # keyed DataFrames as records
    raw_transport: dict[str, Any] = field(default_factory=dict)
    parsed_emails: list[dict] = field(default_factory=list)

    # --- populated by Risk Detection Agent ---
    at_risk_shipments: list[dict] = field(default_factory=list)    # [{shipment_id, risk_type, confidence}]

    # --- populated by Impact Analysis Agent ---
    impact_scores: dict[str, dict] = field(default_factory=dict)   # {shipment_id: {severity, type, days_at_risk}}

    # --- populated by Mitigation Decision Agent ---
    mitigation_plan: dict[str, dict] = field(default_factory=dict) # {shipment_id: {action, confidence, cost_delta}}

    # --- populated by Action agents ---
    action_log: list[str] = field(default_factory=list)
    escalation_required: bool = False
