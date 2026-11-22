"""FY 2026-27 Section 394(1) Income-tax Act, 2025 LRS TCS Calculator.

Implements exact deterministic Liberalised Remittance Scheme (LRS) Tax Collected at Source (TCS) rules:
- Cumulative financial year outward remittance tracking per PAN against USD 250,000 cap.
- ₹10,00,000 (100,000,000 paise) threshold for specified categories.
- Slabs:
  * CORPORATE_FEMA: 0% TCS (exempt, not covered under LRS).
  * NRI_NRO: 0% TCS up to USD 1,000,000 / FY under FEMA Remittance of Assets.
  * EDU_LOAN_EXEMPT: 0% TCS (Section 129 / Section 80E education loan funded).
  * OVERSEAS_TOUR: Flat 2% on entire remittance (no ₹10L threshold).
  * EDU_MED_SELF: 0% up to ₹10L cumulative FY remittance, 2% strictly on portion exceeding ₹10L.
  * GENERAL_INVESTMENT: 0% up to ₹10L cumulative FY remittance, 20% strictly on portion exceeding ₹10L.
- Strict rejection of float arithmetic. Decimal with ROUND_HALF_EVEN used solely for percentage quantization.
"""

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt

# Monetary and statutory cap constants
LRS_USD_CAP_CENTS: int = 25_000_000         # USD 250,000 in cents
NRI_NRO_USD_CAP_CENTS: int = 100_000_000     # USD 1,000,000 in cents
LRS_TCS_THRESHOLD_PAISE: int = 100_000_000   # ₹10,00,000 in paise

# Tax rate constants
RATE_TWO_PERCENT = Decimal("0.02")
RATE_TWENTY_PERCENT = Decimal("0.20")
ONE_PAISE = Decimal("1")

ResidencyStatus = Literal["RESIDENT_IND", "MINOR_GUARDIAN", "NRI_NRO", "CORPORATE_FEMA"]
TcsCategory = Literal[
    "EDU_LOAN_EXEMPT",
    "EDU_MED_SELF",
    "OVERSEAS_TOUR",
    "GENERAL_INVESTMENT",
    "EXEMPT_NON_LRS",
]


