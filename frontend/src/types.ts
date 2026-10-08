export type Question = { id: string; question: string; as_of: string; resolve_by: string | null; outcomes: string[]; resolution_rule: string; resolution_source: string | null; mode: 'binary' | 'scenario'; user_assumptions: string[] }
export type Evidence = { snapshot_hash?: string | null; source_kind?: string; content_kind?: string; source_kind_basis?: string; content_truncated?: boolean;
  query_ids?: string[]; source_group_basis?: string; possible_same_source?: string[]; date_basis?: Record<string, string>; availability?: string; event_status?: string;
  aliases?: { source_url: string; published_at: string | null; updated_at: string | null; metadata: Record<string, unknown> }[]; passages?: EvidencePassage[]; id: string; source_url: string | null; file_id: string | null; title: string; publisher: string | null; published_at: string | null; updated_at: string | null; retrieved_at: string; event_at: string | null; excerpt: string; claim: string; snapshot_path: string | null; content_hash: string; source_type: string; source_group: string; date_status: string; conflict_group: string | null }
export type Actor = { id: string; name: string; goal: string; resources: string[]; constraints: string[]; visible_evidence_ids: string[] }
export type Assumption = { id: string; created_by: string; parent_ids: string[]; content: string; rationale: string }
export type World = { state_version: number; summary: string; variables: Record<string, string>; relations: string[]; actors: Actor[]; evidence_refs: string[]; assumptions: Assumption[]; simulation_branch_reason: string | null }
export type Action = { id: string; actor_id: string; round: number; parent_state: number; action: string; rationale_summary: string; expected_impact: string; evidence_ids: string[]; assumption_ids: string[] }
export type Step = { id: string; round: number; parent_state: number; next_state: number; summary: string; state_changes: Record<string, string>; conflicts: string[]; unresolved: string[]; evidence_ids: string[]; assumption_ids: string[] }
export type Issue = { severity: string; claim: string; explanation: string; affected_ids: string[] }
export type Claim = { text: string; evidence_ids: string[]; assumption_ids: string[]; simulation_ids: string[] }
export type ProbabilityBasis = 'full' | 'evidence_only' | 'none'
export type Forecast = { status: string; conclusion: string; probabilities: Record<string, number> | null; probability_basis?: 'full' | 'evidence_only'; calibrated: false; supporting: Claim[]; opposing: Claim[]; key_assumptions: string[]; scenarios: string[]; limitations: string[]; new_information: string[] }
export type Settlement = { outcome: string; source_url: string; source_title: string; observed_value: string; note: string; recorded_at: string; forecast_probabilities: Record<string, number> | null; brier_score: number | null }
export type SettlementSummary = { settled_count: number; scored_count: number; average_brier: number | null; reference_brier: number }
export type Run = { active_seconds?: number; report_repair?: { available: boolean; reason: string | null; endpoint: string | null }; report_repair_parent?: string | null; forecast_attempts?: { validation_errors: string[]; recorded_at: string; candidate: Forecast }[]; question_framing?: QuestionFraming | null; confirmation_id?: string | null; question_origin?: "confirmed" | "legacy_direct"; preparation_records?: ModelCall[]; model_calls?: ModelCall[]; run_id: string; parent_run_id: string | null; question_version: number; question: Question; evidence_mode: string; demo: boolean; status: string; stage: string; failed_stage?: string | null; stage_durations?: Record<string, number>; stage_outputs: Record<string, unknown>; question_analysis: { normalized_question: string; search_queries: string[]; caveats: string[] } | null; evidence: Evidence[]; evidence_assessment: EvidenceAssessment | null; world: World | null; actions: Action[]; simulation: Step[]; review: { status: string; probability_basis?: ProbabilityBasis; issues: Issue[]; unsupported_claims: string[]; missing_evidence: string[]; evidence_audit_model_can_estimate?: boolean | null; evidence_audit_can_estimate?: boolean | null; evidence_audit_blocking_reasons?: string[]; evidence_audit_discarded_reasons?: string[] } | null; forecast: Forecast | null; settlement: Settlement | null; model: string; started_at: string; finished_at: string | null; usage: { calls: number; prompt_tokens: number; completion_tokens: number }; errors: string[] }
export type Health = { ok: boolean; model_configured: boolean; search_configured: boolean; model: string }

export type QuestionDraft = Omit<Question, 'id' | 'outcomes'>
export type Premise = { id: string; content: string; origin: 'user_explicit' | 'model_inferred'; source_input_id: string; original_span: string; rationale: string; replaces_id: string | null; user_review: 'pending' | 'retained' | 'rejected'; treatment: 'to_verify' | 'scenario_condition' }
export type QuestionFraming = { schema_version: 1; draft_id: string; revision: number; raw_question: string; proposed_spec: QuestionDraft; inputs: { input_id: string; kind: string; text: string }[]; clarifications: { id: string; field: string; question: string; blocking: boolean; status: 'open' | 'resolved'; answer: string | null }[]; premises: Premise[]; alternative_directions: string[]; retrieval_plan: { id: string; query: string; purpose: string; target_premise_ids: string[] }[]; status: 'needs_clarification' | 'ready_for_confirmation'; analysis_record: { model?: string; input_hash?: string; request_ids?: string[]; validation_mode: 'live' | 'fixture' }; demo_case_id: string | null }
export type PremiseDecision = { premise_id: string; user_review: 'retained' | 'rejected'; treatment: 'to_verify' | 'scenario_condition' }
export type QuestionConfirmation = { confirmation_id: string; framing: QuestionFraming; question: Question; revision: number }
export type QuestionDraftView = { framing: QuestionFraming; confirmation: QuestionConfirmation | null }
export type ClarificationAnswer = { clarification_id: string; answer: string }
export type ModelCall = { request_id: string; status: string; usage_known: boolean; prompt_tokens: number; completion_tokens: number; elapsed_seconds: number }

export type EvidencePassage = { paragraph_id: string; start: number; end: number; text: string; snapshot_hash: string }
export type FindingCitation = { evidence_id: string; snapshot_hash: string; paragraph_id: string; quote: string; start: number; end: number }
export type EvidenceFinding = { id: string; target_premise_ids: string[]; claim: string; relation: string; citations: FindingCitation[]; limitation: string }
export type EvidenceQualityProfile = {
  source_count: number; source_group_count: number; body_source_count: number; snippet_only_count: number;
  primary_label_count: number; unknown_publication_count: number; truncated_count: number;
  suspected_same_source_count: number; merged_alias_count: number;
  search_success_count: number; search_empty_count: number; search_failure_count: number;
  excluded_count: number; validated_finding_count: number; rejected_finding_count: number;
  unresolved_conflict_count: number; warnings: string[];
}
export type EvidenceAssessment = { summary: string; quality_profile?: EvidenceQualityProfile | null; findings_validated?: boolean; evidence_ids: string[]; conflicts: string[]; gaps: string[]; findings?: EvidenceFinding[];
  conflict_details?: { issue: string; finding_ids: string[]; scope_comparison: string; status: string; explanation: string }[];
  gap_details?: { missing: string; cause: string; target_premise_ids: string[]; attempted_query_ids: string[] }[];
  retrieval_log?: { task_id: string; query: string; purpose: string; status: string; result_count: number; error: string | null }[];
  rejected_findings?: { candidate: Record<string, unknown>; reason: string }[]; exclusions?: { source: string; reason: string }[] }
export type PassageResponse = { evidence_id: string; text: string; snapshot_hash: string; content_truncated: boolean; passages: EvidencePassage[] }
