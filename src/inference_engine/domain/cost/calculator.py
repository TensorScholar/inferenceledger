import structlog

from ..models.routing import ModelConfig
from .pricing import PricingQuote, PricingTable

logger = structlog.get_logger()


class CostCalculator:
    """Calculates costs for LLM inference requests."""

    def __init__(self, pricing_table: PricingTable | None = None) -> None:
        self.pricing_table = pricing_table or PricingTable()

    def calculate(
        self, model: ModelConfig, input_tokens: int, output_tokens: int
    ) -> float:
        """Legacy routing-estimate path; observed execution must use provider pricing quotes."""
        input_cost = (input_tokens / 1000) * model.cost_per_1k_input_tokens
        output_cost = (output_tokens / 1000) * model.cost_per_1k_output_tokens
        total = input_cost + output_cost

        logger.debug(
            "cost_calculated",
            model=model.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost=total,
        )

        return total

    def quote_provider_usage(
        self,
        *,
        provider: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> PricingQuote:
        """Calculate execution cost and preserve the exact provider pricing provenance."""
        quote = self.pricing_table.quote(
            provider=provider,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )

        logger.debug(
            "cost_calculated_from_provider_usage",
            provider=provider,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            pricing_table_version=quote.pricing_table_version,
            pricing_record_id=quote.pricing_record_id,
            total_cost=quote.amount_usd,
        )
        return quote

    def calculate_savings(
        self,
        base_model: ModelConfig,
        alternative_model: ModelConfig,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate savings using the legacy routing-estimate path."""
        base_cost = self.calculate(base_model, input_tokens, output_tokens)
        alt_cost = self.calculate(alternative_model, input_tokens, output_tokens)
        return max(0, base_cost - alt_cost)
