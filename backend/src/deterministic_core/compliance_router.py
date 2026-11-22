"""Deterministic RBI PA-CB, GIFT City IFSCA PSP, and Oct 2026 EDF/EDPMS routing engine.

Enforces RBI Master Direction on Cross-Border Payment Aggregators (PA-CB) and IFSCA regulations:
- PA-CB transaction hard cap: ₹25,00,000 (250,000,000 paise).
- Strict prohibition of inward/outward netting.
- 1:1 ECA (Export Collection Account) to currency isolation.
- Route >₹25 lakh or foreign retention/investing to GIFT_CITY_IFSCA_PSP.
- Oct 2026 EDF/EDPMS realization tracking:
  * 270 days (9 months) for FCY, 365 days (12 months) for INR.
  * Invoices <= ₹10,00,000 eligible for quarterly bulk self-declaration.
  * Overdue > 365 days restricted to advance or LC settlement only.
  * MoneySaver export accounts into ICICI Bank require pre-capture EDF.
"""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt

# Threshold constants in paise
PACB_MAX_TX_PAISE: int = 250_000_000         # ₹25,00,000 in paise
EDF_SELF_DECL_MAX_PAISE: int = 100_000_000    # ₹10,00,000 in paise

# Realization window constants in days
FCY_REALIZATION_DEADLINE_DAYS: int = 270      # 9 months
INR_REALIZATION_DEADLINE_DAYS: int = 365      # 12 months
MAX_EXPORT_CREDIT_DAYS: int = 365             # 1 year hard cap before LC/advance only

DirectionType = Literal["INWARD", "OUTWARD"]
RailType = Literal["PA_CB_INWARD", "PA_CB_OUTWARD", "GIFT_CITY_IFSCA_PSP"]


class RailRouteResult(BaseModel):
    """Result of cross-border payment rail routing."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ROUTED", "COMPLIANCE_BLOCKED"]
    rail: RailType | None = None
    reason: str | None = None


class EdfEdpmsResult(BaseModel):
    """Result of EDF/EDPMS export compliance evaluation."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "BLOCKED_PRE_CAPTURE_EDF_REQUIRED",
        "OVERDUE_ADVANCE_OR_LC_ONLY",
        "OVERDUE_REALIZATION_BREACH",
        "QUARTERLY_SELF_DECLARATION_ELIGIBLE",
        "MONTHLY_EDF_REQUIRED",
    ]
    deadline_days: int
    days_since_invoice: int
    reason: str | None = None


def route_cross_border_rail(
    amount_inr_paise: int,
    direction: DirectionType,
    currency: str,
    eca_currency: str | None,
    netting_requested: bool,
    foreign_retention_or_investing: bool,
) -> RailRouteResult:
    """Route a cross-border transaction to PA-CB or GIFT City IFSCA PSP.

    Args:
        amount_inr_paise: Transaction amount in INR paise (strict int >= 0).
        direction: "INWARD" (export) or "OUTWARD" (import/remittance).
        currency: 3-letter currency code of transaction (e.g. "USD", "INR").
        eca_currency: Currency of the Export Collection Account (if configured).
        netting_requested: Whether merchant requested netting of inward and outward flows.
        foreign_retention_or_investing: Merchant wishes to retain FCY abroad or invest globally.

    Returns:
        RailRouteResult indicating ROUTED with target rail, or COMPLIANCE_BLOCKED with reason.
    """
    if isinstance(amount_inr_paise, bool) or not isinstance(amount_inr_paise, int):
        raise TypeError(f"amount_inr_paise must be a strict int, got {type(amount_inr_paise).__name__}")
    if amount_inr_paise < 0:
        raise ValueError(f"amount_inr_paise must be >= 0, got {amount_inr_paise}")

    if direction not in ("INWARD", "OUTWARD"):
        raise ValueError(f"Invalid direction: {direction}")

    # Rule 1: RBI PA-CB strictly prohibits netting between inward and outward payments
    if netting_requested:
        return RailRouteResult(
            status="COMPLIANCE_BLOCKED",
            rail=None,
            reason="PACB_NO_NETTING_VIOLATION",
        )

    # Rule 2: 1:1 ECA to currency isolation (inward only)
    if direction == "INWARD" and eca_currency is not None:
        if eca_currency.strip().upper() != currency.strip().upper():
            return RailRouteResult(
                status="COMPLIANCE_BLOCKED",
                rail=None,
                reason="ECA_CURRENCY_MISMATCH",
            )

    # Rule 3: High-ticket (>₹25L) or foreign retention / investing must route to GIFT City
    if foreign_retention_or_investing or amount_inr_paise > PACB_MAX_TX_PAISE:
        return RailRouteResult(
            status="ROUTED",
            rail="GIFT_CITY_IFSCA_PSP",
            reason=None,
        )

    # Rule 4: Normal transactions under PA-CB cap
    target_rail: RailType = "PA_CB_INWARD" if direction == "INWARD" else "PA_CB_OUTWARD"
    return RailRouteResult(
        status="ROUTED",
        rail=target_rail,
        reason=None,
    )


