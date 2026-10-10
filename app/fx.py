"""Fetch auditable reference FX quotes and normalize monetary model inputs."""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import URLError
from urllib.request import Request, urlopen
from typing import Any

from app import regional


class FXRateUnavailable(RuntimeError):
    pass


def fetch_usd_rate(currency_code: str) -> dict[str, Any]:
    """Return USD per one input-currency unit from a dated reference source."""
    currency = regional.supported_currency(currency_code)
    if not currency:
        raise FXRateUnavailable("That input currency is not supported.")
    code = currency["currency_code"]
    if code == "USD":
        return {
            "currency_code": "USD",
            "reference_currency": "USD",
            "rate": 1.0,
            "rate_date": None,
            "source": "Identity conversion (USD to USD)",
        }

    base_url = os.getenv("FX_API_BASE_URL", "https://api.frankfurter.dev/v2").rstrip("/")
    url = f"{base_url}/rate/{code.lower()}/usd"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "CrediGuardAI/1.0"})
    try:
        with urlopen(request, timeout=6) as response:
            quote = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise FXRateUnavailable(
            "The exchange-rate service is unavailable. Try again shortly, or choose USD."
        ) from exc

    try:
        rate_date = date.fromisoformat(str(quote["date"]))
        rate = float(quote["rate"])
        quote_base = str(quote["base"]).upper()
        quote_target = str(quote["quote"]).upper()
    except (KeyError, TypeError, ValueError) as exc:
        raise FXRateUnavailable("The exchange-rate service returned an invalid quote.") from exc
    if quote_base != code or quote_target != "USD" or not math.isfinite(rate) or rate <= 0:
        raise FXRateUnavailable("The exchange-rate service returned an invalid quote.")
    today_utc = datetime.now(timezone.utc).date()
    if (today_utc - rate_date).days > 5 or rate_date > today_utc:
        raise FXRateUnavailable("The latest published exchange rate is too old. Try again later or choose USD.")

    return {
        "currency_code": code,
        "reference_currency": "USD",
        "rate": rate,
        "rate_date": rate_date.isoformat(),
        "source": "Frankfurter reference rates",
    }


def normalize_features(features: dict[str, Any], quote: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert only money fields to USD and return the exact recorded snapshot."""
    code = str(quote["currency_code"]).upper()
    if not regional.supported_currency(code):
        raise FXRateUnavailable("The saved quote uses an unsupported input currency.")
    rate = Decimal(str(quote["rate"]))
    if not rate.is_finite() or rate <= 0:
        raise FXRateUnavailable("The saved exchange rate is invalid.")

    normalized = dict(features)
    model_amounts: dict[str, float] = {}
    for feature in regional.MODEL_FINANCIAL_SCOPE["monetary_features"]:
        if feature not in features:
            continue
        converted = (Decimal(str(features[feature])) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        normalized[feature] = float(converted)
        model_amounts[feature] = float(converted)

    snapshot = {
        "input_currency_code": code,
        "model_currency_code": "USD",
        "usd_per_input_unit": float(rate),
        "rate_date": quote.get("rate_date"),
        "rate_source": quote["source"],
        "quoted_at": quote.get("quoted_at"),
        "model_amounts_usd": model_amounts,
        "rounding": "Converted monetary features rounded to USD cents using ROUND_HALF_UP.",
    }
    return normalized, snapshot
