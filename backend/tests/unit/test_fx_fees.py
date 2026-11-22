"""Unit tests for deterministic FX fee separation and Pydantic schemas.

Verifies:
1. ₹20,000 (2_000_000 paise): MoneySaver fee+GST = 118_000 paise vs Card = 70_800 paise.
2. ₹33,333.33 (3_333_333 paise) crossover: MoneySaver and Card deductions both equal 118_000 paise.
3. USD 5,000 at ₹95/USD (47_500_000 paise): MoneySaver fee 475_000, GST 85_500, net 46_939_500;
   Card fee 1_425_000, GST 256_500, net 45_818_500.
4. Short bank credit of ₹800 (actual_bank_credit_paise = 46_859_500) produces residue_paise == 80_000
   and status == "EXCEPTION_OPEN".
5. ReconRow rejects floats (e.g. 100.5), negative ints, NaN, Infinity, and lowercase/invalid currency codes.
"""

import math
import sys
from pathlib import Path
import pytest
from pydantic import ValidationError

# Ensure repo root is in python path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.deterministic_core.fx_fees import compute_fx_fee_breakdown
from backend.src.deterministic_core.schemas import FxFeeBreakdown, PaymentChannel, ReconRow


class TestFxFeeCalculations:
    """Test suite verifying exact pricing rules for cross-border settlements."""

    def test_twenty_thousand_rupees_moneysaver_vs_card(self) -> None:
        """₹20,000 (2,000,000 paise): MoneySaver flat fee+GST (118,000) vs Card 3.54% (70,800)."""
        gross_paise = 2_000_000

        # MoneySaver below ₹1,00,000 threshold -> flat ₹1,000 fee + ₹180 GST = ₹1,180
        ms_expected_net = gross_paise - 118_000
        ms_breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.MONEYSAVER_EXPORT,
            actual_bank_credit_paise=ms_expected_net,
        )
        assert ms_breakdown.expected_fee_paise == 100_000
        assert ms_breakdown.expected_gst_paise == 18_000
        assert ms_breakdown.expected_fee_paise + ms_breakdown.expected_gst_paise == 118_000
        assert ms_breakdown.expected_net_credit_paise == 1_882_000
        assert ms_breakdown.actual_bank_credit_paise == 1_882_000
        assert ms_breakdown.residue_paise == 0
        assert ms_breakdown.status == "EXACT_MATCH"

        # Card at ₹20,000 -> 3% fee (60,000) + 18% GST (10,800) = 70,800
        card_expected_net = gross_paise - 70_800
        card_breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.INTERNATIONAL_CARD,
            actual_bank_credit_paise=card_expected_net,
        )
        assert card_breakdown.expected_fee_paise == 60_000
        assert card_breakdown.expected_gst_paise == 10_800
        assert card_breakdown.expected_fee_paise + card_breakdown.expected_gst_paise == 70_800
        assert card_breakdown.expected_net_credit_paise == 1_929_200
        assert card_breakdown.actual_bank_credit_paise == 1_929_200
        assert card_breakdown.residue_paise == 0
        assert card_breakdown.status == "EXACT_MATCH"

    def test_crossover_at_33333_rupees(self) -> None:
        """₹33,333.33 (3,333,333 paise) crossover: MoneySaver and Card deductions both equal 118,000 paise."""
        gross_paise = 3_333_333

        ms_breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.MONEYSAVER_EXPORT,
            actual_bank_credit_paise=gross_paise - 118_000,
        )
        card_breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.INTERNATIONAL_CARD,
            actual_bank_credit_paise=gross_paise - 118_000,
        )

        ms_total_deduction = ms_breakdown.expected_fee_paise + ms_breakdown.expected_gst_paise
        card_total_deduction = card_breakdown.expected_fee_paise + card_breakdown.expected_gst_paise

        assert ms_total_deduction == 118_000
        assert card_total_deduction == 118_000
        assert card_breakdown.expected_fee_paise == 100_000
        assert card_breakdown.expected_gst_paise == 18_000
        assert ms_breakdown.expected_net_credit_paise == card_breakdown.expected_net_credit_paise

    def test_usd_5000_at_95_inr(self) -> None:
        """USD 5,000 at ₹95/USD (47,500,000 paise): MoneySaver vs Card above threshold."""
        gross_paise = 47_500_000

        # MoneySaver: 1% fee (475,000) + 18% GST (85,500) = 560,500 total deduction
        ms_net = 46_939_500
        ms_breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.MONEYSAVER_EXPORT,
            actual_bank_credit_paise=ms_net,
        )
        assert ms_breakdown.expected_fee_paise == 475_000
        assert ms_breakdown.expected_gst_paise == 85_500
        assert ms_breakdown.expected_net_credit_paise == 46_939_500
        assert ms_breakdown.residue_paise == 0
        assert ms_breakdown.status == "EXACT_MATCH"

        # Card: 3% fee (1,425,000) + 18% GST (256,500) = 1,681,500 total deduction
        card_net = 45_818_500
        card_breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.INTERNATIONAL_CARD,
            actual_bank_credit_paise=card_net,
        )
        assert card_breakdown.expected_fee_paise == 1_425_000
        assert card_breakdown.expected_gst_paise == 256_500
        assert card_breakdown.expected_net_credit_paise == 45_818_500
        assert card_breakdown.residue_paise == 0
        assert card_breakdown.status == "EXACT_MATCH"

    def test_short_bank_credit_produces_exception_open(self) -> None:
        """Short credit by ₹800 (80,000 paise) produces residue_paise == 80_000 and EXCEPTION_OPEN."""
        gross_paise = 47_500_000
        # Expected net for MoneySaver is 46_939_500
        # Actual bank credit is short by ₹800 (80_000 paise) -> 46_859_500
        actual_credit_paise = 46_859_500

        breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.MONEYSAVER_EXPORT,
            actual_bank_credit_paise=actual_credit_paise,
        )
        assert breakdown.expected_net_credit_paise == 46_939_500
        assert breakdown.actual_bank_credit_paise == 46_859_500
        assert breakdown.residue_paise == 80_000
        assert breakdown.status == "EXCEPTION_OPEN"

    def test_over_credit_produces_negative_residue_and_exception_open(self) -> None:
        """Over-credit by bank also flags EXCEPTION_OPEN with negative residue."""
        gross_paise = 2_000_000
        # Expected net for Card is 1_929_200; actual bank credit is 1_930_000 (+800 paise)
        actual_credit_paise = 1_930_000

        breakdown = compute_fx_fee_breakdown(
            gross_base_paise=gross_paise,
            channel=PaymentChannel.INTERNATIONAL_CARD,
            actual_bank_credit_paise=actual_credit_paise,
        )
        assert breakdown.residue_paise == -800
        assert breakdown.status == "EXCEPTION_OPEN"


