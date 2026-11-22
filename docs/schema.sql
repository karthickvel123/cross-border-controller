-- =============================================================================
-- schema.sql — Cross-Border Multi-Currency Settlement & Compliance Controller
-- PostgreSQL 16 DDL
-- =============================================================================
-- Version:  1.0.0
-- Date:     2026-11-22
-- Track:    Razorpay AI Builder Program — Track 04: AI Finance Controller
--
-- Invariants enforced:
--   G1: All money as BIGINT (paise/cents). Zero floats.
--   G4: Atomic single-use approval via conditional UPDATE + approval_token.
--   G5: Append-only audit_events with SHA-256 hash chain. REVOKE UPDATE/DELETE.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 0. Extensions & Roles
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid(), digest()

-- Application role with restricted privileges on audit table
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role') THEN
        CREATE ROLE app_role NOLOGIN;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 1. ENUM Types
-- ---------------------------------------------------------------------------

CREATE TYPE currency_code AS ENUM (
    'INR', 'USD', 'EUR', 'GBP', 'AED', 'SGD', 'JPY', 'AUD', 'CAD', 'CHF',
    'HKD', 'SAR', 'QAR', 'KWD', 'BHD', 'OMR', 'THB', 'MYR', 'CNY', 'NZD'
);

CREATE TYPE order_status AS ENUM (
    'CREATED', 'AUTHORIZED', 'CAPTURED', 'REFUNDED', 'FAILED'
);

CREATE TYPE recon_status AS ENUM (
    'PENDING', 'MATCHED', 'RESIDUE_NONZERO', 'RECON_ORPHAN',
    'FX_RATE_MISSING', 'EXCEPTION_OPEN'
);

CREATE TYPE settlement_status AS ENUM (
    'CREATED', 'PROCESSED', 'SETTLED', 'FAILED'
);

CREATE TYPE payment_channel AS ENUM (
    'INTERNATIONAL_CARD', 'MONEYSAVER_EXPORT', 'UPI', 'NETBANKING',
    'WALLET', 'EMANDATE', 'NEFT', 'RTGS', 'IMPS'
);

CREATE TYPE compliance_route AS ENUM (
    'PA_CB_STANDARD', 'GIFT_CITY_IFSCA_PSP', 'DOMESTIC'
);

CREATE TYPE compliance_flag AS ENUM (
    'CLEAR', 'MISSING_EDF_PRECAPTURE', 'NETTING_PROHIBITED',
    'EXCEEDS_PA_CB_CAP', 'REALIZATION_OVERDUE', 'LRS_CAP_EXCEEDED',
    'COMPLIANCE_BREACH_BLOCKED'
);

CREATE TYPE exception_status AS ENUM (
    'PENDING', 'PENDING_INVESTIGATION', 'INVESTIGATED',
    'APPROVED_WRITE_OFF', 'APPROVED_ADJUSTMENT', 'APPROVED_ESCALATION',
    'REJECTED', 'ABSTAINED_UNGROUNDED'
);

CREATE TYPE provenance_status AS ENUM (
    'VERIFIED', 'ABSTAINED_UNGROUNDED', 'PENDING_VERIFICATION'
);

CREATE TYPE remitter_type AS ENUM (
    'RESIDENT_INDIVIDUAL', 'NRI_NRO', 'CORPORATE_FEMA'
);

CREATE TYPE lrs_purpose AS ENUM (
    'LOAN_EDUCATION', 'SELF_EDUCATION', 'MEDICAL', 'OVERSEAS_TOUR',
    'GENERAL', 'GIFT_CITY_INVESTMENT'
);

CREATE TYPE audit_action AS ENUM (
    'RECON_MATCHED', 'RECON_RESIDUE', 'RECON_ORPHAN',
    'EXCEPTION_CREATED', 'EXCEPTION_INVESTIGATED', 'EXCEPTION_APPROVED',
    'EXCEPTION_REJECTED', 'EXCEPTION_ABSTAINED',
    'COMPLIANCE_CLASSIFIED', 'COMPLIANCE_FLAGGED',
    'LRS_TCS_CALCULATED', 'LRS_CAP_EXCEEDED',
    'EDF_EDPMS_ENTRY_CREATED', 'EDF_CLOSURE_PACK_GENERATED',
    'SETTLEMENT_RECEIVED', 'WEBHOOK_RECEIVED', 'WEBHOOK_REJECTED',
    'APPROVAL_CONFLICT', 'AUDIT_CHAIN_GENESIS'
);

-- ---------------------------------------------------------------------------
-- 2. Core Financial Tables
-- ---------------------------------------------------------------------------

