"""The optional indicators a user can include in the first strategy input."""

from copy import deepcopy
from typing import Literal

from .optional_indicators import VERSION as INITIAL_INDICATOR_CATALOG_VERSION

__all__ = [
    "INITIAL_INDICATOR_CATALOG_VERSION",
    "INITIAL_INDICATOR_DEFAULT_PARAMETERS",
    "InitialIndicatorName",
    "initial_indicator_catalog",
    "normalize_initial_indicators",
]

InitialIndicatorName = Literal[
    "bollinger", "fibonacci", "adx_dmi", "obv", "donchian", "keltner", "stochastic",
]

# These defaults match the existing tool contracts. Selecting an indicator does
# not tune its parameters or remove unchecked tools from the Agent's catalog.
INITIAL_INDICATOR_DEFAULT_PARAMETERS: dict[InitialIndicatorName, dict] = {
    "bollinger": {"period": 20, "multiplier": 2},
    "fibonacci": {"lookback": 160, "width": 3, "direction": "auto"},
    "adx_dmi": {"period": 14, "adx_period": 14},
    "obv": {"period": 20},
    "donchian": {"period": 20},
    "keltner": {"period": 20, "atr_period": 14, "multiplier": 2},
    "stochastic": {"period": 14, "smooth_k": 3, "smooth_d": 3},
}


def normalize_initial_indicators(value: object) -> list[InitialIndicatorName]:
    """Read legacy metadata safely and deduplicate in stable catalog order.

    API request models validate the whitelist first. This tolerant reader is for
    persisted preferences written by earlier versions or manually edited files.
    """
    if not isinstance(value, list):
        return []
    chosen = {item for item in value if isinstance(item, str)}
    return [name for name in INITIAL_INDICATOR_DEFAULT_PARAMETERS if name in chosen]


def initial_indicator_catalog() -> list[dict]:
    """Return locale-neutral names and a fresh copy of calculation defaults."""
    return [{"name": name, "tool": name, "parameters": deepcopy(parameters)}
            for name, parameters in INITIAL_INDICATOR_DEFAULT_PARAMETERS.items()]
