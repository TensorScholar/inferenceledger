import importlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, cast

import structlog

try:
    _openai_module = importlib.import_module("openai")
    AsyncOpenAI: Any = _openai_module.AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

from ...domain.cost.calculator import CostCalculator
from ...domain.models.execution import AttemptOutcome, CostEvidenceKind, ProviderAttempt
from ...domain.models.request import InferenceRequest
from ...domain.models.response import CacheInfo, InferenceResponse, UsageMetrics
from .base import AbstractModelBackend
from .errors import ProviderError, classify_openai_error, missing_usage_error

logger = structlog.get_logger()

_PROVIDER_NAME = "openai-compatible"


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry policy for provider calls."""

    max_attempts: int = 2
    backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")


@dataclass(frozen=True)
class ProviderCallResult:
    """Provider response plus every local provider invocation used to obtain it."""

    response: Any
    attempts: tuple[ProviderAttempt, ...]

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)


class OpenAIBackend(AbstractModelBackend):
    """OpenAI-compatible chat completions backend."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        *,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        cost_calculator: CostCalculator | None = None,
    ) -> None:
        if AsyncOpenAI is None:
            raise ImportError("openai package not installed")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._model_name = model_name
        self.retry_policy = retry_policy or RetryPolicy()
        self.cost_calculator = cost_calculator or CostCalculator()

    @property
    def model_name(self) -> str:
        return self._model_name

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        """Run inference via OpenAI API and preserve the complete local retry chain."""
        start = perf_counter()

        messages = request.messages or [{"role": "user", "content": request.prompt}]
        call_result = await self._create_completion(messages, request)
        response = call_result.response

        elapsed_ms = int((perf_counter() - start) * 1000)
        completion = response.choices[0].message.content or ""
        usage = response.usage
        if usage is None:
            attempts = _mark_last_attempt_error(
                call_result.attempts,
                error_type="missing_usage",
            )
            raise replace(
                missing_usage_error(self.model_name),
                provider_attempts=attempts,
            )

        cached_tokens = _extract_cached_tokens(usage)
        calculated_cost = self.cost_calculator.calculate_for_model_name(
            model_name=self.model_name,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cached_input_tokens=cached_tokens,
        )
        final_attempt = replace(
            call_result.attempts[-1],
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cached_tokens=cached_tokens,
            calculated_cost_usd=calculated_cost,
            cost_evidence=CostEvidenceKind.CALCULATED_FROM_USAGE,
            pricing_table_version=self.cost_calculator.pricing_table.version,
        )
        attempts = (*call_result.attempts[:-1], final_attempt)

        return InferenceResponse(
            request_id=request.id,
            text=completion,
            finish_reason=response.choices[0].finish_reason or "stop",
            model_used=self.model_name,
            usage=UsageMetrics(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                cached_tokens=cached_tokens,
                cost_usd=calculated_cost,
            ),
            cache_info=CacheInfo(hit=False),
            latency_ms=elapsed_ms,
            inference_time_ms=elapsed_ms,
            provider_attempts=attempts,
        )

    async def infer_batch(self, requests: list[InferenceRequest]) -> list[InferenceResponse]:
        """Process batch sequentially (OpenAI doesn't support batching)."""
        results = []
        for req in requests:
            results.append(await self.infer(req))
        return results

    async def stream(self, request: InferenceRequest) -> AsyncIterator[str]:
        """Stream tokens from OpenAI.

        Streaming usage/cost evidence is not yet exposed by this reference interface, so streaming
        must not be used to substantiate migration-cost claims.
        """
        messages = request.messages or [{"role": "user", "content": request.prompt}]

        call_result = await self._create_completion(messages, request, stream=True)
        stream = call_result.response
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def health_check(self) -> bool:
        """Check OpenAI API health."""
        try:
            await self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception as e:
            logger.error("openai_health_check_failed", error=str(e))
            return False

    async def _create_completion(
        self,
        messages: list[dict[str, str]],
        request: InferenceRequest,
        *,
        stream: bool = False,
    ) -> ProviderCallResult:
        import asyncio

        attempts: list[ProviderAttempt] = []
        last_error: ProviderError | None = None
        for attempt_index in range(1, self.retry_policy.max_attempts + 1):
            attempt_start = perf_counter()
            try:
                response = await self.client.chat.completions.create(
                    model=self.model_name,
                    messages=cast(Any, messages),
                    max_tokens=request.parameters.max_tokens,
                    temperature=request.parameters.temperature,
                    top_p=request.parameters.top_p,
                    frequency_penalty=request.parameters.frequency_penalty,
                    presence_penalty=request.parameters.presence_penalty,
                    stop=request.parameters.stop_sequences or None,
                    stream=stream,
                )
                attempts.append(
                    ProviderAttempt(
                        attempt_index=attempt_index,
                        provider=_PROVIDER_NAME,
                        model=self.model_name,
                        outcome=AttemptOutcome.SUCCEEDED,
                        latency_ms=int((perf_counter() - attempt_start) * 1000),
                    )
                )
                return ProviderCallResult(response=response, attempts=tuple(attempts))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                provider_error = classify_openai_error(exc)
                attempts.append(
                    ProviderAttempt(
                        attempt_index=attempt_index,
                        provider=_PROVIDER_NAME,
                        model=self.model_name,
                        outcome=AttemptOutcome.FAILED,
                        latency_ms=int((perf_counter() - attempt_start) * 1000),
                        error_type=provider_error.error_type.value,
                        status_code=provider_error.status_code,
                    )
                )
                provider_error = replace(
                    provider_error,
                    provider_attempts=tuple(attempts),
                )
                last_error = provider_error
                if not provider_error.retryable or attempt_index >= self.retry_policy.max_attempts:
                    raise provider_error from exc
                await asyncio.sleep(self.retry_policy.backoff_seconds * attempt_index)

        if last_error is not None:
            raise last_error
        raise ProviderError(
            error_type=classify_openai_error(RuntimeError("provider call failed")).error_type,
            message="Provider call failed without a captured exception",
            provider=_PROVIDER_NAME,
            retryable=False,
            provider_attempts=tuple(attempts),
        )


def _mark_last_attempt_error(
    attempts: tuple[ProviderAttempt, ...],
    *,
    error_type: str,
) -> tuple[ProviderAttempt, ...]:
    if not attempts:
        return attempts
    return (*attempts[:-1], replace(attempts[-1], error_type=error_type))


def _extract_cached_tokens(usage: Any) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    cached_tokens = getattr(details, "cached_tokens", 0)
    if isinstance(cached_tokens, int):
        return cached_tokens
    return 0
