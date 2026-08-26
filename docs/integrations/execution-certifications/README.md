# Execution transaction certification records

This directory contains durable, secret-free records for external Execution venue certifications.
One valid TOML record certifies one row of the maintained
[Execution venue matrix](../execution-venue-certification.md) at level `T`. A matrix row cannot be
marked `T` without a record that passes
`python3 scripts/check/check_execution_venue_certification.py`.

Records are named by `certification_id` and use this shape:

```toml
version = 1
certification_id = "provider-channel-environment-YYYYMMDD"
provider = "Provider label exactly as shown in the matrix"
execution_channel = "Channel label exactly as shown in the matrix"
environment = "testnet" # live, testnet, demo, or paper
product = "provider product and instrument"
account_mode = "spot/cross/isolated/etc."
credential_permission_class = "trade enabled; withdrawal disabled"
started_at = "2026-08-26T10:00:00Z"
completed_at = "2026-08-26T10:10:00Z"
client_order_id = "stable redacted-safe client identity"
remote_order_id = "venue identity"
terminal_outcome = "filled" # filled, canceled, rejected, or expired
lifecycle_observations = ["accepted", "partially_filled", "filled"]
fee_mapping = "verified" # or not_applicable
cleanup_result = "no open order; intended test position closed"
residual_impact = "fees and residual balances described without secrets"

[evidence]
submit_observed = true
private_event_observed = true
query_reconciled = true
response_loss_or_disconnect_recovered = true
restart_reconciled = true
no_duplicate_order = true
quantity_mapping_verified = true
cleanup_complete = true

[[artifacts]]
kind = "redacted_event_trace"
path = "docs/integrations/execution-certifications/evidence/example.json"
sha256 = "64-lowercase-hexadecimal-content-digest"
```

A live record also requires `authorization_reference`, identifying the explicit approval without
embedding credentials. Artifact paths must remain under this directory, exist, and match their SHA-256
digest. Artifacts must be redacted before commit: never store API keys, signatures, tokens, account
secrets, raw provider headers, or unrestricted account payloads.

The checker requires evidence for submit, private event, bounded query reconciliation, injected response
loss or disconnect recovery, persisted restart, duplicate prevention, quantity mapping, and cleanup.
Local provider fixtures remain `D/C/R` evidence and do not produce a record here.