-- 2.1 Orders (from Razorpay POST /v1/orders)
CREATE TABLE orders (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_order_id     TEXT        NOT NULL UNIQUE,
    razorpay_payment_id   TEXT        UNIQUE,
    merchant_id           TEXT        NOT NULL,
    amount_paise          BIGINT      NOT NULL,
    currency              currency_code NOT NULL,
    base_amount_paise     BIGINT      NOT NULL,
    base_currency         currency_code NOT NULL DEFAULT 'INR',
    forex_rate_numerator  BIGINT,                         -- rational FX rate = num/den
    forex_rate_denominator BIGINT,                        -- avoids float FX rates
    payment_channel       payment_channel NOT NULL,
    status                order_status NOT NULL DEFAULT 'CREATED',
    receipt               TEXT,
    notes                 JSONB       NOT NULL DEFAULT '{}',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- G1: Money must be non-negative integer (paise)
    CONSTRAINT chk_orders_amount_positive
        CHECK (amount_paise > 0),
    CONSTRAINT chk_orders_base_amount_positive
        CHECK (base_amount_paise > 0),
    -- FX rate rational: both present or both null, denominator > 0
    CONSTRAINT chk_orders_forex_rate_rational
        CHECK (
            (forex_rate_numerator IS NULL AND forex_rate_denominator IS NULL)
            OR
            (forex_rate_numerator IS NOT NULL
             AND forex_rate_denominator IS NOT NULL
             AND forex_rate_denominator > 0
             AND forex_rate_numerator > 0)
        )
);

CREATE INDEX idx_orders_merchant     ON orders (merchant_id);
CREATE INDEX idx_orders_status       ON orders (status);
CREATE INDEX idx_orders_created      ON orders (created_at);


-- 2.2 Settlements (from Razorpay settlement lifecycle)
CREATE TABLE settlements (
    id                UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_settlement_id TEXT         NOT NULL UNIQUE,
    utr               TEXT              NOT NULL UNIQUE,
    amount_paise       BIGINT           NOT NULL,
    currency           currency_code    NOT NULL DEFAULT 'INR',
    fees_paise         BIGINT           NOT NULL DEFAULT 0,
    tax_paise          BIGINT           NOT NULL DEFAULT 0,
    status             settlement_status NOT NULL DEFAULT 'CREATED',
    settled_at         TIMESTAMPTZ,
    created_at         TIMESTAMPTZ      NOT NULL DEFAULT now(),

    CONSTRAINT chk_settlements_amount_positive
        CHECK (amount_paise > 0),
    CONSTRAINT chk_settlements_fees_non_negative
        CHECK (fees_paise >= 0),
    CONSTRAINT chk_settlements_tax_non_negative
        CHECK (tax_paise >= 0)
);


-- 2.3 Recon Rows (from GET /v1/settlements/recon/combined)
CREATE TABLE recon_rows (
    id                 UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id          TEXT          NOT NULL,
    entity_type        TEXT          NOT NULL,       -- 'payment', 'refund', 'transfer', etc.
    order_id           UUID          REFERENCES orders(id),
    settlement_id      UUID          REFERENCES settlements(id),
    settlement_utr     TEXT          NOT NULL,

    -- All money fields: BIGINT paise (G1)
    amount_paise       BIGINT        NOT NULL,
    base_amount_paise  BIGINT        NOT NULL,
    fee_paise          BIGINT        NOT NULL DEFAULT 0,
    tax_paise          BIGINT        NOT NULL DEFAULT 0,
    credit_paise       BIGINT        NOT NULL DEFAULT 0,
    debit_paise        BIGINT        NOT NULL DEFAULT 0,
    on_hold            BOOLEAN       NOT NULL DEFAULT FALSE,

    -- Computed fields (set by deterministic recon engine)
    computed_fee_paise           BIGINT,
    computed_tax_paise           BIGINT,
    computed_net_credit_paise    BIGINT,
    residue_paise                BIGINT,          -- credit - computed_net_credit
    forex_rate_numerator         BIGINT,
    forex_rate_denominator       BIGINT,
    payment_channel              payment_channel,

    recon_status       recon_status  NOT NULL DEFAULT 'PENDING',
    recon_matched_at   TIMESTAMPTZ,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT chk_recon_base_amount_positive
        CHECK (base_amount_paise > 0),
    CONSTRAINT chk_recon_fee_non_negative
        CHECK (fee_paise >= 0),
    CONSTRAINT chk_recon_tax_non_negative
        CHECK (tax_paise >= 0),
    CONSTRAINT chk_recon_credit_non_negative
        CHECK (credit_paise >= 0),
    CONSTRAINT chk_recon_debit_non_negative
        CHECK (debit_paise >= 0),
    -- FX rate rational consistency
    CONSTRAINT chk_recon_forex_rate_rational
        CHECK (
            (forex_rate_numerator IS NULL AND forex_rate_denominator IS NULL)
            OR
            (forex_rate_numerator IS NOT NULL
             AND forex_rate_denominator IS NOT NULL
             AND forex_rate_denominator > 0
             AND forex_rate_numerator > 0)
        )
);

