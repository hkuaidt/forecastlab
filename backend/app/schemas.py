"""The shared, versioned contract for API, agents and saved runs."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 2026-09-26T08:00:00Z")
    return value


class QuestionSpec(BaseModel):
    id: str = Field(default_factory=lambda: f"Q-{uuid4().hex[:8]}")
    question: str = Field(min_length=8, max_length=1000)
    as_of: datetime = Field(default_factory=utcnow)
    resolve_by: datetime | None = None
    outcomes: list[str] = Field(default_factory=lambda: ["是", "否"])
    resolution_rule: str = ""
    resolution_source: str | None = None
    mode: Literal["binary", "scenario"] = "binary"
    user_assumptions: list[str] = Field(default_factory=list)

    _aware_dates = field_validator("as_of", "resolve_by")(aware)

    @model_validator(mode="after")
    def check_resolution(self):
        if self.mode == "binary":
            if not self.resolve_by or not self.resolution_rule.strip():
                raise ValueError("二元预测需要截止时间和可核对的结算规则")
            if self.resolve_by <= self.as_of:
                raise ValueError("结算时间必须晚于信息截至时间")
            if len(self.outcomes) != 2 or len(set(self.outcomes)) != 2:
                raise ValueError("二元预测需要两个不同的结果选项")
        return self


class QuestionDraft(BaseModel):
    question: str = Field(min_length=8, max_length=1000)
    as_of: datetime = Field(default_factory=utcnow)
    resolve_by: datetime | None = None
    resolution_rule: str = ""
    resolution_source: str | None = None
    mode: Literal["binary", "scenario"] = "binary"
    user_assumptions: list[str] = Field(default_factory=list)

    _aware_dates = field_validator("as_of", "resolve_by")(aware)


class StrictModel(BaseModel):
    """New client/candidate contracts reject unrecognised authority fields."""
    model_config = ConfigDict(extra="forbid")


class QuestionInput(StrictModel):
    input_id: str
    kind: Literal["original", "revision", "answer"]
    text: str = Field(min_length=1, max_length=4000)


class ClarificationAnswer(StrictModel):
    clarification_id: str
    answer: str = Field(min_length=1, max_length=2000)


class ClarificationCandidate(StrictModel):
    field: str = Field(max_length=100)
    question: str = Field(min_length=1, max_length=1000)
    blocking: bool = True


class QuestionClarification(ClarificationCandidate):
    id: str
    answer: str | None = None
    status: Literal["open", "resolved"] = "open"


class PremiseCandidate(StrictModel):
    content: str = Field(min_length=1, max_length=1000)
    origin: Literal["user_explicit", "model_inferred"]
    source_input_id: str
    original_span: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(default="", max_length=1000)
    replaces_id: str | None = None


class QuestionPremise(PremiseCandidate):
    id: str = Field(pattern=r"^P[0-9]{3,}$")
    user_review: Literal["pending", "retained", "rejected"] = "pending"
    treatment: Literal["to_verify", "scenario_condition"] = "to_verify"


class RetrievalTaskCandidate(StrictModel):
    query: str = Field(min_length=1, max_length=400)
    purpose: Literal["initial", "challenge", "alternative", "background"]
    premise_indexes: list[int] = Field(default_factory=list, max_length=12)


class RetrievalTask(StrictModel):
    id: str
    query: str = Field(min_length=1, max_length=400)
    purpose: Literal["initial", "challenge", "alternative", "background"]
    target_premise_ids: list[str] = Field(default_factory=list)


class FramingCandidate(StrictModel):
    proposed_spec: QuestionDraft
    clarifications: list[ClarificationCandidate] = Field(default_factory=list, max_length=10)
    premises: list[PremiseCandidate] = Field(default_factory=list, max_length=12)
    alternative_directions: list[str] = Field(default_factory=list, max_length=6)
    retrieval_plan: list[RetrievalTaskCandidate] = Field(default_factory=list, max_length=3)


class AnalysisRecord(StrictModel):
    model: str = "unknown"
    prompt_version: str = "question-evidence-v1"
    input_hash: str = ""
    request_ids: list[str] = Field(default_factory=list)
    elapsed_seconds: float = Field(default=0, ge=0)
    validation_mode: Literal["live", "fixture"] = "live"


class QuestionFraming(StrictModel):
    schema_version: Literal[1] = 1
    draft_id: str
    revision: int = Field(ge=1)
    raw_question: str = Field(min_length=8, max_length=1000)
    proposed_spec: QuestionDraft
    inputs: list[QuestionInput] = Field(default_factory=list, max_length=60)
    clarifications: list[QuestionClarification] = Field(default_factory=list, max_length=10)
    premises: list[QuestionPremise] = Field(default_factory=list, max_length=12)
    alternative_directions: list[str] = Field(default_factory=list, max_length=6)
    retrieval_plan: list[RetrievalTask] = Field(default_factory=list, max_length=3)
    status: Literal["needs_clarification", "ready_for_confirmation"]
    analysis_record: AnalysisRecord = Field(default_factory=AnalysisRecord)
    next_premise_number: int = Field(default=1, ge=1)
    demo_case_id: str | None = None

    @model_validator(mode="after")
    def unique_ids(self):
        for items in (self.premises, self.retrieval_plan, self.clarifications):
            ids = [i.id for i in items]
            if len(ids) != len(set(ids)):
                raise ValueError("编号不得重复")
        return self


class AnalyzeQuestionRequest(StrictModel):
    question: QuestionDraft
    draft_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=10)
    operation_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=100)
    demo_case_id: str | None = None

    @model_validator(mode="after")
    def require_revision(self):
        if bool(self.draft_id) != (self.expected_revision is not None):
            raise ValueError("修订草稿需要 draft_id 和 expected_revision")
        if len({a.clarification_id for a in self.answers}) != len(self.answers):
            raise ValueError("澄清回答编号重复")
        return self


class PremiseDecision(StrictModel):
    premise_id: str
    user_review: Literal["retained", "rejected"]
    treatment: Literal["to_verify", "scenario_condition"] = "to_verify"


class ConfirmQuestionRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    decisions: list[PremiseDecision] = Field(default_factory=list, max_length=12)


class QuestionConfirmation(StrictModel):
    confirmation_id: str
    draft_id: str
    revision: int
    question: QuestionSpec
    framing: QuestionFraming
    demo_case_id: str | None = None
    confirmed_at: datetime = Field(default_factory=utcnow)
    decision_hash: str


class DraftView(StrictModel):
    framing: QuestionFraming
    confirmation: QuestionConfirmation | None = None
    preparation_records: list["ModelCallRecord"] = Field(default_factory=list)


class ModelCallRecord(StrictModel):
    request_id: str
    owner_id: str
    phase: Literal["preparation", "runtime"]
    attempt: int = Field(default=1, ge=1)
    status: Literal["reserved", "succeeded", "failed", "interrupted"] = "reserved"
    model: str = "unknown"
    prompt_version: str = "question-evidence-v1"
    input_hash: str
    started_at: datetime = Field(default_factory=utcnow)
    elapsed_seconds: float = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    usage_known: bool = False
    error_type: str | None = None


class SourceAlias(StrictModel):
    source_url: str
    publisher: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class SourceSnapshot(StrictModel):
    text: str
    snapshot_hash: str
    snapshot_path: str
    stored_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)
    content_truncated: bool = False


class EvidencePassage(StrictModel):
    paragraph_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str
    snapshot_hash: str


class CitationCandidate(StrictModel):
    evidence_id: str
    snapshot_hash: str
    paragraph_id: str
    quote: str = Field(min_length=1, max_length=2400)


class FindingCitation(CitationCandidate):
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class FindingCandidate(StrictModel):
    target_premise_ids: list[str] = Field(default_factory=list, max_length=12)
    claim: str = Field(min_length=1, max_length=1500)
    relation: Literal["supports", "challenges", "alternative", "background", "unclear"]
    citations: list[CitationCandidate] = Field(min_length=1, max_length=4)
    limitation: str = Field(default="", max_length=1500)


class EvidenceFinding(StrictModel):
    id: str
    target_premise_ids: list[str] = Field(default_factory=list)
    claim: str
    relation: Literal["supports", "challenges", "alternative", "background", "unclear"]
    citations: list[FindingCitation] = Field(min_length=1)
    limitation: str = ""


class RejectedFinding(StrictModel):
    candidate: dict[str, object]
    reason: str


class ConflictCandidate(StrictModel):
    issue: str = Field(min_length=1, max_length=1500)
    finding_indexes: list[int] = Field(min_length=2, max_length=8)
    scope_comparison: str = Field(default="", max_length=1500)
    status: Literal["resolved", "unresolved"] = "unresolved"
    explanation: str = Field(default="", max_length=1500)


class ConflictDetail(StrictModel):
    issue: str
    finding_ids: list[str]
    scope_comparison: str = ""
    status: Literal["resolved", "unresolved"] = "unresolved"
    explanation: str = ""


class GapDetail(StrictModel):
    missing: str = Field(min_length=1, max_length=1500)
    target_premise_ids: list[str] = Field(default_factory=list)
    attempted_query_ids: list[str] = Field(default_factory=list)
    cause: Literal["not_found", "snippet_only", "date_unknown", "retrieval_failed", "validation_failed", "after_cutoff", "historical_unverified"] = "not_found"
    topic: Literal["available_information", "future_outcome"] = "available_information"


class AssessmentCandidate(StrictModel):
    summary: str = Field(max_length=3000)
    findings: list[FindingCandidate] = Field(default_factory=list, max_length=16)
    conflicts: list[ConflictCandidate] = Field(default_factory=list, max_length=6)
    gaps: list[GapDetail] = Field(default_factory=list, max_length=8)


class RetrievalLog(StrictModel):
    task_id: str
    query: str
    purpose: str = "background"
    status: Literal["success", "empty", "failed"]
    result_count: int = 0
    elapsed_seconds: float = 0
    error: str | None = None


class Evidence(BaseModel):
    id: str
    source_url: HttpUrl | None = None
    file_id: str | None = None
    title: str
    publisher: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    retrieved_at: datetime
    event_at: datetime | None = None
    excerpt: str = Field(min_length=1, max_length=12000)
    claim: str = ""
    snapshot_path: str | None = None
    content_hash: str
    source_type: Literal["primary", "secondary", "snippet_only", "imported", "exercise"]
    source_group: str
    date_status: Literal["verified", "unknown", "synthetic"] = "unknown"
    conflict_group: str | None = None
    source_kind: Literal["primary", "secondary", "unknown"] = "unknown"
    content_kind: Literal["body", "snippet", "imported_excerpt"] = "imported_excerpt"
    source_kind_basis: str = ""
    snapshot_hash: str | None = None
    content_truncated: bool = False
    query_ids: list[str] = Field(default_factory=list)
    aliases: list[SourceAlias] = Field(default_factory=list)
    source_group_basis: str = ""
    possible_same_source: list[str] = Field(default_factory=list)
    date_basis: dict[str, str] = Field(default_factory=dict)
    availability: Literal["verified_before_cutoff", "live_near_cutoff", "unverified", "after_cutoff", "historical_exercise", "synthetic"] = "unverified"
    event_status: Literal["observed", "planned", "unknown"] = "unknown"
    passages: list[EvidencePassage] = Field(default_factory=list)

    _aware_dates = field_validator("published_at", "updated_at", "retrieved_at", "event_at")(aware)

    @model_validator(mode="after")
    def check_location(self):
        if not self.source_url and not self.file_id:
            raise ValueError("证据需要真实 URL 或文件 ID")
        return self


class ImportedEvidence(BaseModel):
    """Input shape; claimed provenance is not a server attestation."""
    body: str | None = Field(default=None, max_length=1_000_000)
    source_kind: Literal["primary", "secondary", "unknown"] = "unknown"
    source_kind_basis: str = ""
    source_group_basis: str = ""
    event_status: Literal["observed", "planned", "unknown"] = "unknown"
    id: str | None = None
    source_url: HttpUrl | None = None
    file_id: str | None = None
    title: str = Field(min_length=1)
    publisher: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    retrieved_at: datetime | None = None
    event_at: datetime | None = None
    excerpt: str = Field(min_length=1, max_length=12000)
    claim: str = ""
    snapshot_path: str | None = None
    source_type: Literal["imported", "exercise"] = "imported"
    source_group: str | None = None
    date_status: Literal["verified", "unknown", "synthetic"] = "unknown"

    _aware_dates = field_validator("published_at", "updated_at", "retrieved_at", "event_at")(aware)

    @model_validator(mode="after")
    def check_location(self):
        if not self.source_url and not self.file_id:
            raise ValueError("导入证据需要 source_url 或 file_id")
        return self


class RetrievalResult(StrictModel):
    evidence: list[Evidence] = Field(default_factory=list)
    retrieval_log: list[RetrievalLog] = Field(default_factory=list)
    exclusions: list[dict[str, str]] = Field(default_factory=list)
    status: Literal["completed", "partial", "failed"] = "completed"


class QuestionAnalysis(BaseModel):
    normalized_question: str
    search_queries: list[str] = Field(min_length=1, max_length=3)
    caveats: list[str] = Field(default_factory=list)


class EvidenceQualityProfile(StrictModel):
    """Descriptive, server-derived evidence coverage; not a trust/independence certificate."""
    source_count: int = Field(default=0, ge=0)
    source_group_count: int = Field(default=0, ge=0)
    body_source_count: int = Field(default=0, ge=0)
    snippet_only_count: int = Field(default=0, ge=0)
    primary_label_count: int = Field(default=0, ge=0)
    unknown_publication_count: int = Field(default=0, ge=0)
    truncated_count: int = Field(default=0, ge=0)
    suspected_same_source_count: int = Field(default=0, ge=0)
    merged_alias_count: int = Field(default=0, ge=0)
    search_success_count: int = Field(default=0, ge=0)
    search_empty_count: int = Field(default=0, ge=0)
    search_failure_count: int = Field(default=0, ge=0)
    excluded_count: int = Field(default=0, ge=0)
    validated_finding_count: int = Field(default=0, ge=0)
    rejected_finding_count: int = Field(default=0, ge=0)
    unresolved_conflict_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class EvidenceAssessment(BaseModel):
    summary: str
    quality_profile: EvidenceQualityProfile | None = None
    # Server-controlled trust bit. Legacy/model output is forced to False; only
    # assess_evidence() may set it True after exact snapshot/passage validation.
    findings_validated: bool = False
    findings: list[EvidenceFinding] = Field(default_factory=list)
    conflict_details: list[ConflictDetail] = Field(default_factory=list)
    gap_details: list[GapDetail] = Field(default_factory=list)
    retrieval_log: list[RetrievalLog] = Field(default_factory=list)
    rejected_findings: list[RejectedFinding] = Field(default_factory=list)
    exclusions: list[dict[str, str]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Assumption(BaseModel):
    id: str
    created_by: Literal["user", "model"]
    parent_ids: list[str] = Field(default_factory=list)
    content: str
    rationale: str = ""


class ActorProfile(BaseModel):
    id: str
    name: str
    goal: str
    resources: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    visible_evidence_ids: list[str] = Field(default_factory=list)


class WorldState(BaseModel):
    state_version: int = 0
    summary: str
    variables: dict[str, str] = Field(default_factory=dict)
    relations: list[str] = Field(default_factory=list)
    actors: list[ActorProfile] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    simulation_branch_reason: str | None = None


class ActorAction(BaseModel):
    id: str
    created_by: str
    parent_ids: list[str] = Field(default_factory=list)
    actor_id: str
    round: int = Field(ge=1, le=2)
    parent_state: int
    action: str
    rationale_summary: str
    expected_impact: str
    evidence_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    kind: Literal["simulation"] = "simulation"


class SimulationStep(BaseModel):
    id: str
    created_by: str = "environment"
    parent_ids: list[str] = Field(default_factory=list)
    round: int = Field(ge=1, le=2)
    parent_state: int
    next_state: int
    summary: str
    state_changes: dict[str, str] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    kind: Literal["simulation"] = "simulation"


class ReviewIssue(BaseModel):
    severity: Literal["low", "medium", "high"]
    claim: str
    explanation: str
    affected_ids: list[str] = Field(default_factory=list)


class Review(BaseModel):
    status: Literal["passed", "qualified", "blocked"]
    probability_basis: Literal["full", "evidence_only", "none"] = "full"
    issues: list[ReviewIssue] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_audit_model_can_estimate: bool | None = None
    evidence_audit_can_estimate: bool | None = None
    evidence_audit_blocking_reasons: list[str] = Field(default_factory=list)
    evidence_audit_discarded_reasons: list[str] = Field(default_factory=list)


class EvidenceOnlyAudit(BaseModel):
    can_estimate: bool
    blocking_reasons: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    simulation_ids: list[str] = Field(default_factory=list)


class Forecast(BaseModel):
    status: Literal["completed", "insufficient_evidence", "scenario_only", "partial"]
    probability_basis: Literal["full", "evidence_only"] = "full"
    conclusion: str
    probabilities: dict[str, float] | None = None
    calibrated: Literal[False] = False
    supporting: list[Claim] = Field(default_factory=list)
    opposing: list[Claim] = Field(default_factory=list)
    key_assumptions: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    new_information: list[str] = Field(default_factory=list)


class EvidenceOnlyForecast(Forecast):
    """An approved evidence-only binary forecast cannot silently omit probability."""
    status: Literal["completed"] = "completed"
    probability_basis: Literal["evidence_only"] = "evidence_only"
    probabilities: dict[str, float]


class SettlementRequest(BaseModel):
    """A manually verified outcome, recorded only after the resolution deadline."""
    outcome: str = Field(min_length=1)
    source_url: HttpUrl
    source_title: str = Field(default="", max_length=300)
    observed_value: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)


class Settlement(SettlementRequest):
    recorded_at: datetime = Field(default_factory=utcnow)
    forecast_probabilities: dict[str, float] | None = None
    brier_score: float | None = None

    _aware_recorded_at = field_validator("recorded_at")(aware)


class RunRequest(StrictModel):
    confirmation_id: str | None = None
    question: QuestionSpec | None = None
    evidence_mode: Literal["import", "online", "reuse", "demo"] = "import"
    evidence: list[ImportedEvidence] = Field(default_factory=list)
    parent_run_id: str | None = None


    @model_validator(mode="after")
    def one_question_source(self):
        if (self.question is None) == (self.confirmation_id is None):
            raise ValueError("question 与 confirmation_id 必须且只能提供一个")
        return self


class RunRecord(BaseModel):
    retrieval_result: RetrievalResult | None = None
    retrieval_started: bool = False
    question_framing: QuestionFraming | None = None
    confirmation_id: str | None = None
    question_origin: Literal["confirmed", "legacy_direct"] = "legacy_direct"
    preparation_records: list[ModelCallRecord] = Field(default_factory=list)
    model_calls: list[ModelCallRecord] = Field(default_factory=list)
    active_seconds: float = 0
    premise_assumption_map: dict[str, str] = Field(default_factory=dict)
    run_id: str
    question_version: int = 1
    parent_run_id: str | None = None
    question: QuestionSpec
    evidence_mode: str
    demo: bool = False
    status: Literal["queued", "running", "completed", "insufficient_evidence", "scenario_only", "partial", "failed", "interrupted"] = "queued"
    stage: str = "queued"
    failed_stage: str | None = None
    stage_durations: dict[str, float] = Field(default_factory=dict)
    stage_outputs: dict[str, object] = Field(default_factory=dict)
    question_analysis: QuestionAnalysis | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_assessment: EvidenceAssessment | None = None
    world: WorldState | None = None
    actions: list[ActorAction] = Field(default_factory=list)
    simulation: list[SimulationStep] = Field(default_factory=list)
    review: Review | None = None
    forecast: Forecast | None = None
    # Opt-in experiment record; never substitutes for the scored forecast.
    shadow_forecast: Forecast | None = None
    settlement: Settlement | None = None
    model: str
    prompt_version: str = "v2"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    usage: dict[str, int] = Field(default_factory=lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    errors: list[str] = Field(default_factory=list)
    retry_history: list[str] = Field(default_factory=list)
    resume_count: int = 0
