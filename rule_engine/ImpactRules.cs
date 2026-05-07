// ImpactRules.cs
// Deterministic business rules for supply chain impact severity assessment.
// Replaces the _formula_impact() fallback in agents/impact_analysis.py.
// Called when GROQ_API_KEY is absent or LLM call fails.

namespace SupplyChain.Rules;

public record ImpactContext(
    string  ShipmentId,
    string  Priority,          // "HIGH" | "MEDIUM" | "LOW"
    double  BufferDays,        // (current_stock - safety_stock) / daily_consumption
    int     DaysSoBreach,      // days until sales-order commitment date
    string  RiskType           // e.g. "delayed|gps_stuck"
);

public record ImpactResult(
    string Severity,            // "HIGH" | "MEDIUM" | "LOW"
    string ImpactType,          // "stockout_risk" | "so_breach_risk" | "delivery_delay"
    int    UrgencyScore,        // 1–10
    string KeyConcern,          // one-sentence summary
    int    RecommendedDaysToAct
);

public static class ImpactRules
{
    /// <summary>
    /// Evaluate business impact for one at-risk shipment.
    /// Mirrors Python agents/impact_analysis.py _formula_impact().
    /// </summary>
    public static ImpactResult Evaluate(ImpactContext ctx)
    {
        string severity;
        int    urgency;

        if (ctx.Priority == "HIGH" || ctx.BufferDays < 5 || ctx.DaysSoBreach < 3)
        {
            severity = "HIGH";
            urgency  = 9;
        }
        else if (ctx.Priority == "MEDIUM" || ctx.BufferDays < 15 || ctx.DaysSoBreach < 10)
        {
            severity = "MEDIUM";
            urgency  = 5;
        }
        else
        {
            severity = "LOW";
            urgency  = 2;
        }

        string impactType = ctx.BufferDays < 15 ? "stockout_risk"
                          : ctx.DaysSoBreach < 10 ? "so_breach_risk"
                          : "delivery_delay";

        int actInDays = ctx.DaysSoBreach < 999
            ? Math.Max(1, Math.Min((int)ctx.BufferDays, ctx.DaysSoBreach))
            : Math.Max(1, (int)ctx.BufferDays);

        string concern = $"{severity} priority: {ctx.BufferDays:F1}d buffer, SO breach in {ctx.DaysSoBreach}d (rule engine).";

        return new ImpactResult(severity, impactType, urgency, concern, actInDays);
    }
}
