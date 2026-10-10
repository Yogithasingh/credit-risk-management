"""Regional settings supported by the current LendingClub model."""

from __future__ import annotations


SUPPORTED_MARKETS = [
    {
        "country_code": "US",
        "country_name": "United States",
        "currency_code": "USD",
        "currency_name": "US Dollar",
        "currency_symbol": "$",
    }
]

UNSUPPORTED_MARKETS = [
    {"country_code": "IN", "country_name": "India", "currency_code": "INR", "currency_name": "Indian Rupee"},
    {"country_code": "GB", "country_name": "United Kingdom", "currency_code": "GBP", "currency_name": "Pound Sterling"},
    {"country_code": "EU", "country_name": "European Union", "currency_code": "EUR", "currency_name": "Euro"},
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
    "conversion_enabled": False,
    "basis": "The supplied LendingClub loan_amnt, annual_inc, and revol_bal training features are USD-denominated.",
}


def options_public() -> dict:
    return {
        "supported_markets": SUPPORTED_MARKETS,
        "unsupported_markets": UNSUPPORTED_MARKETS,
        "supported_locales": SUPPORTED_LOCALES,
        "model_financial_scope": MODEL_FINANCIAL_SCOPE,
    }