CREATE INDEX idx_recon_entity        ON recon_rows (entity_id);
CREATE INDEX idx_recon_settlement    ON recon_rows (settlement_utr);
CREATE INDEX idx_recon_status        ON recon_rows (recon_status);
CREATE INDEX idx_recon_order         ON recon_rows (order_id);


-- ---------------------------------------------------------------------------
-- 3. Exception & Approval Tables (G4)
-- ---------------------------------------------------------------------------

CREATE TABLE exceptions (
    exception_id       UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    recon_row_id       UUID             NOT NULL REFERENCES recon_rows(id),
    residue_paise      BIGINT           NOT NULL,
    residue_currency   currency_code    NOT NULL DEFAULT 'INR',
    context            JSONB            NOT NULL DEFAULT '{}',

    -- G4: Atomic single-use approval lock
    approval_status    exception_status NOT NULL DEFAULT 'PENDING',
    approval_token     UUID             UNIQUE DEFAULT gen_random_uuid(),
    approved_by        TEXT,
    approved_at        TIMESTAMPTZ,
    approval_reason    TEXT,

    created_at         TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ      NOT NULL DEFAULT now(),

    -- Residue must be non-zero to warrant an exception
    CONSTRAINT chk_exception_residue_nonzero
        CHECK (residue_paise != 0),
    -- approved_by and approved_at must be set together
    CONSTRAINT chk_exception_approval_consistency
        CHECK (
            (approved_by IS NULL AND approved_at IS NULL)
            OR
            (approved_by IS NOT NULL AND approved_at IS NOT NULL)
        ),
    -- Once approved, token must be consumed (NULL)
    CONSTRAINT chk_exception_token_consumed
        CHECK (
            CASE
                WHEN approval_status IN (
                    'APPROVED_WRITE_OFF', 'APPROVED_ADJUSTMENT',
                    'APPROVED_ESCALATION', 'REJECTED'
                )
                THEN approval_token IS NULL
                ELSE TRUE
            END
        )
);

CREATE INDEX idx_exceptions_status   ON exceptions (approval_status);
CREATE INDEX idx_exceptions_token    ON exceptions (approval_token)
    WHERE approval_token IS NOT NULL;
CREATE INDEX idx_exceptions_recon    ON exceptions (recon_row_id);


-- LLM Investigation results (G3: read-only LLM output storage)
CREATE TABLE llm_investigations (
    investigation_id   UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
    exception_id       UUID              NOT NULL REFERENCES exceptions(exception_id),
    model_name         TEXT              NOT NULL,
    provider           TEXT              NOT NULL,     -- 'gemini', 'groq', 'sim'
    prompt_hash        BYTEA             NOT NULL,     -- SHA-256 of the full prompt
    raw_response       TEXT              NOT NULL,
    hypothesis         TEXT,
    suggested_action   TEXT,

    -- G3: FactSet verification result
    provenance_status  provenance_status NOT NULL DEFAULT 'PENDING_VERIFICATION',
    verified_tokens    JSONB             NOT NULL DEFAULT '[]',
    failed_tokens      JSONB             NOT NULL DEFAULT '[]',

    latency_ms         INTEGER           NOT NULL,
    token_count_input  INTEGER,
    token_count_output INTEGER,
    created_at         TIMESTAMPTZ       NOT NULL DEFAULT now(),

    CONSTRAINT chk_investigation_latency_positive
        CHECK (latency_ms >= 0),
    CONSTRAINT chk_investigation_token_counts
        CHECK (
            (token_count_input IS NULL OR token_count_input >= 0)
            AND (token_count_output IS NULL OR token_count_output >= 0)
        )
);

CREATE INDEX idx_investigations_exception ON llm_investigations (exception_id);
CREATE INDEX idx_investigations_provenance ON llm_investigations (provenance_status);


-- ---------------------------------------------------------------------------
-- 4. Compliance Tables
-- ---------------------------------------------------------------------------