class LrsTcsResult(BaseModel):
    """Result of deterministic Section 394(1) LRS TCS computation."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "TCS_COMPUTED",
        "EXEMPT_EDU_LOAN",
        "EXEMPT_CORPORATE_FEMA",
        "EXEMPT_NRI_NRO",
        "EXEMPT_NON_LRS",
        "COMPLIANCE_BLOCKED",
    ]
    tcs_paise: StrictInt = Field(ge=0, description="TCS amount in INR paise, >= 0")
    taxable_amount_paise: StrictInt = Field(ge=0, description="Portion of transaction subject to TCS in INR paise")
    tcs_rate_bps: StrictInt = Field(ge=0, description="Effective TCS rate in basis points (e.g. 200 = 2%, 2000 = 20%)")
    cumulative_usd_post_cents: StrictInt = Field(ge=0, description="Cumulative FY USD outward in cents")
    cumulative_inr_post_paise: StrictInt = Field(ge=0, description="Cumulative FY INR outward in paise")
    reason: str | None = None


def compute_lrs_tcs(
    residency_status: ResidencyStatus,
    tcs_category: TcsCategory,
    prior_fy_inr_paise: int,
    prior_fy_usd_cents: int,
    current_inr_paise: int,
    current_usd_cents: int,
) -> LrsTcsResult:
    """Compute deterministic Section 394(1) LRS TCS for a cross-border remittance.

    Args:
        residency_status: Remitter entity category ("RESIDENT_IND", "MINOR_GUARDIAN", "NRI_NRO", "CORPORATE_FEMA").
        tcs_category: Purpose category ("EDU_LOAN_EXEMPT", "EDU_MED_SELF", "OVERSEAS_TOUR", "GENERAL_INVESTMENT", "EXEMPT_NON_LRS").
        prior_fy_inr_paise: Cumulative outward remittance in INR paise earlier in this FY (>= 0).
        prior_fy_usd_cents: Cumulative outward remittance in USD cents earlier in this FY (>= 0).
        current_inr_paise: Current remittance amount in INR paise (>= 0).
        current_usd_cents: Current remittance amount equivalent in USD cents (>= 0).

    Returns:
        LrsTcsResult with calculated TCS in integer paise, or COMPLIANCE_BLOCKED if caps exceeded.
    """
    # Strict integer type and bounds validation
    for name, val in [
        ("prior_fy_inr_paise", prior_fy_inr_paise),
        ("prior_fy_usd_cents", prior_fy_usd_cents),
        ("current_inr_paise", current_inr_paise),
        ("current_usd_cents", current_usd_cents),
    ]:
        if isinstance(val, bool) or not isinstance(val, int):
            raise TypeError(f"{name} must be a strict int, got {type(val).__name__}")
        if val < 0:
            raise ValueError(f"{name} must be >= 0, got {val}")

    if residency_status not in ("RESIDENT_IND", "MINOR_GUARDIAN", "NRI_NRO", "CORPORATE_FEMA"):
        raise ValueError(f"Invalid residency_status: {residency_status}")

    if tcs_category not in (
        "EDU_LOAN_EXEMPT",
        "EDU_MED_SELF",
        "OVERSEAS_TOUR",
        "GENERAL_INVESTMENT",
        "EXEMPT_NON_LRS",
    ):
        raise ValueError(f"Invalid tcs_category: {tcs_category}")

    cumulative_usd_post = prior_fy_usd_cents + current_usd_cents
    cumulative_inr_post = prior_fy_inr_paise + current_inr_paise

    # 1. Corporate FEMA Remittances: Completely outside LRS scope, 0% TCS
    if residency_status == "CORPORATE_FEMA":
        return LrsTcsResult(
            status="EXEMPT_CORPORATE_FEMA",
            tcs_paise=0,
            taxable_amount_paise=0,
            tcs_rate_bps=0,
            cumulative_usd_post_cents=cumulative_usd_post,
            cumulative_inr_post_paise=cumulative_inr_post,
            reason=None,
        )

    # 2. NRI NRO Remittances: Remittance of Assets under FEMA (USD 1,000,000 / FY limit)
    if residency_status == "NRI_NRO":
        if cumulative_usd_post > NRI_NRO_USD_CAP_CENTS:
            return LrsTcsResult(
                status="COMPLIANCE_BLOCKED",
                tcs_paise=0,
                taxable_amount_paise=0,
                tcs_rate_bps=0,
                cumulative_usd_post_cents=cumulative_usd_post,
                cumulative_inr_post_paise=cumulative_inr_post,
                reason="NRI_NRO_1M_CAP_EXCEEDED",
            )
        return LrsTcsResult(
            status="EXEMPT_NRI_NRO",
            tcs_paise=0,
            taxable_amount_paise=0,
            tcs_rate_bps=0,
            cumulative_usd_post_cents=cumulative_usd_post,
            cumulative_inr_post_paise=cumulative_inr_post,
            reason=None,
        )

    # 3. Resident Individual / Minor through Guardian: Covered under LRS (USD 250,000 cap)
    if cumulative_usd_post > LRS_USD_CAP_CENTS:
        return LrsTcsResult(
            status="COMPLIANCE_BLOCKED",
            tcs_paise=0,
            taxable_amount_paise=0,
            tcs_rate_bps=0,
            cumulative_usd_post_cents=cumulative_usd_post,
            cumulative_inr_post_paise=cumulative_inr_post,
            reason="LRS_250K_USD_CAP_EXCEEDED",
        )

    # 3a. Education loan funded under Section 129 / 80E: 0% TCS
    if tcs_category == "EDU_LOAN_EXEMPT":
        return LrsTcsResult(
            status="EXEMPT_EDU_LOAN",
            tcs_paise=0,
            taxable_amount_paise=0,
            tcs_rate_bps=0,
            cumulative_usd_post_cents=cumulative_usd_post,
            cumulative_inr_post_paise=cumulative_inr_post,
            reason=None,
        )

    # 3b. Non-LRS Exemptions
    if tcs_category == "EXEMPT_NON_LRS":
        return LrsTcsResult(
            status="EXEMPT_NON_LRS",
            tcs_paise=0,
            taxable_amount_paise=0,
            tcs_rate_bps=0,
            cumulative_usd_post_cents=cumulative_usd_post,
            cumulative_inr_post_paise=cumulative_inr_post,
            reason=None,
        )

    # 3c. Overseas Tour Packages: Flat 2% on entire current remittance amount without threshold
    if tcs_category == "OVERSEAS_TOUR":
        taxable_amount_paise = current_inr_paise
        tcs_dec = Decimal(taxable_amount_paise) * RATE_TWO_PERCENT
        tcs_paise = int(tcs_dec.quantize(ONE_PAISE, rounding=ROUND_HALF_EVEN))
        return LrsTcsResult(
            status="TCS_COMPUTED",
            tcs_paise=tcs_paise,
            taxable_amount_paise=taxable_amount_paise,
            tcs_rate_bps=200,
            cumulative_usd_post_cents=cumulative_usd_post,
            cumulative_inr_post_paise=cumulative_inr_post,
            reason=None,
        )

    # 3d & 3e: Threshold categories (₹10,00,000 threshold across cumulative FY remittances)
    # Determine the portion of CURRENT transaction that falls above the ₹10L threshold
    if cumulative_inr_post <= LRS_TCS_THRESHOLD_PAISE:
        taxable_amount_paise = 0
    elif prior_fy_inr_paise >= LRS_TCS_THRESHOLD_PAISE:
        taxable_amount_paise = current_inr_paise
    else:
        # Crosses threshold in this transaction
        taxable_amount_paise = cumulative_inr_post - LRS_TCS_THRESHOLD_PAISE

    if tcs_category == "EDU_MED_SELF":
        rate = RATE_TWO_PERCENT
        rate_bps = 200
    elif tcs_category == "GENERAL_INVESTMENT":
        rate = RATE_TWENTY_PERCENT
        rate_bps = 2000
    else:
        raise ValueError(f"Unhandled category: {tcs_category}")

    if taxable_amount_paise > 0:
        tcs_dec = Decimal(taxable_amount_paise) * rate
        tcs_paise = int(tcs_dec.quantize(ONE_PAISE, rounding=ROUND_HALF_EVEN))
    else:
        tcs_paise = 0

    return LrsTcsResult(
        status="TCS_COMPUTED",
        tcs_paise=tcs_paise,
        taxable_amount_paise=taxable_amount_paise,
        tcs_rate_bps=rate_bps,
        cumulative_usd_post_cents=cumulative_usd_post,
        cumulative_inr_post_paise=cumulative_inr_post,
        reason=None,
    )