def evaluate_edf_edpms(
    amount_inr_paise: int,
    settlement_currency: str,
    destination_bank: str,
    channel: str,
    edf_number: str | None,
    days_since_invoice: int,
) -> EdfEdpmsResult:
    """Evaluate RBI Export Declaration Form (EDF) and EDPMS compliance status.

    Args:
        amount_inr_paise: Invoice amount in INR paise (strict int >= 0).
        settlement_currency: Currency of settlement ("INR", "USD", etc.).
        destination_bank: Receiving AD Bank name (e.g. "ICICI", "HDFC").
        channel: Payment channel (e.g. "MONEYSAVER_EXPORT", "INTERNATIONAL_CARD").
        edf_number: Export Declaration Form identifier (if captured).
        days_since_invoice: Number of days elapsed since export invoice / shipment.

    Returns:
        EdfEdpmsResult with regulatory status and realization deadlines.
    """
    if isinstance(amount_inr_paise, bool) or not isinstance(amount_inr_paise, int):
        raise TypeError(f"amount_inr_paise must be a strict int, got {type(amount_inr_paise).__name__}")
    if amount_inr_paise < 0:
        raise ValueError(f"amount_inr_paise must be >= 0, got {amount_inr_paise}")

    if isinstance(days_since_invoice, bool) or not isinstance(days_since_invoice, int):
        raise TypeError(f"days_since_invoice must be a strict int, got {type(days_since_invoice).__name__}")
    if days_since_invoice < 0:
        raise ValueError(f"days_since_invoice must be >= 0, got {days_since_invoice}")

    # Rule 1: ICICI MoneySaver Export requires pre-capture EDF number
    has_edf = bool(edf_number and edf_number.strip())
    if channel == "MONEYSAVER_EXPORT" and destination_bank.strip().upper() == "ICICI" and not has_edf:
        deadline = (
            FCY_REALIZATION_DEADLINE_DAYS
            if settlement_currency.strip().upper() != "INR"
            else INR_REALIZATION_DEADLINE_DAYS
        )
        return EdfEdpmsResult(
            status="BLOCKED_PRE_CAPTURE_EDF_REQUIRED",
            deadline_days=deadline,
            days_since_invoice=days_since_invoice,
            reason="MoneySaver export payout to ICICI Bank requires pre-capture EDF number",
        )

    # Realization deadline: 270 days for FCY, 365 days for INR
    deadline_days = (
        FCY_REALIZATION_DEADLINE_DAYS
        if settlement_currency.strip().upper() != "INR"
        else INR_REALIZATION_DEADLINE_DAYS
    )

    # Rule 2: Overdue beyond 365 days -> restricted to Advance / LC only
    if days_since_invoice > MAX_EXPORT_CREDIT_DAYS:
        return EdfEdpmsResult(
            status="OVERDUE_ADVANCE_OR_LC_ONLY",
            deadline_days=deadline_days,
            days_since_invoice=days_since_invoice,
            reason="Export proceeds overdue beyond 365 days; settlement restricted to advance or LC only",
        )

    # Rule 3: Overdue beyond currency realization window (e.g. 270 days for FCY)
    if days_since_invoice > deadline_days:
        return EdfEdpmsResult(
            status="OVERDUE_REALIZATION_BREACH",
            deadline_days=deadline_days,
            days_since_invoice=days_since_invoice,
            reason=f"Export realization overdue beyond {deadline_days} days deadline",
        )

    # Rule 4: Small-value exports (<= ₹10 Lakh) eligible for quarterly bulk self-declaration
    if amount_inr_paise <= EDF_SELF_DECL_MAX_PAISE:
        return EdfEdpmsResult(
            status="QUARTERLY_SELF_DECLARATION_ELIGIBLE",
            deadline_days=deadline_days,
            days_since_invoice=days_since_invoice,
            reason=None,
        )

    # Rule 5: Standard export (> ₹10 Lakh) requires regular monthly individual EDF filing
    return EdfEdpmsResult(
        status="MONTHLY_EDF_REQUIRED",
        deadline_days=deadline_days,
        days_since_invoice=days_since_invoice,
        reason=None,
    )