-- 4.1 Compliance Classifications
CREATE TABLE compliance_classifications (
    id                UUID              PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id          UUID              NOT NULL REFERENCES orders(id),
    route             compliance_route  NOT NULL,
    flag              compliance_flag   NOT NULL DEFAULT 'CLEAR',
    flag_reason       TEXT,

    -- PA-CB specific fields
    pa_cb_amount_paise BIGINT,
    exceeds_pa_cb_cap  BOOLEAN          NOT NULL DEFAULT FALSE,
    netting_link_count INTEGER          NOT NULL DEFAULT 0,

    classified_at     TIMESTAMPTZ       NOT NULL DEFAULT now(),

    -- PA-CB netting prohibition: must be zero
    CONSTRAINT chk_compliance_no_netting
        CHECK (netting_link_count = 0),
    -- If exceeds cap, flag must reflect it
    CONSTRAINT chk_compliance_cap_flag_consistency
        CHECK (
            CASE
                WHEN exceeds_pa_cb_cap = TRUE
                THEN flag IN ('EXCEEDS_PA_CB_CAP', 'COMPLIANCE_BREACH_BLOCKED')
                ELSE TRUE
            END
        ),
    -- PA-CB cap: 250,000,000 paise = ₹25,00,000
    CONSTRAINT chk_compliance_pa_cb_cap
        CHECK (
            CASE
                WHEN route = 'PA_CB_STANDARD'
                THEN pa_cb_amount_paise IS NOT NULL
                     AND pa_cb_amount_paise <= 250000000  -- ₹25,00,000
                ELSE TRUE
            END
        )
);

CREATE INDEX idx_compliance_order    ON compliance_classifications (order_id);
CREATE INDEX idx_compliance_route    ON compliance_classifications (route);
CREATE INDEX idx_compliance_flag     ON compliance_classifications (flag)
    WHERE flag != 'CLEAR';


-- 4.2 EDF / EDPMS Entries
CREATE TABLE edf_edpms_entries (
    id                 UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           UUID             NOT NULL REFERENCES orders(id),
    settlement_utr     TEXT,

    -- EDF fields
    edf_number         TEXT,
    edf_precapture     BOOLEAN          NOT NULL DEFAULT FALSE,
    is_moneysaver_icici BOOLEAN         NOT NULL DEFAULT FALSE,

    -- EDPMS fields
    edpms_irm_number   TEXT,
    invoice_amount_paise BIGINT,
    invoice_currency   currency_code,

    -- Realization tracking
    shipment_date      DATE,
    realization_deadline DATE,
    realization_type   TEXT             CHECK (realization_type IN ('FCY_9M', 'INR_12M')),
    is_overdue         BOOLEAN          NOT NULL DEFAULT FALSE,
    overdue_days       INTEGER          DEFAULT 0,

    -- Quarterly bulk self-declaration grouping
    bulk_declaration_quarter TEXT,       -- e.g., 'Q1-2027', 'Q2-2027'
    bulk_eligible      BOOLEAN          NOT NULL DEFAULT FALSE,

    -- e-FIRA/FIRS pairing
    efira_reference    TEXT,
    firs_reference     TEXT,

    created_at         TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ      NOT NULL DEFAULT now(),

    -- Invoice amount must be positive if present
    CONSTRAINT chk_edf_invoice_amount_positive
        CHECK (invoice_amount_paise IS NULL OR invoice_amount_paise > 0),
    -- Bulk declaration only for invoices <= ₹10,00,000 (10,00,000,00 paise)
    CONSTRAINT chk_edf_bulk_eligible_threshold
        CHECK (
            CASE
                WHEN bulk_eligible = TRUE
                THEN invoice_amount_paise IS NOT NULL
                     AND invoice_amount_paise <= 100000000  -- ₹10,00,000
                ELSE TRUE
            END
        ),
    -- MoneySaver+ICICI must have EDF pre-capture check
    CONSTRAINT chk_edf_moneysaver_icici_precapture
        CHECK (
            CASE
                WHEN is_moneysaver_icici = TRUE
                THEN edf_precapture IS NOT NULL
                ELSE TRUE
            END
        ),
    -- Overdue days must be non-negative
    CONSTRAINT chk_edf_overdue_days_non_negative
        CHECK (overdue_days >= 0),
    -- Realization deadline must be after shipment date
    CONSTRAINT chk_edf_deadline_after_shipment
        CHECK (
            realization_deadline IS NULL
            OR shipment_date IS NULL
            OR realization_deadline > shipment_date
        )
);

CREATE INDEX idx_edf_order           ON edf_edpms_entries (order_id);
CREATE INDEX idx_edf_overdue         ON edf_edpms_entries (is_overdue) WHERE is_overdue = TRUE;
CREATE INDEX idx_edf_quarter         ON edf_edpms_entries (bulk_declaration_quarter);


