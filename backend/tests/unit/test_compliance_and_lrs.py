"""Unit and boundary tests for compliance routing and Section 394(1) LRS TCS calculation.

Verifies:
1. PA-CB boundaries:
   - ₹24,99,999.99 (249_999_999 paise) -> PA_CB_INWARD
   - ₹25,00,000.00 (250_000_000 paise) -> PA_CB_INWARD
   - ₹25,00,000.01 (250_000_001 paise) -> GIFT_CITY_IFSCA_PSP
2. Netting violation and ECA currency mismatch -> COMPLIANCE_BLOCKED.
3. ICICI MoneySaver missing edf_number -> BLOCKED_PRE_CAPTURE_EDF_REQUIRED;
   ₹10L invoice -> QUARTERLY_SELF_DECLARATION_ELIGIBLE;
   366 days overdue -> OVERDUE_ADVANCE_OR_LC_ONLY.
4. LRS TCS: ₹8L prior + ₹7L current GENERAL_INVESTMENT -> ₹2L at 0% + ₹5L at 20% = 10_000_000 paise (₹1,00,000).
5. EDU_MED_SELF crossing ₹10L (₹8L prior + ₹7L current) -> 2% on ₹5L = 1_000_000 paise (₹10,000).
6. OVERSEAS_TOUR ₹5L with ₹0 prior -> flat 2% = 1_000_000 paise (₹10,000).
7. EDU_LOAN_EXEMPT ₹15L -> 0 paise;
   NRI_NRO ₹50L -> 0 paise (EXEMPT_NRI_NRO);
   RESIDENT_IND exceeding USD 250,000 -> COMPLIANCE_BLOCKED.
"""

import sys
from pathlib import Path
import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.src.deterministic_core.compliance_router import (
    PACB_MAX_TX_PAISE,
    EDF_SELF_DECL_MAX_PAISE,
    evaluate_edf_edpms,
    route_cross_border_rail,
)
from backend.src.deterministic_core.lrs_tcs import (
    LRS_USD_CAP_CENTS,
    NRI_NRO_USD_CAP_CENTS,
    LRS_TCS_THRESHOLD_PAISE,
    compute_lrs_tcs,
)


