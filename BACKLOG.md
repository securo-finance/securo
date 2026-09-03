# Backlog

## Pending
- [ ] `counts_on_bill()` (`backend/app/services/_query_filters.py`) uses the `treat_as_transfer` category flag to decide whether a credit-card credit is a merchant refund (should shrink the bill) or an unpaired bill payment (should not). Both readings are wrong sometimes — there's no structural signal today that tells the two apart once a credit lands in a transfer-tagged category. The real fix is a field that captures this directly, e.g. promoting a Pluggy operation subtype (`raw_data` payload) to a column, or an explicit "this is a bill payment" flag set when `transfer_detection_service.py` fails to auto-pair a credit-card credit. (discovered 2026-09-03, while resolving conflicts on PR #649)

## Deferred Decisions
- Whether transfer-tagged credit-card credits should net against the bill total: resolved for now by keeping them OUT (PR #649's rule favors the more common case — an unpaired payment shrinking the bill every cycle — over the rarer one, a refund of a transfer-tagged purchase). Revisit once a structural signal (see above) exists to resolve both cases correctly instead of picking one.
