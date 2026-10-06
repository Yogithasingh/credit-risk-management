from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegisterInput(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if any(ord(character) < 32 for character in value):
            raise ValueError("Name contains unsupported characters.")
        return value

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@") or any(c.isspace() for c in value):
            raise ValueError("Enter a valid email address.")
        local, _, domain = value.partition("@")
        if "." not in domain or len(local) > 64:
            raise ValueError("Enter a valid email address.")
        return value


class LoginInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loan_amount: float = Field(gt=0, le=1_000_000)
    term_months: Literal[36, 60]
    annual_income: float = Field(gt=0, le=100_000_000)
    home_ownership: Literal["MORTGAGE", "RENT", "OWN"]
    employment_length: Literal[
        "< 1 year", "1 year", "2 years", "3 years", "4 years", "5 years",
        "6 years", "7 years", "8 years", "9 years", "10+ years",
    ] | None = None
    purpose: Literal[
        "debt_consolidation",
        "credit_card",
        "home_improvement",
        "other",
        "major_purchase",
        "car",
        "small_business",
        "house",
        "moving",
        "vacation",
        "medical",
    ]
    dti: float = Field(ge=0, le=200)
    prior_delinquencies: int = Field(ge=0, le=100)
    fico_score: float = Field(ge=300, le=850)
    recent_credit_inquiries: int = Field(ge=0, le=100)
    open_accounts: int = Field(ge=0, le=500)
    public_records: int = Field(ge=0, le=100)
    revolving_balance: float = Field(ge=0, le=100_000_000)
    revolving_utilization: float = Field(ge=0, le=300)
    total_accounts: int = Field(ge=0, le=1000)


class DecisionInput(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "NEEDS_MORE_INFORMATION"]
    notes: str = Field(default="", max_length=3000)

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: str) -> str:
        return value.strip()


class RiskThresholdInput(BaseModel):
    low_risk_maximum: float = Field(gt=0.0, lt=1.0)
    medium_risk_maximum: float = Field(gt=0.0, lt=1.0)

    @field_validator("medium_risk_maximum")
    @classmethod
    def medium_above_low(cls, value: float, info):
        low = info.data.get("low_risk_maximum")
        if low is not None and value <= low:
            raise ValueError("Medium risk threshold must be greater than the low risk threshold.")
        return value


class UserRoleInput(BaseModel):
    role: Literal["APPLICANT", "ANALYST"]


class UserActiveInput(BaseModel):
    active: bool