class TestComplianceRouterBoundaries:
    """Test suite covering PA-CB caps, netting, ECA, and EDF/EDPMS rules."""

    def test_pacb_boundary_limits(self) -> None:
        """₹24,99,999.99 and ₹25,00,000.00 route to PA_CB_INWARD; ₹25,00,000.01 routes to GIFT City."""
        # 1 paisa below ₹25L cap
        res_below = route_cross_border_rail(
            amount_inr_paise=249_999_999,
            direction="INWARD",
            currency="USD",
            eca_currency="USD",
            netting_requested=False,
            foreign_retention_or_investing=False,
        )
        assert res_below.status == "ROUTED"
        assert res_below.rail == "PA_CB_INWARD"

        # Exactly at ₹25L cap (250_000_000 paise)
        res_exact = route_cross_border_rail(
            amount_inr_paise=250_000_000,
            direction="INWARD",
            currency="USD",
            eca_currency="USD",
            netting_requested=False,
            foreign_retention_or_investing=False,
        )
        assert res_exact.status == "ROUTED"
        assert res_exact.rail == "PA_CB_INWARD"

        # 1 paisa above ₹25L cap -> routes to GIFT City IFSCA PSP
        res_above = route_cross_border_rail(
            amount_inr_paise=250_000_001,
            direction="INWARD",
            currency="USD",
            eca_currency="USD",
            netting_requested=False,
            foreign_retention_or_investing=False,
        )
        assert res_above.status == "ROUTED"
        assert res_above.rail == "GIFT_CITY_IFSCA_PSP"

    def test_outward_pacb_routing(self) -> None:
        """Outward payments under cap route to PA_CB_OUTWARD."""
        res_out = route_cross_border_rail(
            amount_inr_paise=50_000_000,
            direction="OUTWARD",
            currency="USD",
            eca_currency=None,
            netting_requested=False,
            foreign_retention_or_investing=False,
        )
        assert res_out.status == "ROUTED"
        assert res_out.rail == "PA_CB_OUTWARD"

    def test_foreign_retention_always_routes_to_gift_city(self) -> None:
        """Even small tickets route to GIFT City if foreign retention or investing is requested."""
        res = route_cross_border_rail(
            amount_inr_paise=10_000_000,  # ₹1L
            direction="INWARD",
            currency="USD",
            eca_currency="USD",
            netting_requested=False,
            foreign_retention_or_investing=True,
        )
        assert res.status == "ROUTED"
        assert res.rail == "GIFT_CITY_IFSCA_PSP"

    def test_netting_violation_blocked(self) -> None:
        """Netting request is blocked under PA-CB regulations."""
        res = route_cross_border_rail(
            amount_inr_paise=10_000_000,
            direction="INWARD",
            currency="USD",
            eca_currency="USD",
            netting_requested=True,
            foreign_retention_or_investing=False,
        )
        assert res.status == "COMPLIANCE_BLOCKED"
        assert res.rail is None
        assert res.reason == "PACB_NO_NETTING_VIOLATION"

    def test_eca_currency_mismatch_blocked(self) -> None:
        """ECA currency mismatch (e.g. ECA EUR for USD payment) is blocked."""
        res = route_cross_border_rail(
            amount_inr_paise=10_000_000,
            direction="INWARD",
            currency="USD",
            eca_currency="EUR",
            netting_requested=False,
            foreign_retention_or_investing=False,
        )
        assert res.status == "COMPLIANCE_BLOCKED"
        assert res.rail is None
        assert res.reason == "ECA_CURRENCY_MISMATCH"

    def test_edf_icici_moneysaver_missing_edf(self) -> None:
        """ICICI MoneySaver export missing edf_number is blocked pre-capture."""
        res = evaluate_edf_edpms(
            amount_inr_paise=50_000_000,
            settlement_currency="USD",
            destination_bank="ICICI",
            channel="MONEYSAVER_EXPORT",
            edf_number=None,
            days_since_invoice=10,
        )
        assert res.status == "BLOCKED_PRE_CAPTURE_EDF_REQUIRED"
        assert res.deadline_days == 270

        # Blank or whitespace-only edf is also blocked
        res_blank = evaluate_edf_edpms(
            amount_inr_paise=50_000_000,
            settlement_currency="USD",
            destination_bank="icici",
            channel="MONEYSAVER_EXPORT",
            edf_number="   ",
            days_since_invoice=10,
        )
        assert res_blank.status == "BLOCKED_PRE_CAPTURE_EDF_REQUIRED"

    def test_edf_icici_moneysaver_with_valid_edf(self) -> None:
        """ICICI MoneySaver with valid edf_number proceeds normally."""
        res = evaluate_edf_edpms(
            amount_inr_paise=50_000_000,
            settlement_currency="USD",
            destination_bank="ICICI",
            channel="MONEYSAVER_EXPORT",
            edf_number="EDF-2026-998811",
            days_since_invoice=10,
        )
        assert res.status == "QUARTERLY_SELF_DECLARATION_ELIGIBLE"

    def test_edf_self_declaration_thresholds(self) -> None:
        """Invoice <= ₹10L is quarterly bulk eligible; > ₹10L requires monthly individual EDF."""
        # ₹10L exactly (100,000,000 paise)
        res_exact_10l = evaluate_edf_edpms(
            amount_inr_paise=100_000_000,
            settlement_currency="USD",
            destination_bank="HDFC",
            channel="INTERNATIONAL_CARD",
            edf_number=None,
            days_since_invoice=30,
        )
        assert res_exact_10l.status == "QUARTERLY_SELF_DECLARATION_ELIGIBLE"

        # ₹10L + 1 paisa (100,000,001 paise)
        res_above_10l = evaluate_edf_edpms(
            amount_inr_paise=100_000_001,
            settlement_currency="USD",
            destination_bank="HDFC",
            channel="INTERNATIONAL_CARD",
            edf_number=None,
            days_since_invoice=30,
        )
        assert res_above_10l.status == "MONTHLY_EDF_REQUIRED"

    def test_edf_overdue_deadlines(self) -> None:
        """FCY 270 days deadline vs 365 days hard cap."""
        # FCY 280 days (> 270, <= 365) -> OVERDUE_REALIZATION_BREACH
        res_fcy_overdue = evaluate_edf_edpms(
            amount_inr_paise=50_000_000,
            settlement_currency="USD",
            destination_bank="AXIS",
            channel="INTERNATIONAL_CARD",
            edf_number=None,
            days_since_invoice=280,
        )
        assert res_fcy_overdue.status == "OVERDUE_REALIZATION_BREACH"
        assert res_fcy_overdue.deadline_days == 270

        # INR at 280 days (<= 365) -> Not overdue
        res_inr_on_time = evaluate_edf_edpms(
            amount_inr_paise=50_000_000,
            settlement_currency="INR",
            destination_bank="AXIS",
            channel="INTERNATIONAL_CARD",
            edf_number=None,
            days_since_invoice=280,
        )
        assert res_inr_on_time.status == "QUARTERLY_SELF_DECLARATION_ELIGIBLE"
        assert res_inr_on_time.deadline_days == 365

        # 366 days overdue (> 365 days) -> OVERDUE_ADVANCE_OR_LC_ONLY
        res_critical = evaluate_edf_edpms(
            amount_inr_paise=50_000_000,
            settlement_currency="USD",
            destination_bank="AXIS",
            channel="INTERNATIONAL_CARD",
            edf_number=None,
            days_since_invoice=366,
        )
        assert res_critical.status == "OVERDUE_ADVANCE_OR_LC_ONLY"


