// Supply Chain AI — Go REST API
//
// Public API layer (port 8000). Forwards /run to the Python LangGraph service
// (PYTHON_SERVICE_URL, default http://localhost:8001) and serves all other
// endpoints from cached state.
//
// Run: go run main.go
// Build: go build -o supply-chain-api .

package main

import (
	"log"
	"os"

	"github.com/gin-gonic/gin"
	"supply-chain-ai/api/handlers"
)

func main() {
	pythonURL := os.Getenv("PYTHON_SERVICE_URL")
	if pythonURL == "" {
		pythonURL = "http://localhost:8001"
	}
	port := os.Getenv("GO_API_PORT")
	if port == "" {
		port = "8000"
	}

	store := handlers.NewStore()

	r := gin.Default()

	r.GET("/health",                       handlers.Health())
	r.POST("/run",                         handlers.RunPipeline(store, pythonURL))
	r.GET("/shipments/at-risk",            handlers.GetAtRisk(store))
	r.GET("/escalations",                  handlers.GetEscalations(store))
	r.POST("/escalations/:shipment_id/approve", handlers.ApproveEscalation(store))
	r.GET("/summary",                      handlers.GetSummary(store))

	log.Printf("[go-api] Listening on :%s  →  Python service: %s", port, pythonURL)
	if err := r.Run(":" + port); err != nil {
		log.Fatalf("[go-api] Failed to start: %v", err)
	}
}
