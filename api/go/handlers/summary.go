package handlers

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

func GetSummary(store *Store) gin.HandlerFunc {
	return func(c *gin.Context) {
		result := store.Get()
		if result == nil {
			c.JSON(http.StatusNotFound, gin.H{"error": "No pipeline run yet. POST /run first."})
			return
		}

		severityBreakdown := map[string]int{"HIGH": 0, "MEDIUM": 0, "LOW": 0}
		for _, entry := range result.MitigationPlan {
			if m, ok := entry.(map[string]interface{}); ok {
				if sev, ok := m["severity"].(string); ok {
					severityBreakdown[sev]++
				}
			}
		}

		humanApproved := 0
		for _, l := range result.ActionLog {
			if strings.HasPrefix(l, "[HUMAN_APPROVED]") {
				humanApproved++
			}
		}

		c.JSON(http.StatusOK, gin.H{
			"at_risk_shipments":  len(result.AtRiskShipments),
			"autonomous_actions": result.AutonomousActions,
			"escalations_pending": result.Escalations,
			"human_approved":     humanApproved,
			"escalation_required": result.EscalationRequired,
			"severity_breakdown": severityBreakdown,
		})
	}
}
