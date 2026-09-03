"""Unit tests for routing strategies."""

import pytest

from inference_engine.domain.models.request import InferenceRequest, ModelParameters
from inference_engine.domain.models.routing import (
    ModelConfig,
    ModelTier,
    RoutingReason,
    RoutingStrategy,
)
from inference_engine.domain.routing.baseline import BaselineRouter, BaselineRoutingModeError
from inference_engine.domain.routing.complexity import ComplexityEstimator
from inference_engine.domain.routing.cost_aware import CostAwareRouter
from inference_engine.domain.routing.load_balanced import LoadBalancedRouter
from inference_engine.domain.routing.policy import PolicyRouter, PolicyRouterConfig


class FakeCostEstimator:
    """Deterministic test-only tariff source; model metadata contains no monetary rates."""

    def __init__(self, cost_per_1k_total_tokens: dict[str, float]) -> None:
        self.cost_per_1k_total_tokens = cost_per_1k_total_tokens

    def estimate(
        self,
        *,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        return (
            (input_tokens + output_tokens)
            / 1000
            * self.cost_per_1k_total_tokens[model_id]
        )


@pytest.fixture
def sample_models() -> list[ModelConfig]:
    return [
        ModelConfig(
            id="gpt-4",
            name="GPT-4",
            tier=ModelTier.PREMIUM,
            max_context_length=8192,
        ),
        ModelConfig(
            id="gpt-3.5",
            name="GPT-3.5 Turbo",
            tier=ModelTier.ECONOMY,
            max_context_length=4096,
        ),
    ]


@pytest.fixture
def sample_cost_estimator() -> FakeCostEstimator:
    return FakeCostEstimator(
        {
            "gpt-4": 0.09,
            "gpt-3.5": 0.0035,
        }
    )


@pytest.fixture
def simple_request() -> InferenceRequest:
    return InferenceRequest(
        prompt="Hello world",
        parameters=ModelParameters(max_tokens=10),
    )


@pytest.fixture
def complex_request() -> InferenceRequest:
    return InferenceRequest(
        prompt="Analyze quantum computing and explain how superposition works in detail",
        parameters=ModelParameters(max_tokens=500),
    )


class TestComplexityEstimator:
    @pytest.mark.asyncio
    async def test_simple_query(self, simple_request):
        estimator = ComplexityEstimator()

        complexity = await estimator.estimate(simple_request)
        assert complexity.score < 0.3

    @pytest.mark.asyncio
    async def test_complex_query(self, complex_request):
        estimator = ComplexityEstimator()

        complexity = await estimator.estimate(complex_request)
        assert complexity.score > 0.5

    @pytest.mark.asyncio
    async def test_recommended_tier(self, simple_request, complex_request):
        estimator = ComplexityEstimator()

        simple_complexity = await estimator.estimate(simple_request)
        complex_complexity = await estimator.estimate(complex_request)

        assert simple_complexity.recommended_tier == ModelTier.ECONOMY
        assert complex_complexity.recommended_tier in [ModelTier.STANDARD, ModelTier.PREMIUM]


class TestCostAwareRouter:
    @pytest.mark.asyncio
    async def test_route_simple_request(
        self,
        simple_request,
        sample_models,
        sample_cost_estimator,
    ):
        router = CostAwareRouter(
            sample_models,
            ComplexityEstimator(),
            sample_cost_estimator,
            cost_weight=0.9,
        )

        decision = await router.route(simple_request)

        assert decision.selected_model.id == "gpt-3.5"
        assert decision.estimated_cost == pytest.approx(
            sample_cost_estimator.estimate(
                model_id="gpt-3.5",
                input_tokens=simple_request.estimated_input_tokens,
                output_tokens=simple_request.parameters.max_tokens,
            )
        )

    @pytest.mark.asyncio
    async def test_route_standard_complexity_uses_request_specific_cost_estimate(
        self,
        complex_request,
        sample_models,
        sample_cost_estimator,
    ):
        complexity_estimator = ComplexityEstimator()
        complexity = await complexity_estimator.estimate(complex_request)
        assert complexity.recommended_tier == ModelTier.STANDARD

        router = CostAwareRouter(
            sample_models,
            complexity_estimator,
            sample_cost_estimator,
            cost_weight=0.5,
        )

        decision = await router.route(complex_request)

        assert decision.estimated_cost == pytest.approx(
            sample_cost_estimator.estimate(
                model_id=decision.selected_model.id,
                input_tokens=complex_request.estimated_input_tokens,
                output_tokens=complex_request.parameters.max_tokens,
            )
        )
        assert decision.estimated_latency_ms > 0

    @pytest.mark.asyncio
    async def test_fallback_chain(
        self,
        simple_request,
        sample_models,
        sample_cost_estimator,
    ):
        router = CostAwareRouter(
            sample_models,
            ComplexityEstimator(),
            sample_cost_estimator,
        )

        decision = await router.route(simple_request)

        assert [model.id for model in decision.fallback_models] == ["gpt-4"]


class TestBaselineRouter:
    @pytest.mark.asyncio
    async def test_single_model_mode_routes_to_configured_model(
        self,
        simple_request,
        sample_models,
        sample_cost_estimator,
    ):
        router = BaselineRouter(
            sample_models,
            ComplexityEstimator(),
            sample_cost_estimator,
            mode=RoutingStrategy.SINGLE_MODEL,
            single_model_id="gpt-4",
        )

        decision = await router.route(simple_request)

        assert decision.strategy == RoutingStrategy.SINGLE_MODEL
        assert decision.selected_model.id == "gpt-4"
        assert "single_model baseline" in decision.decision_reason

    @pytest.mark.asyncio
    async def test_rule_based_mode_uses_request_specific_cheapest_sufficient_tier(
        self,
        simple_request,
        complex_request,
        sample_models,
        sample_cost_estimator,
    ):
        router = BaselineRouter(
            sample_models,
            ComplexityEstimator(),
            sample_cost_estimator,
            mode=RoutingStrategy.RULE_BASED,
        )

        simple_decision = await router.route(simple_request)
        complex_decision = await router.route(complex_request)

        assert simple_decision.selected_model.id == "gpt-3.5"
        assert complex_decision.selected_model.tier.rank >= ModelTier.STANDARD.rank
        assert complex_decision.complexity_estimate is not None

    @pytest.mark.asyncio
    async def test_single_model_mode_requires_model_id(
        self,
        simple_request,
        sample_models,
        sample_cost_estimator,
    ):
        router = BaselineRouter(
            sample_models,
            ComplexityEstimator(),
            sample_cost_estimator,
            mode=RoutingStrategy.SINGLE_MODEL,
        )

        with pytest.raises(BaselineRoutingModeError, match="single_model_id"):
            await router.route(simple_request)


class TestPolicyRouter:
    @pytest.fixture
    def policy_models(self) -> list[ModelConfig]:
        return [
            ModelConfig(
                id="economy",
                name="Economy",
                tier=ModelTier.ECONOMY,
                max_context_length=4096,
                avg_latency_ms=250,
            ),
            ModelConfig(
                id="standard",
                name="Standard",
                tier=ModelTier.STANDARD,
                max_context_length=8192,
                avg_latency_ms=700,
            ),
            ModelConfig(
                id="premium",
                name="Premium",
                tier=ModelTier.PREMIUM,
                max_context_length=128_000,
                avg_latency_ms=1400,
            ),
        ]

    @pytest.fixture
    def policy_cost_estimator(self) -> FakeCostEstimator:
        return FakeCostEstimator(
            {
                "economy": 0.002,
                "standard": 0.015,
                "premium": 0.09,
            }
        )

    @pytest.mark.asyncio
    async def test_policy_prefers_candidate_within_budget(
        self,
        simple_request,
        policy_models,
        policy_cost_estimator,
    ):
        router = PolicyRouter(
            policy_models,
            ComplexityEstimator(),
            policy_cost_estimator,
            PolicyRouterConfig(max_estimated_cost_usd=0.00003),
        )

        decision = await router.route(simple_request)

        assert decision.strategy == RoutingStrategy.POLICY
        assert decision.selected_model.id == "economy"
        assert decision.estimated_cost <= 0.00003
        assert decision.decision_reason == RoutingReason.POLICY_COST_WITHIN_BUDGET

    @pytest.mark.asyncio
    async def test_policy_latency_slo_filters_slow_models(
        self,
        simple_request,
        policy_models,
        policy_cost_estimator,
    ):
        router = PolicyRouter(
            policy_models,
            ComplexityEstimator(),
            policy_cost_estimator,
            PolicyRouterConfig(
                latency_slo_ms=800,
                min_quality_score=0.70,
            ),
        )

        decision = await router.route(simple_request)

        assert decision.selected_model.id == "standard"
        assert decision.estimated_latency_ms <= 800
        assert decision.decision_reason == RoutingReason.POLICY_LATENCY_WITHIN_SLO

    @pytest.mark.asyncio
    async def test_policy_quality_floor_selects_capable_model(
        self,
        simple_request,
        policy_models,
        policy_cost_estimator,
    ):
        router = PolicyRouter(
            policy_models,
            ComplexityEstimator(),
            policy_cost_estimator,
            PolicyRouterConfig(min_quality_score=0.90),
        )

        decision = await router.route(simple_request)

        assert decision.selected_model.id == "premium"
        assert decision.estimated_quality_score >= 0.90
        assert decision.decision_reason == RoutingReason.POLICY_QUALITY_FLOOR

    @pytest.mark.asyncio
    async def test_policy_records_impossible_budget_reason(
        self,
        simple_request,
        policy_models,
        policy_cost_estimator,
    ):
        router = PolicyRouter(
            policy_models,
            ComplexityEstimator(),
            policy_cost_estimator,
            PolicyRouterConfig(max_estimated_cost_usd=0.00000001),
        )

        decision = await router.route(simple_request)

        assert decision.selected_model.id == "economy"
        assert decision.estimated_cost > 0.00000001
        assert decision.decision_reason == RoutingReason.POLICY_NO_CANDIDATE_WITHIN_BUDGET

    @pytest.mark.asyncio
    async def test_policy_does_not_mask_impossible_quality_reason(
        self,
        simple_request,
        policy_models,
        policy_cost_estimator,
    ):
        router = PolicyRouter(
            policy_models,
            ComplexityEstimator(),
            policy_cost_estimator,
            PolicyRouterConfig(
                min_quality_score=1.0,
                latency_slo_ms=800,
            ),
        )

        decision = await router.route(simple_request)

        assert decision.decision_reason == RoutingReason.POLICY_NO_CANDIDATE_MEETS_QUALITY_FLOOR


class TestLoadBalancedRouter:
    @pytest.mark.asyncio
    async def test_round_robin(
        self,
        simple_request,
        sample_models,
        sample_cost_estimator,
    ):
        router = LoadBalancedRouter(sample_models, sample_cost_estimator)

        decision1 = await router.route(simple_request)
        decision2 = await router.route(simple_request)

        assert decision1.selected_model.id != decision2.selected_model.id
        assert decision1.estimated_cost > 0
        assert decision2.estimated_cost > 0