class TestReconRowSchemaValidation:
    """Test suite verifying strict validation and rejection of invalid data in ReconRow."""

    def _valid_recon_data(self) -> dict:
        return {
            "entity_id": "pay_test_12345",
            "type": "payment",
            "channel": PaymentChannel.INTERNATIONAL_CARD,
            "currency": "USD",
            "amount": 500000,
            "base_amount": 47500000,
            "fee": 1425000,
            "tax": 256500,
            "credit": 45818500,
            "debit": 0,
            "on_hold": False,
            "settlement_utr": "UTR123456789",
        }

    def test_valid_recon_row(self) -> None:
        """Valid ReconRow passes validation."""
        row = ReconRow(**self._valid_recon_data())
        assert row.entity_id == "pay_test_12345"
        assert row.currency == "USD"
        assert row.amount == 500000
        assert row.base_amount == 47500000

    @pytest.mark.parametrize(
        "field_name",
        ["amount", "base_amount", "fee", "tax", "credit", "debit"],
    )
    def test_recon_row_rejects_floats(self, field_name: str) -> None:
        """ReconRow strictly rejects float values for monetary fields."""
        data = self._valid_recon_data()
        data[field_name] = 100.5
        with pytest.raises(ValidationError):
            ReconRow(**data)

    @pytest.mark.parametrize(
        "field_name",
        ["amount", "base_amount", "fee", "tax", "credit", "debit"],
    )
    def test_recon_row_rejects_negative_ints(self, field_name: str) -> None:
        """ReconRow strictly rejects negative integers for monetary fields."""
        data = self._valid_recon_data()
        data[field_name] = -1
        with pytest.raises(ValidationError):
            ReconRow(**data)

    @pytest.mark.parametrize(
        "field_name",
        ["amount", "base_amount", "fee", "tax", "credit", "debit"],
    )
    def test_recon_row_rejects_nan(self, field_name: str) -> None:
        """ReconRow strictly rejects NaN for monetary fields."""
        data = self._valid_recon_data()
        data[field_name] = float("nan")
        with pytest.raises(ValidationError):
            ReconRow(**data)

    @pytest.mark.parametrize(
        "field_name",
        ["amount", "base_amount", "fee", "tax", "credit", "debit"],
    )
    def test_recon_row_rejects_infinity(self, field_name: str) -> None:
        """ReconRow strictly rejects Infinity for monetary fields."""
        data = self._valid_recon_data()
        data[field_name] = float("inf")
        with pytest.raises(ValidationError):
            ReconRow(**data)

    @pytest.mark.parametrize(
        "field_name",
        ["amount", "base_amount", "fee", "tax", "credit", "debit"],
    )
    def test_recon_row_rejects_bool_for_int(self, field_name: str) -> None:
        """ReconRow strictly rejects bool (True/False) for monetary integer fields."""
        data = self._valid_recon_data()
        data[field_name] = True
        with pytest.raises(ValidationError):
            ReconRow(**data)

    @pytest.mark.parametrize(
        "invalid_currency",
        ["usd", "eur", "US", "USDT", "123", "", "US$", "us-east"],
    )
    def test_recon_row_rejects_invalid_currency(self, invalid_currency: str) -> None:
        """ReconRow strictly rejects lowercase, non-3-letter, or non-alphabetical currency codes."""
        data = self._valid_recon_data()
        data["currency"] = invalid_currency
        with pytest.raises(ValidationError):
            ReconRow(**data)

    @pytest.mark.parametrize("valid_currency", ["USD", "EUR", "GBP", "SGD", "INR", "AED", "JPY"])
    def test_recon_row_accepts_valid_currencies(self, valid_currency: str) -> None:
        """ReconRow accepts valid 3-letter uppercase ISO currency codes."""
        data = self._valid_recon_data()
        data["currency"] = valid_currency
        row = ReconRow(**data)
        assert row.currency == valid_currency

    @pytest.mark.parametrize("blank_id", ["", "   ", "\t\n"])
    def test_recon_row_rejects_empty_entity_id(self, blank_id: str) -> None:
        """ReconRow strictly rejects empty or whitespace-only entity_id."""
        data = self._valid_recon_data()
        data["entity_id"] = blank_id
        with pytest.raises(ValidationError):
            ReconRow(**data)


