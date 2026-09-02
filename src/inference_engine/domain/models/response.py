from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from ...utils.time import utc_now


@dataclass(frozen=True)
class CacheInfo:
    """Information about cache use for one response."""

    hit: bool
    source: str | None = None
    similarity_score: float | None = None
    tokens_saved: int = 0
    latency_saved_ms: int = 0


@dataclass(frozen=True)
class UsageMetrics:
    """Token usage and cost metrics."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def cache_hit_rate(self) -> float:
        if self.total_tokens == 0:
            return 0.0
        return self.cached_tokens / self.total_tokens


@dataclass(frozen=True)
class InferenceResponse:
    """Canonical inference response."""

    request_id: UUID
    text: str
    model_used: str
    usage: UsageMetrics
    cache_info: CacheInfo
    latency_ms: int
    finish_reason: str = "stop"
    queue_time_ms: int = 0
    inference_time_ms: int = 0
    postprocess_time_ms: int = 0
    provider_attempt_count: int = 1
    provider_retry_count: int = 0
    timestamp: datetime = field(default_factory=utc_now)
    id: UUID = field(default_factory=uuid4)

    @property
    def total_cost_usd(self) -> float:
        return self.usage.cost_usd
