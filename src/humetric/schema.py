"""Pydantic v2 models — API request/response schemas.

populate_by_name=True + AliasChoices: Claude may return English field names,
API responses are in English.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from . import config


# ── Enums ─────────────────────────────────────────────────────

class TenantStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    suspended = "suspended"


class EntityStatus(str, Enum):
    active = "active"
    archived = "archived"


class ApiKeyScopes(str, Enum):
    signals_write = "signals:write"
    entities_read = "entities:read"
    entities_write = "entities:write"
    signals_read = "signals:read"
    query = "query"
    packs_admin = "packs:admin"
    packs_read = "packs:read"


class ConsentStatus(str, Enum):
    granted = "granted"
    revoked = "revoked"
    expired = "expired"


class ErrorCode(str, Enum):
    entity_not_found = "entity_not_found"
    invalid_api_key = "invalid_api_key"
    expired_api_key = "expired_api_key"
    consent_required = "consent_required"
    rate_limit_exceeded = "rate_limit_exceeded"
    validation_error = "validation_error"
    internal_error = "internal_error"
    tenant_not_found = "tenant_not_found"
    tenant_already_exists = "tenant_already_exists"
    api_key_not_found = "api_key_not_found"
    api_key_revoked = "api_key_revoked"
    api_key_expired = "api_key_expired"
    insufficient_scopes = "insufficient_scopes"
    entity_archived = "entity_archived"
    signal_not_found = "signal_not_found"
    signal_processing_failed = "signal_processing_failed"
    invalid_top_k = "invalid_top_k"
    pack_not_found = "pack_not_found"
    pack_already_exists = "pack_already_exists"
    entity_type_already_active = "entity_type_already_active"
    entity_type_locked = "entity_type_locked"
    invalid_yaml = "invalid_yaml"
    unknown_entity_type = "unknown_entity_type"
    missing_required_fields = "missing_required_fields"
    no_active_pack_for_type = "no_active_pack_for_type"
    ai_service_unavailable = "ai_service_unavailable"
    byo_key_unavailable = "byo_key_unavailable"
    tier_limit_exceeded = "tier_limit_exceeded"
    email_already_registered = "email_already_registered"
    email_not_verified = "email_not_verified"
    invalid_verification_token = "invalid_verification_token"
    captcha_failed = "captcha_failed"
    cannot_revoke_self = "cannot_revoke_self"
    cannot_revoke_last_key = "cannot_revoke_last_key"
    analyzer_session_not_found = "analyzer_session_not_found"
    analyzer_invalid_state = "analyzer_invalid_state"
    analyzer_refine_limit_exceeded = "analyzer_refine_limit_exceeded"
    analyzer_payment_required = "analyzer_payment_required"


# ── Tenant ─────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(..., max_length=64, description="Tenant short code")
    name: str = Field(..., max_length=255, description="Tenant display name")
    status: TenantStatus = TenantStatus.active
    embedding_provider: str = Field(default="voyage", max_length=64)
    llm_provider: str = Field(default="anthropic", max_length=64)
    monthly_signal_quota: int | None = None
    entity_quota: int | None = None


class TenantRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    code: str
    name: str
    status: str
    embedding_provider: str
    llm_provider: str
    monthly_signal_quota: int | None = None
    entity_quota: int | None = None
    created_at: datetime


# ── Entity ─────────────────────────────────────────────────────

class EntityCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(
        ...,
        max_length=128,
        pattern=r"^[a-zA-Z0-9\-_.]+$",
        description="Client-provided unique ID",
        validation_alias=AliasChoices("id", "Id"),
    )
    entity_type: str = Field(
        ...,
        max_length=64,
        validation_alias=AliasChoices("entity_type", "entityType"),
    )
    fields: dict = Field(default_factory=dict)
    free_text: str | None = Field(
        default=None,
        max_length=50000,
        validation_alias=AliasChoices("free_text", "freeText"),
    )
    status: str = "active"


class EntityMetricRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metric_key: str = Field(validation_alias=AliasChoices("metric_key", "key", "metricKey"))
    value: float
    confidence: float
    effective_confidence: float | None = Field(default=None, validation_alias=AliasChoices("effective_confidence", "effectiveConfidence"))
    source_count: int = Field(default=1, validation_alias=AliasChoices("source_count", "sourceCount"))
    last_updated: datetime | None = Field(default=None, validation_alias=AliasChoices("last_updated", "lastUpdated"))
    source_signal_id: str | None = Field(default=None, validation_alias=AliasChoices("source_signal_id", "sourceSignalId"))


class EntityRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    entity_type: str = Field(validation_alias=AliasChoices("entity_type", "entityType"))
    fields: dict
    free_text: str | None = None
    metrics: list[EntityMetricRead] = []
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EntityMetricsResponse(BaseModel):
    entity_id: str
    metrics: list[EntityMetricRead]
    metric_count: int


# ── Signal (Spec 022) ─────────────────────────────────────────

class SignalCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_id: str = Field(max_length=128)
    entity_type: str = Field(max_length=64)
    text: str | None = None
    structured: dict | None = None
    external_id: str | None = Field(default=None, max_length=200)


class SignalResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_id: str
    status: str
    metrics: list[EntityMetricRead] = Field(default_factory=list)
    trace_url: str | None = None


class SignalStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_id: str
    status: str
    entity_id: str
    error: str | None = None
    created_at: datetime | None = None
    processed_at: datetime | None = None


class SignalTrace(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_id: str
    entity_id: str
    trace_data: dict = Field(default_factory=dict)


# ── Query (Spec 022) ──────────────────────────────────────────

class QueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_type: str | None = None
    rank_by: str | None = Field(default=None, validation_alias=AliasChoices("rank_by", "rankBy"))
    filters: dict | None = None
    free_text_query: str | None = Field(default=None, validation_alias=AliasChoices("free_text_query", "freeTextQuery"))
    top_k: int = Field(default=10, ge=1, le=100)
    include_reasoning: bool = Field(default=False, validation_alias=AliasChoices("include_reasoning", "includeReasoning"))


class RankedResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_id: str = Field(validation_alias=AliasChoices("entity_id", "entityId", "id"))
    entity_type: str
    score: float
    metrics: list[EntityMetricRead] = Field(default_factory=list)
    reasoning: str | None = None


class QueryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    results: list[RankedResult]
    top_k: int
    model: str | None = None


# ── API Key ────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prefix: str = Field(
        default="hm_test",
        max_length=16,
        pattern=r"^(hm_live|hm_test)$",
        validation_alias=AliasChoices("prefix", "Prefix"),
    )
    scopes: list[str] = Field(default_factory=lambda: ["signals:write", "entities:read", "query"])
    label: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    prefix: str
    scopes: list[str]
    label: str | None = None
    is_revoked: bool
    last_used_at: datetime | None = None
    created_at: datetime
    expires_at: datetime | None = None


class ApiKeyCreated(BaseModel):
    """Response returned when an API key is created — full_key is only ever shown here."""
    model_config = ConfigDict(populate_by_name=True)

    id: int
    prefix: str
    full_key: str
    scopes: list[str]
    label: str | None = None
    message: str = "Store this key securely. It will not be shown again."


class ApiKeyListResponse(BaseModel):
    api_keys: list[ApiKeyRead]


# ── Consent ────────────────────────────────────────────────────

class ConsentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_id: str = Field(max_length=128)
    scope: str = Field(max_length=128)
    status: ConsentStatus = ConsentStatus.granted
    expires_at: datetime | None = Field(default=None, validation_alias=AliasChoices("expires_at", "expiresAt"))


class ConsentRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    entity_id: str
    scope: str
    status: str
    granted_at: datetime
    revoked_at: datetime | None = None
    expires_at: datetime | None = Field(default=None, validation_alias=AliasChoices("expires_at", "expiresAt"))


# ── Metric Pack (Spec 023) ──────────────────────────────────────

class PackMetricDef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    label: str
    type: str = "float"
    prompt: str = ""
    default_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    sensitive: bool = False
    visible_to: list[str] = Field(default_factory=list)
    requires_consent_scope: str | None = None
    allow_unknown: bool = False


class PackKVKK(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sensitive_metrics: list[str] = Field(default_factory=list)


class PackPrompts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    extraction: str = ""
    curation: str = ""


class PackFieldDef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    type: str = "str"
    label: str = ""


class PackDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_type: str = Field(max_length=64)
    label: str = Field(max_length=255)
    version: int = Field(default=1, ge=1)
    required_fields: list[str | PackFieldDef] = Field(default_factory=list)
    metrics: list[PackMetricDef] = Field(default_factory=list)
    prompts: PackPrompts = Field(default_factory=PackPrompts)
    kvkk: PackKVKK = Field(default_factory=PackKVKK)


class PackCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    yaml_text: str = Field(min_length=1, max_length=102400)
    pack_key: str = Field(
        default="",
        max_length=128,
        validation_alias=AliasChoices("pack_key", "packKey"),
    )


class PackRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pack_key: str
    version: int
    label: str
    entity_type: str
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PackDetail(PackRead):
    definition: dict = Field(default_factory=dict)


class PackWizardRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=10, max_length=10000)
    entity_type_hint: str | None = Field(
        default=None,
        max_length=64,
        validation_alias=AliasChoices("entity_type_hint", "entityTypeHint"),
    )


class PackWizardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pack_yaml: str
    validation_errors: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# ── AuditLog ───────────────────────────────────────────────────

class AuditLogRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    action: str
    entity_id: str | None = None
    details: dict | None = None
    api_key_id: int | None = None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogRead]
    total: int
    limit: int
    offset: int


# ── Error Envelope ─────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    doc_url: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


_ERROR_DOC_BASE = "https://docs.humetric.dev/errors"


def error_envelope(code: str, message: str) -> ErrorResponse:
    return ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            doc_url=f"{_ERROR_DOC_BASE}/{code}",
        )
    )


# ── Agent Models (Spec 022) ────────────────────────────────────

class ExtractedMetric(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metric_key: str = Field(validation_alias=AliasChoices("metric_key", "metricKey"))
    value: float
    confidence: float
    reasoning: str = ""
    needs_review: bool = False
    source_span: str | None = None


class ExtractionResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metrics: list[ExtractedMetric] = Field(default_factory=list)


class CurationDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metric_key: str = Field(validation_alias=AliasChoices("metric_key", "metricKey"))
    value: float
    confidence: float
    action: str
    reasoning: str = ""


class CurationResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decisions: list[CurationDecision] = Field(default_factory=list)


class FinalMetric(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    metric_key: str
    value: float
    confidence: float
    reasoning: str = ""
    source_signal_id: str | None = None
    needs_review: bool = False


class RankedResultLLM(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_id: str = Field(validation_alias=AliasChoices("entity_id", "entityId", "id"))
    score: float
    reasoning: str = ""


class RankingResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    results: list[RankedResultLLM] = Field(default_factory=list)


# ── Tenant Keys (Spec 025 + multi-provider BYOK) ───────────────

class TenantKeysRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    has_anthropic_key: bool
    has_voyage_key: bool
    has_openai_key: bool = False
    has_google_ai_key: bool = False
    has_deepseek_key: bool = False
    llm_provider: str = "anthropic"
    updated_at: datetime | None = None


class TenantKeysUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    anthropic_key: str | None = Field(default=None, max_length=512, description="Plaintext Anthropic API key")
    voyage_key: str | None = Field(default=None, max_length=512, description="Plaintext Voyage API key")
    openai_key: str | None = Field(default=None, max_length=512, description="Plaintext OpenAI API key")
    google_ai_key: str | None = Field(default=None, max_length=512, description="Plaintext Google AI API key")
    deepseek_key: str | None = Field(default=None, max_length=512, description="Plaintext DeepSeek API key")
    llm_provider: str | None = Field(default=None, max_length=64, description="Active LLM provider: anthropic | openai | google | deepseek")

    @field_validator("llm_provider")
    @classmethod
    def _validate_llm_provider(cls, v: str | None) -> str | None:
        if v is not None and v not in config.ENABLED_LLM_PROVIDERS:
            raise ValueError(
                f"Unsupported or disabled LLM provider: '{v}'. "
                f"Enabled providers: {', '.join(config.ENABLED_LLM_PROVIDERS)}"
            )
        return v


# ── Registration (Spec 026) ──────────────────────────────────────

class RegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    captcha_token: str = Field(..., max_length=2048)


class RegisterResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: int
    message: str
    email_verification_sent: bool = True
    email_verified: bool = False


class VerifyEmailResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    verified: bool = True
    message: str


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dashboard_token: str
    tenant_id: int
    name: str
    email: str
    expires_in: int = 86400


class TenantDashboardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: int
    tier: str
    subscription_status: str
    api_key_prefix: str | None = None
    usage_current_month: dict = {}
    limits: dict = {}
    stripe_customer_portal_url: str | None = None


class RotateApiKeyResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: str
    api_key_prefix: str
    message: str = "API key rotated. The old key is now invalid."


# ── Billing (Spec 026) ───────────────────────────────────────────

class CheckoutResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    checkout_url: str


class UsageRecordOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: str
    signal_count: int = 0
    llm_token_count: int = 0
    embedding_count: int = 0


class UsageReportResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: int
    start_date: str
    end_date: str
    records: list[UsageRecordOut] = []
    total: UsageRecordOut | None = None


class TierLimitExceededResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    error: str = "tier_limit_exceeded"
    message: str
    upgrade_url: str
    current_usage: dict = {}


class ReviewerOverrideRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    value: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    comment: str = ""


class ReviewerOverrideResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entity_id: str
    metric_key: str
    previous_value: float | None = None
    previous_confidence: float | None = None
    new_value: float
    new_confidence: float
    comment: str = ""
    overridden_at: datetime | None = None


# ── Metric Analyzer (Spec 027 — Faz 1) ──────────────────────────

class AnalyzerImage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(max_length=255)
    media_type: str = Field(pattern=r"^image/(png|jpeg|webp|gif)$")
    data_b64: str = Field(max_length=(config.ANALYZER_MAX_IMAGE_MB * 1024 * 1024 * 2))

    @field_validator("data_b64")
    @classmethod
    def _validate_decoded_size(cls, v: str) -> str:
        import base64

        try:
            decoded_len = len(base64.b64decode(v, validate=True))
        except Exception as exc:
            raise ValueError(f"Invalid base64 image data: {exc}") from exc
        max_bytes = config.ANALYZER_MAX_IMAGE_MB * 1024 * 1024
        if decoded_len > max_bytes:
            raise ValueError(f"Image exceeds max size of {config.ANALYZER_MAX_IMAGE_MB}MB")
        return v


class AnalyzerSessionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(min_length=10, max_length=5000)
    schema_text: str | None = Field(default=None, max_length=config.ANALYZER_MAX_SCHEMA_CHARS)
    schema_format: str | None = Field(default=None, pattern=r"^(sql|json)$")
    images: list[AnalyzerImage] = Field(default_factory=list)

    @field_validator("images")
    @classmethod
    def _validate_image_count(cls, v: list[AnalyzerImage]) -> list[AnalyzerImage]:
        if len(v) > config.ANALYZER_MAX_IMAGES:
            raise ValueError(f"Too many images (max {config.ANALYZER_MAX_IMAGES})")
        return v


class AnalyzerRefineRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(min_length=1, max_length=5000)


class AnalyzerFieldDef(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    key: str
    type: str = "str"
    label: str = ""


class AnalyzerMetricProposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    key: str
    label: str
    type: str = "float"
    prompt: str = ""
    default_confidence: float = 0.7
    sensitive: bool = False
    requires_consent_scope: str | None = None
    rationale: str = ""

    @field_validator("default_confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        # Structured-output JSON schemas reject numeric ge/le constraints, so
        # the [0,1] range is enforced here instead of via Field(ge=..., le=...).
        return max(0.0, min(1.0, v))


class AnalyzerFindings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    summary: str = ""
    entity_type: str
    label: str
    required_fields: list[AnalyzerFieldDef] = Field(default_factory=list)
    metrics: list[AnalyzerMetricProposal] = Field(default_factory=list)
    extraction_prompt: str = ""
    curation_prompt: str = ""
    open_questions: list[str] = Field(default_factory=list)
    market_notes: str = ""


class AnalyzerArtifactMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    name: str
    format: str | None = None
    media_type: str | None = None
    size: int = 0


class AnalyzerReportSection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    text: str
    ts: str


class AnalyzerSessionRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    title: str
    status: str
    refine_count: int
    max_refines: int
    report: list[AnalyzerReportSection] = Field(default_factory=list)
    artifacts: list[AnalyzerArtifactMeta] = Field(default_factory=list)
    findings: AnalyzerFindings | None = None
    error: str | None = None
    pack_key: str | None = None
    checkout_url: str | None = None
    paid_at: datetime | None = None
    created_at: datetime | None = None


class AnalyzerSessionListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    title: str
    status: str
    refine_count: int
    pack_key: str | None = None
    paid_at: datetime | None = None
    created_at: datetime | None = None


class AnalyzerSessionListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[AnalyzerSessionListItem] = Field(default_factory=list)
    total: int = 0


class AnalyzerCreatePackRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pack_key: str | None = Field(default=None, max_length=128)
