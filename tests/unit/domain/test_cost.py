"""Unit tests for cost management."""

import pytest

from inference_engine.domain.cost.calculator import CostCalculator
from inference_engine.domain.cost.pricing import (
    DEFAULT_PRICING,
    PRICING_TABLE_VERSION,
    ModelPricing,
    PricingTable,
    UnknownModelPricingError,
)
from inference_engine.domain.models.cost import CostBreakdown
from inference_engine.domain.models.routing import ModelConfig, ModelTier


@pytest.fixture
def sample_model() -> ModelConfig:
    """Create sample model configuration."""
    return ModelConfig(
        id="test-model",
        name="Test Model",
        tier=ModelTier.STANDARD,
        max_context_length=2048,
        cost_per_1k_input_tokens=0.01,
        cost_per_1k_output_tokens=0.02,
    )


def _pricing_table(*, cached_input_per_million: float | None = None) -> PricingTable:
    pricing = ModelPricing(
        provider="test-provider",
        model="test-model",
        input_per_million=1.00,
        output_per_million=2.00,
        cached_input_per_million=cached_input_per_million,
        source_url="https://pricing.example/test-model",
    )
    return PricingTable(
        prices={(pricing.provider, pricing.model): pricing},
        version="test",
    )


class TestCostCalculator:
    """Tests for CostCalculator."""

    def test_calculate_cost(self, sample_model):
        """Test the legacy routing-estimate path separately from observed execution pricing."""
        calculator = CostCalculator()

        cost = calculator.calculate(sample_model, input_tokens=100, output_tokens=50)

        expected = (100 / 1000) * 0.01 + (50 / 1000) * 0.02
        assert abs(cost - expected) < 0.0001

    def test_calculate_savings(self, sample_model):
        """Test legacy routing-estimate savings behavior."""
        calculator = CostCalculator()

        premium_model = ModelConfig(
            id="premium",
            name="Premium",
            tier=ModelTier.PREMIUM,
            max_context_length=4096,
            cost_per_1k_input_tokens=0.05,
            cost_per_1k_output_tokens=0.10,
        )

        savings = calculator.calculate_savings(
            premium_model,
            sample_model,
            input_tokens=100,
            output_tokens=50,
        )

        assert savings > 0

    def test_quote_provider_usage_preserves_pricing_provenance(self):
        """Observed execution pricing must identify the exact provider pricing assumption."""
        calculator = CostCalculator(_pricing_table())

        quote = calculator.quote_provider_usage(
            provider="test-provider",
            model_name="test-model",
            input_tokens=1_000,
            output_tokens=500,
        )

        assert quote.amount_usd == pytest.approx(0.002)
        assert quote.provider == "test-provider"
        assert quote.model == "test-model"
        assert quote.pricing_table_version == "test"
        assert quote.pricing_record_id.startswith("test-provider:test-model:")
        assert quote.pricing_source_url == "https://pricing.example/test-model"

    def test_quote_provider_usage_uses_cached_input_rate(self):
        """Cached input tokens are priced separately when the provider record supplies a rate."""
        calculator = CostCalculator(_pricing_table(cached_input_per_million=0.25))

        quote = calculator.quote_provider_usage(
            provider="test-provider",
            model_name="test-model",
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=400,
        )

        assert quote.amount_usd == pytest.approx(0.0017)

    def test_quote_provider_usage_rejects_unknown_provider_even_for_known_model(self):
        """A matching model name must never authorize another provider's price."""
        calculator = CostCalculator(_pricing_table())

        with pytest.raises(UnknownModelPricingError):
            calculator.quote_provider_usage(
                provider="different-provider",
                model_name="test-model",
                input_tokens=1,
                output_tokens=1,
            )

    def test_default_pricing_includes_sourced_openai_standard_record(self):
        """Built-in pricing is provider-qualified and explicitly versioned."""
        pricing = DEFAULT_PRICING[("openai", "gpt-5.4-mini")]

        assert PRICING_TABLE_VERSION == "openai-standard-2026-09-03"
        assert pricing.provider == "openai"
        assert pricing.input_per_million == 0.75
        assert pricing.output_per_million == 4.50
        assert pricing.cached_input_per_million == 0.075
        assert pricing.source_url


class TestCostBreakdown:
    """Tests for CostBreakdown."""

    def test_savings_rate(self):
        """Test savings rate."""
        breakdown = CostBreakdown(
            inference_cost=100.0,
            compute_cost=20.0,
            cache_savings=30.0,
            optimization_savings=50.0,
        )

        assert breakdown.savings_rate == pytest.approx(80 / 120)

    def test_net_cost(self):
        """Test net cost."""
        breakdown = CostBreakdown(
            inference_cost=100.0,
            compute_cost=20.0,
            cache_savings=30.0,
            optimization_savings=50.0,
        )

        assert breakdown.net_cost == 40.0
