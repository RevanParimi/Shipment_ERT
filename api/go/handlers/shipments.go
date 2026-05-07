package handlers

import (
	"net/http"
	"github.com/gin-gonic/gin"
)

func GetAtRisk(store *Store) gin.HandlerFunc {
	return func(c *gin.Context) {
		result := store.Get()
		if result == nil {
			c.JSON(http.StatusNotFound, gin.H{"error": "No pipeline run yet. POST /run first."})
			return
		}
		c.JSON(http.StatusOK, result.AtRiskShipments)
	}
}
