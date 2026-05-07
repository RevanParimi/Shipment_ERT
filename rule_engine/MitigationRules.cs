// MitigationRules.cs
// Deterministic action-selection rules for supply chain mitigation.
// Replaces _rule_decide() in agents/mitigation.py and
// run_rule_mitigation() for the all-LOW fast path in the LangGraph graph.

namespace SupplyChain.Rules;

public record MitigationContext(
    string ShipmentId,
    string Severity,      // "HIGH" | "MEDIUM" | "LOW"
    string RiskType,      // pipe-separated signal list e.g. "delayed|gps_stuck"
    double Confidence,    // 0.0–1.0 from risk detection
    string ImpactType     // "stockout_risk" | "so_breach_risk" | "delivery_delay"
);

public record MitigationResult(
    string Action,        // hold | notify_customer | reroute | expedite | mode_switch | escalate
    string Rationale,     // one-sentence explanation
    double Confidence,    // pass-through from risk signal
    int    CostDeltaUsd,  // estimated execution cost
    string DecidedBy      // always "rules" for this engine
);

public static class MitigationRules
{
    private static readonly Dictionary<string, int> ActionCosts = new()
    {
        ["hold"]             = 0,
        ["notify_customer"]  = 50,
        ["reroute"]          = 1_200,
        ["expedite"]         = 2_500,
        ["mode_switch"]      = 4_000,
        ["escalate"]         = 0,
    };

    /// <summary>
    /// Select the best corrective action using deterministic rules.
    /// Mirrors Python agents/mitigation.py _rule_decide().
    /// </summary>
    public static MitigationResult Decide(MitigationContext ctx)
    {
        string action;
        string rationale;

        switch (ctx.Severity)
        {
            case "HIGH":
                bool isPhysicalBlock = ctx.RiskType.Contains("customs_hold")
                                    || ctx.RiskType.Contains("gps_stuck");
                if (isPhysicalBlock)
                {
                    action   = "mode_switch";
                    rationale = "High-severity physical blockage; switch to faster transport mode.";
                }
                else
                {
                    action   = "expedite";
                    rationale = "High-severity delay; expedite shipment to meet delivery commitment.";
                }
                break;

            case "MEDIUM":
                action   = "reroute";
                rationale = "Medium-severity delay; reroute via alternative lane to recover time.";
                break;

            default: // LOW
                action   = "notify_customer";
                rationale = "Low-severity delay; proactively notify customer of expected late arrival.";
                break;
        }

        return new MitigationResult(
            Action:      action,
            Rationale:   rationale,
            Confidence:  Math.Max(ctx.Confidence, 0.70),
            CostDeltaUsd: ActionCosts.GetValueOrDefault(action, 0),
            DecidedBy:   "rules"
        );
    }
}