-- 4.3 LRS TCS Events (Section 394(1) FY 2026-27)
CREATE TABLE lrs_tcs_events (
    id                UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id          UUID            NOT NULL REFERENCES orders(id),

    -- PAN: keyed HMAC-SHA256 (never store raw PAN)
    pan_hmac          BYTEA           NOT NULL,       -- HMAC-SHA256(pan, key)
    remitter_type     remitter_type   NOT NULL,
    purpose           lrs_purpose     NOT NULL,

    -- FY tracking (April 1 to March 31)
    fiscal_year       TEXT            NOT NULL,       -- e.g., 'FY2026-27'
    fy_start          DATE            NOT NULL,
    fy_end            DATE            NOT NULL,

    -- Amounts (all BIGINT paise for INR, cents for USD where applicable)
    txn_amount_paise  BIGINT          NOT NULL,
    usd_equivalent_cents BIGINT       NOT NULL,       -- for LRS USD 250K cap

    -- Cumulative YTD (pre-this-transaction)
    cumulative_usd_ytd_cents BIGINT   NOT NULL DEFAULT 0,
    -- Cumulative YTD (post-this-transaction)
    cumulative_usd_post_cents BIGINT  NOT NULL,

    -- TCS computation
    threshold_paise   BIGINT          NOT NULL,       -- ₹10,00,000 or 0 per slab
    excess_paise      BIGINT          NOT NULL DEFAULT 0,
    tcs_rate_bps      INTEGER         NOT NULL,       -- basis points (200 = 2%, 2000 = 20%)
    tcs_amount_paise  BIGINT          NOT NULL DEFAULT 0,

    -- Cap enforcement
    exceeds_lrs_cap   BOOLEAN         NOT NULL DEFAULT FALSE,

    created_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),

    -- G1: All money positive or zero
    CONSTRAINT chk_tcs_txn_amount_positive
        CHECK (txn_amount_paise > 0),
    CONSTRAINT chk_tcs_usd_equivalent_positive
        CHECK (usd_equivalent_cents > 0),
    CONSTRAINT chk_tcs_cumulative_non_negative
        CHECK (cumulative_usd_ytd_cents >= 0 AND cumulative_usd_post_cents >= 0),
    CONSTRAINT chk_tcs_excess_non_negative
        CHECK (excess_paise >= 0),
    CONSTRAINT chk_tcs_amount_non_negative
        CHECK (tcs_amount_paise >= 0),
    CONSTRAINT chk_tcs_threshold_non_negative
        CHECK (threshold_paise >= 0),

    -- TCS rate must be a valid slab: 0, 200, or 2000 bps
    CONSTRAINT chk_tcs_rate_valid_slab
        CHECK (tcs_rate_bps IN (0, 200, 2000)),

    -- NRI_NRO and CORPORATE_FEMA must have 0% TCS
    CONSTRAINT chk_tcs_nri_corporate_zero
        CHECK (
            CASE
                WHEN remitter_type IN ('NRI_NRO', 'CORPORATE_FEMA')
                THEN tcs_rate_bps = 0 AND tcs_amount_paise = 0
                ELSE TRUE
            END
        ),

    -- LOAN_EDUCATION must have 0% TCS
    CONSTRAINT chk_tcs_loan_education_zero
        CHECK (
            CASE
                WHEN purpose = 'LOAN_EDUCATION'
                THEN tcs_rate_bps = 0 AND tcs_amount_paise = 0
                ELSE TRUE
            END
        ),

    -- OVERSEAS_TOUR must be flat 2% (200 bps), threshold = 0
    CONSTRAINT chk_tcs_overseas_tour_flat
        CHECK (
            CASE
                WHEN purpose = 'OVERSEAS_TOUR'
                     AND remitter_type = 'RESIDENT_INDIVIDUAL'
                THEN tcs_rate_bps = 200 AND threshold_paise = 0
                ELSE TRUE
            END
        ),

    -- LRS USD cap: 25,000,000 cents = USD 250,000 for residents
    --              100,000,000 cents = USD 1,000,000 for NRI_NRO
    CONSTRAINT chk_tcs_lrs_cap
        CHECK (
            CASE
                WHEN remitter_type = 'RESIDENT_INDIVIDUAL'
                THEN cumulative_usd_post_cents <= 25000000  -- USD 250,000
                WHEN remitter_type = 'NRI_NRO'
                THEN cumulative_usd_post_cents <= 100000000 -- USD 1,000,000
                ELSE TRUE  -- CORPORATE_FEMA: no cap
            END
            OR exceeds_lrs_cap = TRUE  -- Flag it but still record
        ),

    -- FY dates must be valid (April to next March)
    CONSTRAINT chk_tcs_fy_dates_valid
        CHECK (
            fy_end > fy_start
            AND EXTRACT(MONTH FROM fy_start) = 4
            AND EXTRACT(DAY FROM fy_start) = 1
            AND EXTRACT(MONTH FROM fy_end) = 3
            AND EXTRACT(DAY FROM fy_end) = 31
        ),

    -- Post = Pre + current
    CONSTRAINT chk_tcs_cumulative_consistency
        CHECK (cumulative_usd_post_cents = cumulative_usd_ytd_cents + usd_equivalent_cents)
);

