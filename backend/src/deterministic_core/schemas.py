"""Deterministic Core Schemas for Cross-Border Settlement & Recon.

Enforces G1 invariant (Conservation of Value):
- All monetary amounts are strict 64-bit integer subunits (paise for INR, cents for foreign currencies).
- Zero floats, decimals, negative amounts, NaNs, or Infinities in monetary fields.
"""

from enum import Enum
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class PaymentChannel(str, Enum):
    """Payment channels supported for cross-border transactions."""

    INTERNATIONAL_CARD = "INTERNATIONAL_CARD"
    MONEYSAVER_EXPORT = "MONEYSAVER_EXPORT"


NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]


class ReconRow(BaseModel):
    """Model matching Razorpay's /v1/settlements/recon/combined record."""

    model_config = ConfigDict(extra="forbid")

    entity_id: Annotated[str, Field(min_length=1, description="Razorpay entity ID")]
    type: Literal["payment", "refund", "transfer", "adjustment"]
    channel: PaymentChannel
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="3-letter uppercase ISO code")]
    amount: NonNegativeStrictInt = Field(description="Foreign currency subunit (cents), >= 0")
    base_amount: NonNegativeStrictInt = Field(description="INR subunit (paise), >= 0")
    fee: NonNegativeStrictInt = Field(description="Fee in INR paise, >= 0")
    tax: NonNegativeStrictInt = Field(description="GST/Tax in INR paise, >= 0")
    credit: NonNegativeStrictInt = Field(description="Credit in INR paise, >= 0")
    debit: NonNegativeStrictInt = Field(description="Debit in INR paise, >= 0")
    on_hold: bool = False
    settlement_utr: str | None = None

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("entity_id must not be empty or blank")
        return v


class FxFeeBreakdown(BaseModel):
    """Deterministic breakdown of gross base amount, expected fees, GST, and residue."""

    model_config = ConfigDict(extra="forbid")

    gross_base_paise: NonNegativeStrictInt = Field(description="Gross transaction amount in INR paise")
    expected_fee_paise: NonNegativeStrictInt = Field(description="Deterministic fee in INR paise")
    expected_gst_paise: NonNegativeStrictInt = Field(description="Deterministic 18% GST on fee in INR paise")
    expected_net_credit_paise: NonNegativeStrictInt = Field(
        description="Expected net bank credit = gross - fee - gst"
    )
    actual_bank_credit_paise: NonNegativeStrictInt = Field(
        description="Actual amount credited to bank in INR paise"
    )
    residue_paise: StrictInt = Field(
        description="Discrepancy = expected_net_credit_paise - actual_bank_credit_paise"
    )
    status: Literal["EXACT_MATCH", "EXCEPTION_OPEN"]

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str, info) -> str:
        data = info.data
        if "residue_paise" in data:
            expected_status = "EXACT_MATCH" if data["residue_paise"] == 0 else "EXCEPTION_OPEN"
            if v != expected_status:
                raise ValueError(
                    f"status must be '{expected_status}' when residue_paise is {data['residue_paise']}"
                )
        return v