class TestFxFeeBreakdownSchemaValidation:
    """Test suite verifying FxFeeBreakdown schema validation."""

    def test_breakdown_rejects_status_mismatch(self) -> None:
        """FxFeeBreakdown rejects EXACT_MATCH if residue != 0."""
        with pytest.raises(ValidationError):
            FxFeeBreakdown(
                gross_base_paise=1000,
                expected_fee_paise=30,
                expected_gst_paise=5,
                expected_net_credit_paise=965,
                actual_bank_credit_paise=900,
                residue_paise=65,
                status="EXACT_MATCH",  # Inconsistent with residue=65
            )

    def test_breakdown_rejects_exception_open_on_zero_residue(self) -> None:
        """FxFeeBreakdown rejects EXCEPTION_OPEN if residue == 0."""
        with pytest.raises(ValidationError):
            FxFeeBreakdown(
                gross_base_paise=1000,
                expected_fee_paise=30,
                expected_gst_paise=5,
                expected_net_credit_paise=965,
                actual_bank_credit_paise=965,
                residue_paise=0,
                status="EXCEPTION_OPEN",  # Inconsistent with residue=0
            )

    def test_compute_fx_fee_breakdown_rejects_floats_and_invalid_types(self) -> None:
        """compute_fx_fee_breakdown function level type checking."""
        with pytest.raises(TypeError):
            compute_fx_fee_breakdown(100.5, PaymentChannel.INTERNATIONAL_CARD, 95)  # type: ignore

        with pytest.raises(TypeError):
            compute_fx_fee_breakdown(100, PaymentChannel.INTERNATIONAL_CARD, 95.5)  # type: ignore

        with pytest.raises(ValueError):
            compute_fx_fee_breakdown(-100, PaymentChannel.INTERNATIONAL_CARD, 95)

        with pytest.raises(ValueError):
            compute_fx_fee_breakdown(100, PaymentChannel.INTERNATIONAL_CARD, -95)

        with pytest.raises(ValueError):
            compute_fx_fee_breakdown(100, "INVALID_CHANNEL", 95)  # type: ignore