CREATE INDEX idx_tcs_pan             ON lrs_tcs_events (pan_hmac);
CREATE INDEX idx_tcs_fy              ON lrs_tcs_events (fiscal_year);
CREATE INDEX idx_tcs_cap_exceeded    ON lrs_tcs_events (exceeds_lrs_cap)
    WHERE exceeds_lrs_cap = TRUE;
CREATE INDEX idx_tcs_order           ON lrs_tcs_events (order_id);


-- ---------------------------------------------------------------------------
-- 5. FETERS Purpose Codes Reference Table
-- ---------------------------------------------------------------------------

CREATE TABLE feters_codes (
    code               TEXT         PRIMARY KEY,
    description        TEXT         NOT NULL,
    category           TEXT         NOT NULL,
    lrs_applicable     BOOLEAN      NOT NULL DEFAULT FALSE,
    lrs_purpose_map    lrs_purpose,
    active             BOOLEAN      NOT NULL DEFAULT TRUE,
    effective_from     DATE         NOT NULL DEFAULT '2023-10-01',
    effective_to       DATE,

    CONSTRAINT chk_feters_code_format
        CHECK (code ~ '^S\d{4}$'),  -- e.g., S0101, S0802
    CONSTRAINT chk_feters_effective_range
        CHECK (effective_to IS NULL OR effective_to > effective_from)
);


-- ---------------------------------------------------------------------------
-- 6. Webhook Events (raw ingest, append-only staging)
-- ---------------------------------------------------------------------------

CREATE TABLE webhook_events (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_event_id TEXT         NOT NULL,
    event_type        TEXT         NOT NULL,       -- 'payment.captured', etc.
    payload           JSONB        NOT NULL,
    signature_valid   BOOLEAN      NOT NULL,
    received_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Deduplication: same event from Razorpay
    CONSTRAINT uk_webhook_event_id UNIQUE (razorpay_event_id)
);

CREATE INDEX idx_webhook_event_type  ON webhook_events (event_type);
CREATE INDEX idx_webhook_received    ON webhook_events (received_at);


-- ---------------------------------------------------------------------------
-- 7. Audit Events — Append-Only Hash Chain (G5)
-- ---------------------------------------------------------------------------

CREATE TABLE audit_events (
    seq               BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action            audit_action NOT NULL,
    entity_type       TEXT         NOT NULL,       -- 'order', 'recon_row', 'exception', etc.
    entity_id         UUID         NOT NULL,
    actor             TEXT         NOT NULL DEFAULT 'system',
    payload           JSONB        NOT NULL,

    -- SHA-256 hash chain: h_i = SHA256(h_{i-1} || canonical_json || timestamp)
    prev_hash         BYTEA        NOT NULL,       -- h_{i-1} (32 bytes)
    event_hash        BYTEA        NOT NULL,       -- h_i     (32 bytes)
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Hash must be exactly 32 bytes (SHA-256)
    CONSTRAINT chk_audit_prev_hash_length
        CHECK (octet_length(prev_hash) = 32),
    CONSTRAINT chk_audit_event_hash_length
        CHECK (octet_length(event_hash) = 32),
    -- Genesis event (seq=1) has prev_hash = SHA256('GENESIS')
    -- Enforced at application layer; DB ensures non-null and length.

    -- Payload must not be empty
    CONSTRAINT chk_audit_payload_not_empty
        CHECK (payload != '{}'::jsonb)
);

CREATE INDEX idx_audit_entity        ON audit_events (entity_type, entity_id);
CREATE INDEX idx_audit_action        ON audit_events (action);
CREATE INDEX idx_audit_created       ON audit_events (created_at);
CREATE INDEX idx_audit_hash_chain    ON audit_events (seq, prev_hash);

-- G5 Enforcement: REVOKE UPDATE and DELETE from application role
-- This ensures the audit chain is truly append-only at the database level.
REVOKE UPDATE, DELETE ON audit_events FROM app_role;

-- Grant only INSERT and SELECT
GRANT INSERT, SELECT ON audit_events TO app_role;
-- Grant usage on the identity sequence
GRANT USAGE ON SEQUENCE audit_events_seq_seq TO app_role;


