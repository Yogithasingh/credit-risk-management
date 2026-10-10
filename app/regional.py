"""Regional market scope and supported input denominations."""

from __future__ import annotations


SUPPORTED_MARKETS = [
    {
        "country_code": "US",
        "country_name": "United States",
        "currency_code": "USD",
        "currency_name": "US Dollar",
        "currency_symbol": "$",
        "reference_currency": "USD",
        "supported_input_currencies": ["USD", "INR", "GBP", "EUR"],
    }
]

# These are input denominations only. All monetary features are converted to
# USD before scoring. They do not add support for another country's credit market.
SUPPORTED_CURRENCIES = [
    {"currency_code": "USD", "currency_name": "US Dollar", "currency_symbol": "$"},
    {"currency_code": "INR", "currency_name": "Indian Rupee", "currency_symbol": "₹"},
    {"currency_code": "GBP", "currency_name": "Pound Sterling", "currency_symbol": "£"},
    {"currency_code": "EUR", "currency_name": "Euro", "currency_symbol": "€"},
]

UNSUPPORTED_MARKETS = [
    {"country_code": "IN", "country_name": "India", "reason": "application market not validated"},
    {"country_code": "GB", "country_name": "United Kingdom", "reason": "application market not validated"},
    {"country_code": "EU", "country_name": "European Union", "reason": "application market not validated"},
]

SUPPORTED_LOCALES = [
    {"locale_code": "en-US", "name": "English (United States)"},
    {"locale_code": "en-GB", "name": "English (United Kingdom)"},
    {"locale_code": "en-IN", "name": "English (India)"},
    {"locale_code": "hi-IN", "name": "Hindi (India)"},
    {"locale_code": "de-DE", "name": "German (Germany)"},
    {"locale_code": "fr-FR", "name": "French (France)"},
]

MODEL_FINANCIAL_SCOPE = {
    "application_market": "United States",
    "country_code": "US",
    "reference_currency": "USD",
    "monetary_features": ["loan_amount", "annual_income", "revolving_balance"],
    "conversion_enabled": True,
    "supported_input_currencies": [item["currency_code"] for item in SUPPORTED_CURRENCIES],
    "conversion_provider": "Frankfurter reference rates",
    "basis": "The supplied LendingClub loan_amnt, annual_inc, and revol_bal training features are USD-denominated. Supported input denominations are converted to USD using a dated daily reference rate before scoring. This does not validate the model for non-US credit markets.",
}


def supported_currency(currency_code: str) -> dict | None:
    code = str(currency_code).strip().upper()
    return next((item for item in SUPPORTED_CURRENCIES if item["currency_code"] == code), None)


def supported_market(country_code: str) -> dict | None:
    code = str(country_code).strip().upper()
    return next((item for item in SUPPORTED_MARKETS if item["country_code"] == code), None)


def options_public() -> dict:
    return {
        "supported_markets": SUPPORTED_MARKETS,
        "unsupported_markets": UNSUPPORTED_MARKETS,
        "supported_currencies": SUPPORTED_CURRENCIES,
        "supported_locales": SUPPORTED_LOCALES,
        "model_financial_scope": MODEL_FINANCIAL_SCOPE,
    }
