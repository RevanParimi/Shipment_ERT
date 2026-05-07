package handlers

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
)

func GetEscalations(store *Store) gin.HandlerFunc {
	return func(c *gin.Context) {
		result := store.Get()
		if result == nil {
			c.JSON(http.StatusNotFound, gin.H{"error": "No pipeline run yet. POST /run first."})
			return
		}

		escalatedIDs := extractEscalatedIDs(result.ActionLog)
		out := make(map[string]interface{})
		for _, sid := range escalatedIDs {
			if entry, ok := result.MitigationPlan[sid]; ok {
				out[sid] = entry
			}
		}
		c.JSON(http.StatusOK, out)
	}
}

func ApproveEscalation(store *Store) gin.HandlerFunc {
	return func(c *gin.Context) {
		result := store.Get()
		if result == nil {
			c.JSON(http.StatusNotFound, gin.H{"error": "No pipeline run yet. POST /run first."})
			return
		}

		shipmentID := c.Param("shipment_id")
		entry, ok := result.MitigationPlan[shipmentID]
		if !ok {
			c.JSON(http.StatusNotFound, gin.H{"error": fmt.Sprintf("Shipment '%s' not found in mitigation plan.", shipmentID)})
			return
		}

		entryMap, _ := entry.(map[string]interface{})
		action, _ := entryMap["action"].(string)
		rationale, _ := entryMap["rationale"].(string)

		logEntry := fmt.Sprintf(
			"[HUMAN_APPROVED] %s | action=%s | approved and executed by human operator | %s",
			shipmentID, action, rationale,
		)
		result.ActionLog = append(result.ActionLog, logEntry)
		store.Set(result)

		c.JSON(http.StatusOK, gin.H{
			"message": fmt.Sprintf("Action '%s' for %s approved and executed.", action, shipmentID),
			"log":     logEntry,
		})
	}
}

func extractEscalatedIDs(logs []string) []string {
	var ids []string
	for _, l := range logs {
		if strings.HasPrefix(l, "[ESCALATE]") {
			parts := strings.SplitN(l, " | ", 2)
			id := strings.TrimPrefix(parts[0], "[ESCALATE] ")
			ids = append(ids, strings.TrimSpace(id))
		}
	}
	return ids
}