-- ---------------------------------------------------------------------------
-- 8. Approval Audit Trail (separate from main audit for query efficiency)
-- ---------------------------------------------------------------------------

CREATE TABLE approval_events (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    exception_id      UUID         NOT NULL REFERENCES exceptions(exception_id),
    action            TEXT         NOT NULL
                      CHECK (action IN (
                          'CREATED', 'INVESTIGATION_STARTED', 'INVESTIGATION_COMPLETED',
                          'INVESTIGATION_ABSTAINED', 'APPROVED', 'REJECTED',
                          'CONFLICT_DETECTED'
                      )),
    actor             TEXT         NOT NULL,
    old_status        exception_status,
    new_status        exception_status NOT NULL,
    metadata          JSONB        NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX idx_approval_exception  ON approval_events (exception_id);
CREATE INDEX idx_approval_created    ON approval_events (created_at);


-- ---------------------------------------------------------------------------
-- 9. Eval Runs (evaluation harness tracking)
-- ---------------------------------------------------------------------------

CREATE TABLE eval_runs (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    seed              TEXT         NOT NULL,
    data_mode         TEXT         NOT NULL CHECK (data_mode IN ('sim', 'live')),
    model_name        TEXT,
    corpus_size       INTEGER      NOT NULL,
    strata            JSONB        NOT NULL,       -- {happy_path: 110, ...}

    -- Aggregate results
    deterministic_match_rate  NUMERIC(5,4),  -- OK to use NUMERIC for metrics (not money)
    false_match_rate          NUMERIC(5,4),
    exception_detection_rate  NUMERIC(5,4),
    factset_grounding_rate    NUMERIC(5,4),
    compliance_accuracy       NUMERIC(5,4),
    audit_chain_integrity     NUMERIC(5,4),

    started_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    artifacts_path    TEXT,         -- path to run_<seed>.jsonl

    CONSTRAINT chk_eval_corpus_positive
        CHECK (corpus_size > 0),
    -- Rates are between 0.0000 and 1.0000
    CONSTRAINT chk_eval_rates_range
        CHECK (
            (deterministic_match_rate IS NULL OR (deterministic_match_rate >= 0 AND deterministic_match_rate <= 1))
            AND (false_match_rate IS NULL OR (false_match_rate >= 0 AND false_match_rate <= 1))
            AND (exception_detection_rate IS NULL OR (exception_detection_rate >= 0 AND exception_detection_rate <= 1))
            AND (factset_grounding_rate IS NULL OR (factset_grounding_rate >= 0 AND factset_grounding_rate <= 1))
            AND (compliance_accuracy IS NULL OR (compliance_accuracy >= 0 AND compliance_accuracy <= 1))
            AND (audit_chain_integrity IS NULL OR (audit_chain_integrity >= 0 AND audit_chain_integrity <= 1))
        )
);

CREATE INDEX idx_eval_seed           ON eval_runs (seed);


-- ---------------------------------------------------------------------------
-- 10. Helper Views
-- ---------------------------------------------------------------------------

-- 10.1 Open exceptions with investigation status
CREATE VIEW v_open_exceptions AS
SELECT
    e.exception_id,
    e.residue_paise,
    e.residue_currency,
    e.approval_status,
    e.created_at           AS exception_created_at,
    r.entity_id,
    r.settlement_utr,
    r.amount_paise         AS original_amount_paise,
    li.investigation_id,
    li.provenance_status,
    li.hypothesis,
    li.model_name
FROM exceptions e
JOIN recon_rows r ON r.id = e.recon_row_id
LEFT JOIN LATERAL (
    SELECT *
    FROM llm_investigations li2
    WHERE li2.exception_id = e.exception_id
    ORDER BY li2.created_at DESC
    LIMIT 1
) li ON TRUE
WHERE e.approval_status IN ('PENDING', 'PENDING_INVESTIGATION', 'INVESTIGATED');

-- 10.2 Settlement reconciliation summary
CREATE VIEW v_settlement_recon_summary AS
SELECT
    s.utr,
    s.amount_paise          AS settlement_amount_paise,
    s.currency,
    COUNT(r.id)             AS recon_row_count,
    SUM(r.credit_paise)     AS total_credit_paise,
    SUM(r.debit_paise)      AS total_debit_paise,
    SUM(r.credit_paise) - SUM(r.debit_paise) AS net_paise,
    s.amount_paise - (SUM(r.credit_paise) - SUM(r.debit_paise)) AS variance_paise,
    BOOL_AND(r.recon_status = 'MATCHED') AS all_matched
FROM settlements s
LEFT JOIN recon_rows r ON r.settlement_utr = s.utr
GROUP BY s.id, s.utr, s.amount_paise, s.currency;

-- 10.3 PAN-cumulative LRS YTD (for threshold checks)
CREATE VIEW v_lrs_cumulative_ytd AS
SELECT
    pan_hmac,
    fiscal_year,
    remitter_type,
    SUM(usd_equivalent_cents)   AS total_usd_cents,
    SUM(tcs_amount_paise)       AS total_tcs_paise,
    MAX(cumulative_usd_post_cents) AS latest_cumulative_usd_cents,
    BOOL_OR(exceeds_lrs_cap)    AS any_cap_exceeded,
    COUNT(*)                    AS txn_count
FROM lrs_tcs_events
GROUP BY pan_hmac, fiscal_year, remitter_type;

-- 10.4 EDF/EDPMS overdue tracking
CREATE VIEW v_edf_overdue AS
SELECT
    e.id,
    e.order_id,
    o.razorpay_order_id,
    e.edf_number,
    e.shipment_date,
    e.realization_deadline,
    e.realization_type,
    CURRENT_DATE - e.realization_deadline AS days_overdue,
    CASE
        WHEN CURRENT_DATE - e.realization_deadline > 365 THEN 'CRITICAL_OVERDUE'
        WHEN CURRENT_DATE > e.realization_deadline        THEN 'OVERDUE'
        WHEN e.realization_deadline - CURRENT_DATE <= 30  THEN 'APPROACHING'
        ELSE 'ON_TRACK'
    END AS risk_level
FROM edf_edpms_entries e
JOIN orders o ON o.id = e.order_id
WHERE e.realization_deadline IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 11. Trigger: Prevent audit_events mutation (defense-in-depth)
-- ---------------------------------------------------------------------------

-- Even with REVOKE, add a trigger as defense-in-depth for superuser/owner
CREATE OR REPLACE FUNCTION fn_prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'audit_events is append-only (G5). UPDATE and DELETE are prohibited. '
        'seq=%, action=%',
        COALESCE(OLD.seq::TEXT, NEW.seq::TEXT),
        COALESCE(OLD.action::TEXT, NEW.action::TEXT);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevent_audit_update
    BEFORE UPDATE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION fn_prevent_audit_mutation();

CREATE TRIGGER trg_prevent_audit_delete
    BEFORE DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION fn_prevent_audit_mutation();


-- ---------------------------------------------------------------------------
-- 12. Seed: Genesis Audit Event
-- ---------------------------------------------------------------------------

-- The genesis event anchors the hash chain.
-- h_0 = SHA256('GENESIS') = decode('...', 'hex')
-- h_1 = SHA256(h_0 || '{"action":"AUDIT_CHAIN_GENESIS"}' || '<timestamp>')
-- This is inserted by the application on first boot; shown here for reference.

-- INSERT INTO audit_events (action, entity_type, entity_id, actor, payload, prev_hash, event_hash)
-- VALUES (
--     'AUDIT_CHAIN_GENESIS',
--     'system',
--     '00000000-0000-0000-0000-000000000000',
--     'system',
--     '{"action": "AUDIT_CHAIN_GENESIS", "version": "1.0.0"}'::jsonb,
--     digest('GENESIS', 'sha256'),
--     digest(
--         digest('GENESIS', 'sha256')
--         || '{"action":"AUDIT_CHAIN_GENESIS","version":"1.0.0"}'::bytea
--         || now()::TEXT::bytea,
--         'sha256'
--     )
-- );


-- ---------------------------------------------------------------------------
-- 13. Grants Summary
-- ---------------------------------------------------------------------------

-- Financial tables: full CRUD for app_role
GRANT SELECT, INSERT, UPDATE, DELETE ON orders TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON recon_rows TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON settlements TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON exceptions TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON compliance_classifications TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON edf_edpms_entries TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON lrs_tcs_events TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON webhook_events TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON approval_events TO app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON eval_runs TO app_role;
GRANT SELECT, INSERT ON llm_investigations TO app_role;   -- No UPDATE/DELETE on investigations
GRANT SELECT ON feters_codes TO app_role;                  -- Read-only reference data

-- Views
GRANT SELECT ON v_open_exceptions TO app_role;
GRANT SELECT ON v_settlement_recon_summary TO app_role;
GRANT SELECT ON v_lrs_cumulative_ytd TO app_role;
GRANT SELECT ON v_edf_overdue TO app_role;

-- audit_events: INSERT + SELECT only (G5)
-- Already granted above; UPDATE and DELETE revoked.


-- =============================================================================
-- END OF SCHEMA
-- =============================================================================
