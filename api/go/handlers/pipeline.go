package handlers

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
)

// RunPipeline forwards POST /run to the Python LangGraph service and caches the result.
func RunPipeline(store *Store, pythonURL string) gin.HandlerFunc {
	client := &http.Client{Timeout: 300 * time.Second} // pipeline can take time

	return func(c *gin.Context) {
		url := fmt.Sprintf("%s/run", pythonURL)

		resp, err := client.Post(url, "application/json", nil)
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{
				"error": fmt.Sprintf("Python service unreachable: %v", err),
			})
			return
		}
		defer resp.Body.Close()

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to read response"})
			return
		}

		if resp.StatusCode != http.StatusOK {
			c.Data(resp.StatusCode, "application/json", body)
			return
		}

		var result PipelineResult
		if err := json.Unmarshal(body, &result); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to parse pipeline result"})
			return
		}

		store.Set(&result)
		c.JSON(http.StatusOK, result)
	}
}
