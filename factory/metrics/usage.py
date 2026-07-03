"""Token cost estimation.

Backends report exact cost when their SDK provides it; otherwise we estimate
from a coarse per-model price table. Prices are configurable here in one
place and intentionally conservative - the dashboard labels them estimates.
"""

from __future__ import annotations

from factory.backends.base import Usage

# USD per 1M tokens: (input, output). Longest matching prefix wins.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "composer": (2.0, 10.0),
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.8, 4.0),
    "gpt": (5.0, 15.0),
}

DEFAULT_PRICE = (3.0, 15.0)


def price_for_model(model: str) -> tuple[float, float]:
    model = model.lower()
    best: tuple[int, tuple[float, float]] | None = None
    for prefix, price in PRICE_TABLE.items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else DEFAULT_PRICE


def estimate_cost_usd(model: str, usage: Usage) -> float:
    """Return reported cost if present, else a price-table estimate."""
    if usage.estimated_cost_usd > 0:
        return usage.estimated_cost_usd
    input_price, output_price = price_for_model(model)
    return round(
        usage.prompt_tokens * input_price / 1_000_000
        + usage.completion_tokens * output_price / 1_000_000,
        6,
    )
