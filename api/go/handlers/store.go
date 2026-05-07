package handlers

import "sync"

// PipelineResult mirrors the JSON response from POST /run on the Python service.
type PipelineResult struct {
	AtRiskCount       int                       `json:"at_risk_count"`
	AutonomousActions int                       `json:"autonomous_actions"`
	Escalations       int                       `json:"escalations"`
	EscalationRequired bool                     `json:"escalation_required"`
	AtRiskShipments   []map[string]interface{}  `json:"at_risk_shipments"`
	ImpactScores      map[string]interface{}    `json:"impact_scores"`
	MitigationPlan    map[string]interface{}    `json:"mitigation_plan"`
	ActionLog         []string                  `json:"action_log"`
}

// Store is a thread-safe in-memory cache of the last pipeline result.
type Store struct {
	mu     sync.RWMutex
	result *PipelineResult
}

func NewStore() *Store { return &Store{} }

func (s *Store) Set(r *PipelineResult) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.result = r
}

func (s *Store) Get() *PipelineResult {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.result
}
