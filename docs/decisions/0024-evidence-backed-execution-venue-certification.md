# Decision 0024: Evidence-backed Execution venue certification

- Status: Accepted
- Date: 2026-08-26
- Scope: Venue Execution certification, durable evidence, capability claims

## Context

Provider fixtures and local transport fault injection can prove deterministic mapping, composition, and
recovery, but they cannot prove that an external demo, testnet, paper broker, or explicitly authorized
live venue completed the same lifecycle. A prose-only `T` mark can drift from the evidence, omit restart
or cleanup, or accidentally preserve credential material.

## Decision

An Execution venue matrix row may be marked transaction-certified (`T`) only when a provider-specific,
secret-free TOML record exists under `docs/integrations/execution-certifications/` and passes the
repository certification checker. A valid record requires:

- explicit provider, channel, environment, account mode, permission class, and bounded time window;
- stable client and remote order identities plus accepted and terminal lifecycle observations;
- submit, private-event, bounded-query, response-loss/disconnect recovery, restart reconciliation,
  duplicate-prevention, quantity mapping, and cleanup evidence;
- fee mapping or an explicit not-applicable result, cleanup outcome, and residual impact;
- at least one redacted artifact under the same certification directory with a matching SHA-256 digest;
- an authorization reference for any live transaction.

The checker enforces bidirectional consistency. A `T` mark without a valid record fails, and a valid
record whose matrix row is not `T` also fails. Secret-bearing TOML keys, missing artifacts, path escapes,
and digest mismatches fail. The check runs as part of `docs-check`; default tests never place an external
order.

## Consequences

- `D/C/R` local evidence cannot be relabeled as an external transaction.
- A future authorized certification is reproducible and reviewable without storing credentials.
- Revoking or superseding evidence requires updating both the durable record and matrix claim.
- The record validates evidence already produced through the normal Execution path; it is not another
  command API, order sender, or state owner.

## Implementation anchors

- Evidence format: `docs/integrations/execution-certifications/README.md`
- Maintained matrix: `docs/integrations/execution-venue-certification.md`
- Checker: `scripts/check/check_execution_venue_certification.py`
- Checker tests: `tests/test_execution_venue_certification_check.py`
