"""Deterministic FX & Fee Separator.

Implements exact Razorpay pricing rules for cross-border settlements:
1. INTERNATIONAL_CARD: 3% fee + 18% GST on fee (3.54% total deduction).
2. MONEYSAVER_EXPORT:
   - For gross < ₹1,00,000 (10,000,000 paise): flat ₹1,000 fee (100,000 paise) + ₹180 GST (18,000 paise).
   - For gross >= ₹1,00,000: 1% fee + 18% GST on fee (1.18% total deduction).

Decimal with ROUND_HALF_EVEN is used ONLY for intermediate rate/GST quantization,
returning strictly integer paise. Zero float arithmetic.
"""

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Literal

from backend.src.deterministic_core.schemas import FxFeeBreakdown, PaymentChannel

# Rate constants (Decimal only for percentage quantization)
CARD_FEE_RATE = Decimal("0.03")
MONEYSAVER_FEE_RATE = Decimal("0.01")
GST_RATE = Decimal("0.18")
ONE_PAISE = Decimal("1")

# MoneySaver flat fee tier (< ₹1,00,000 in paise)
MONEYSAVER_THRESHOLD_PAISE = 10_000_000
MONEYSAVER_FLAT_FEE_PAISE = 100_000      # ₹1,000 in paise
MONEYSAVER_FLAT_GST_PAISE = 18_000       # ₹180 in paise (18% of ₹1,000)


def _quantize_paise(amount_dec: Decimal) -> int:
    """Quantize a Decimal amount to nearest whole paisa using banker's rounding."""
    return int(amount_dec.quantize(ONE_PAISE, rounding=ROUND_HALF_EVEN))


def compute_fx_fee_breakdown(
    gross_base_paise: int,
    channel: PaymentChannel,
    actual_bank_credit_paise: int,
) -> FxFeeBreakdown:
    """Compute deterministic fee, 18% GST, net credit, and residue against actual bank credit.

    Args:
        gross_base_paise: Total gross base transaction amount in INR paise (strict int >= 0).
        channel: PaymentChannel enum (INTERNATIONAL_CARD or MONEYSAVER_EXPORT).
        actual_bank_credit_paise: Actual amount credited by bank in INR paise (strict int >= 0).

    Returns:
        FxFeeBreakdown with integer paise breakdown and EXACT_MATCH / EXCEPTION_OPEN status.

    Raises:
        TypeError: If monetary arguments are not strict integers or channel is invalid.
        ValueError: If monetary amounts are negative.
    """
    # Strict integer validation before calculation
    if isinstance(gross_base_paise, bool) or not isinstance(gross_base_paise, int):
        raise TypeError(
            f"gross_base_paise must be a strict int, got {type(gross_base_paise).__name__}"
        )
    if gross_base_paise < 0:
        raise ValueError(f"gross_base_paise must be >= 0, got {gross_base_paise}")

    if isinstance(actual_bank_credit_paise, bool) or not isinstance(actual_bank_credit_paise, int):
        raise TypeError(
            f"actual_bank_credit_paise must be a strict int, got {type(actual_bank_credit_paise).__name__}"
        )
    if actual_bank_credit_paise < 0:
        raise ValueError(f"actual_bank_credit_paise must be >= 0, got {actual_bank_credit_paise}")

    if isinstance(channel, str):
        try:
            channel = PaymentChannel(channel)
        except ValueError as err:
            raise ValueError(f"Invalid payment channel: {channel}") from err
    elif not isinstance(channel, PaymentChannel):
        raise TypeError(f"channel must be a PaymentChannel, got {type(channel).__name__}")

    if channel == PaymentChannel.INTERNATIONAL_CARD:
        fee_dec = Decimal(gross_base_paise) * CARD_FEE_RATE
        expected_fee_paise = _quantize_paise(fee_dec)
        gst_dec = Decimal(expected_fee_paise) * GST_RATE
        expected_gst_paise = _quantize_paise(gst_dec)

    elif channel == PaymentChannel.MONEYSAVER_EXPORT:
        if gross_base_paise < MONEYSAVER_THRESHOLD_PAISE:
            expected_fee_paise = MONEYSAVER_FLAT_FEE_PAISE
            expected_gst_paise = MONEYSAVER_FLAT_GST_PAISE
        else:
            fee_dec = Decimal(gross_base_paise) * MONEYSAVER_FEE_RATE
            expected_fee_paise = _quantize_paise(fee_dec)
            gst_dec = Decimal(expected_fee_paise) * GST_RATE
            expected_gst_paise = _quantize_paise(gst_dec)

    expected_net_credit_paise = gross_base_paise - expected_fee_paise - expected_gst_paise
    residue_paise = expected_net_credit_paise - actual_bank_credit_paise

    status: Literal["EXACT_MATCH", "EXCEPTION_OPEN"] = (
        "EXACT_MATCH" if residue_paise == 0 else "EXCEPTION_OPEN"
    )

    return FxFeeBreakdown(
        gross_base_paise=gross_base_paise,
        expected_fee_paise=expected_fee_paise,
        expected_gst_paise=expected_gst_paise,
        expected_net_credit_paise=expected_net_credit_paise,
        actual_bank_credit_paise=actual_bank_credit_paise,
        residue_paise=residue_paise,
        status=status,
    )
