-- Backfill: null out installment_total_amount where it was computed from
-- amount * total_installments (the old fallback pattern). Transactions where
-- Pluggy provided creditCardMetadata.totalAmount will retain their value.
--
-- Run with: psql $DATABASE_URL -f backfill_installment_total.sql

UPDATE transactions
SET installment_total_amount = NULL
WHERE installment_number IS NOT NULL
  AND installment_total_amount IS NOT NULL
  AND installment_total_amount = amount * total_installments;