class TestLrsTcsCalculations:
    """Test suite covering Section 394(1) LRS TCS slabs, caps, and exemptions."""

    def test_general_investment_crossing_threshold(self) -> None:
        """₹8L prior + ₹7L current GENERAL_INVESTMENT -> ₹2L at 0% + ₹5L at 20% = 10_000_000 paise (₹1,00,000)."""
        prior_inr = 80_000_000    # ₹8,00,000
        current_inr = 70_000_000  # ₹7,00,000
        prior_usd = 96_000_00     # USD 9,600
        current_usd = 84_000_00   # USD 8,400

        res = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=prior_inr,
            prior_fy_usd_cents=prior_usd,
            current_inr_paise=current_inr,
            current_usd_cents=current_usd,
        )
        assert res.status == "TCS_COMPUTED"
        assert res.taxable_amount_paise == 50_000_000  # ₹5,00,000
        assert res.tcs_paise == 10_000_000             # ₹1,00,000
        assert res.tcs_rate_bps == 2000

    def test_edu_med_self_crossing_threshold(self) -> None:
        """EDU_MED_SELF crossing ₹10L (₹8L prior + ₹7L current) -> 2% on ₹5L = 1_000_000 paise (₹10,000)."""
        prior_inr = 80_000_000
        current_inr = 70_000_000
        prior_usd = 96_000_00
        current_usd = 84_000_00

        res = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="EDU_MED_SELF",
            prior_fy_inr_paise=prior_inr,
            prior_fy_usd_cents=prior_usd,
            current_inr_paise=current_inr,
            current_usd_cents=current_usd,
        )
        assert res.status == "TCS_COMPUTED"
        assert res.taxable_amount_paise == 50_000_000
        assert res.tcs_paise == 1_000_000             # ₹10,000
        assert res.tcs_rate_bps == 200

    def test_overseas_tour_flat_two_percent(self) -> None:
        """OVERSEAS_TOUR ₹5L with ₹0 prior -> flat 2% = 1_000_000 paise (₹10,000)."""
        current_inr = 50_000_000  # ₹5,00,000
        current_usd = 60_000_00   # USD 6,000

        res = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="OVERSEAS_TOUR",
            prior_fy_inr_paise=0,
            prior_fy_usd_cents=0,
            current_inr_paise=current_inr,
            current_usd_cents=current_usd,
        )
        assert res.status == "TCS_COMPUTED"
        assert res.taxable_amount_paise == 50_000_000
        assert res.tcs_paise == 1_000_000             # ₹10,000
        assert res.tcs_rate_bps == 200

    def test_edu_loan_exempt(self) -> None:
        """EDU_LOAN_EXEMPT ₹15L -> 0 paise TCS."""
        res = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="EDU_LOAN_EXEMPT",
            prior_fy_inr_paise=0,
            prior_fy_usd_cents=0,
            current_inr_paise=150_000_000,
            current_usd_cents=180_000_00,
        )
        assert res.status == "EXEMPT_EDU_LOAN"
        assert res.tcs_paise == 0
        assert res.taxable_amount_paise == 0

    def test_nri_nro_exemption_and_cap(self) -> None:
        """NRI_NRO ₹50L -> 0 paise (EXEMPT_NRI_NRO); exceeding USD 1,000,000 -> COMPLIANCE_BLOCKED."""
        # ₹50L within USD 1M cap
        res_ok = compute_lrs_tcs(
            residency_status="NRI_NRO",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=0,
            prior_fy_usd_cents=0,
            current_inr_paise=500_000_000,      # ₹50L
            current_usd_cents=60_000_000,       # USD 600,000
        )
        assert res_ok.status == "EXEMPT_NRI_NRO"
        assert res_ok.tcs_paise == 0

        # Exceeding USD 1,000,000 cap (100_000_000 cents)
        res_blocked = compute_lrs_tcs(
            residency_status="NRI_NRO",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=0,
            prior_fy_usd_cents=60_000_000,      # USD 600,000
            current_inr_paise=400_000_000,
            current_usd_cents=40_000_001,       # USD 400,000 + 1 cent -> 1,000,000.01 USD
        )
        assert res_blocked.status == "COMPLIANCE_BLOCKED"
        assert res_blocked.reason == "NRI_NRO_1M_CAP_EXCEEDED"
        assert res_blocked.tcs_paise == 0

    def test_resident_ind_exceeding_250k_usd_cap_blocked(self) -> None:
        """RESIDENT_IND exceeding USD 250,000 cap is blocked."""
        res_blocked = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=150_000_000,
            prior_fy_usd_cents=20_000_000,      # USD 200,000
            current_inr_paise=50_000_000,
            current_usd_cents=5_000_001,        # USD 50,000 + 1 cent -> USD 250,000.01
        )
        assert res_blocked.status == "COMPLIANCE_BLOCKED"
        assert res_blocked.reason == "LRS_250K_USD_CAP_EXCEEDED"
        assert res_blocked.tcs_paise == 0

    def test_corporate_fema_exempt(self) -> None:
        """CORPORATE_FEMA has 0 paise TCS and is exempt from LRS USD cap."""
        res = compute_lrs_tcs(
            residency_status="CORPORATE_FEMA",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=1_000_000_000,
            prior_fy_usd_cents=50_000_000,     # USD 500,000 (above LRS cap, but corporate)
            current_inr_paise=500_000_000,
            current_usd_cents=20_000_000,
        )
        assert res.status == "EXEMPT_CORPORATE_FEMA"
        assert res.tcs_paise == 0

    def test_minor_guardian_subject_to_lrs_cap(self) -> None:
        """MINOR_GUARDIAN follows same rules as RESIDENT_IND and enforces USD 250k cap."""
        res = compute_lrs_tcs(
            residency_status="MINOR_GUARDIAN",
            tcs_category="EDU_MED_SELF",
            prior_fy_inr_paise=0,
            prior_fy_usd_cents=25_000_000,      # exactly at cap
            current_inr_paise=10_000_000,
            current_usd_cents=1,                # 1 cent over cap
        )
        assert res.status == "COMPLIANCE_BLOCKED"
        assert res.reason == "LRS_250K_USD_CAP_EXCEEDED"

    def test_general_investment_entirely_below_threshold(self) -> None:
        """GENERAL_INVESTMENT with cumulative <= ₹10L has 0 paise TCS."""
        res = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=30_000_000,      # ₹3L
            prior_fy_usd_cents=36_000_00,
            current_inr_paise=50_000_000,       # ₹5L -> total ₹8L <= ₹10L
            current_usd_cents=60_000_00,
        )
        assert res.status == "TCS_COMPUTED"
        assert res.taxable_amount_paise == 0
        assert res.tcs_paise == 0

    def test_general_investment_prior_already_exceeded_threshold(self) -> None:
        """GENERAL_INVESTMENT with prior already >= ₹10L taxes 100% of current transaction."""
        res = compute_lrs_tcs(
            residency_status="RESIDENT_IND",
            tcs_category="GENERAL_INVESTMENT",
            prior_fy_inr_paise=120_000_000,     # ₹12L
            prior_fy_usd_cents=144_000_00,
            current_inr_paise=50_000_000,       # ₹5L -> 100% taxable
            current_usd_cents=60_000_00,
        )
        assert res.status == "TCS_COMPUTED"
        assert res.taxable_amount_paise == 50_000_000
        assert res.tcs_paise == 10_000_000      # 20% of ₹5L
        assert res.tcs_rate_bps == 2000

    def test_type_checking_rejections(self) -> None:
        """Strict integer validations reject invalid types and negative amounts."""
        with pytest.raises(TypeError):
            compute_lrs_tcs("RESIDENT_IND", "GENERAL_INVESTMENT", 100.5, 0, 100, 0)  # type: ignore

        with pytest.raises(TypeError):
            compute_lrs_tcs("RESIDENT_IND", "GENERAL_INVESTMENT", 0, True, 100, 0)  # type: ignore

        with pytest.raises(ValueError):
            compute_lrs_tcs("RESIDENT_IND", "GENERAL_INVESTMENT", -1, 0, 100, 0)

        with pytest.raises(ValueError):
            route_cross_border_rail(-1, "INWARD", "USD", "USD", False, False)

        with pytest.raises(TypeError):
            evaluate_edf_edpms(100.5, "USD", "ICICI", "MONEYSAVER_EXPORT", None, 10)  # type: ignore
