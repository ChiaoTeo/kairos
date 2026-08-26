RUSTFMT_TOOLCHAIN ?= nightly-2025-08-26

.PHONY: docs docs-check execution-core-check execution-venue-check python-type-check rust-fmt rust-fmt-check

execution-core-check:
	cargo test -p kairos-execution --all-targets
	cargo test -p kairos-execution-contract
	uv run pytest -q tests/test_execution_contract.py tests/test_command_ports.py
	cargo test -p kairos-execution --lib \
		integration_tests::kairospy_explicit_algorithm_round_trips_through_execution_json_rpc \
		-- --exact --ignored

execution-venue-check:
	cargo test -p kairos-integration --lib execution
	cargo test -p kairos-integration --lib participants::hyperliquid::exchange::tests
	cargo build -p kairos-transport --bin kairos-aeron-driver
	KAIROS_AERON_DRIVER_BIN=$(CURDIR)/target/debug/kairos-aeron-driver \
		cargo test -p kairos-execution --lib \
		integration_tests::conflux_managed_runtime_dispatches_due_twap_through_its_venue_connection \
		-- --exact --ignored
	KAIROS_AERON_DRIVER_BIN=$(CURDIR)/target/debug/kairos-aeron-driver \
		cargo test -p kairos-execution --lib \
		integration_tests::managed_twap_response_loss_reconciles_by_query_after_restart \
		-- --exact --ignored
	KAIROS_AERON_DRIVER_BIN=$(CURDIR)/target/debug/kairos-aeron-driver \
		cargo test -p kairos-execution --lib \
		integration_tests::conflux_private_execution_event_preserves_explicit_order_identities_across_restart \
		-- --exact --ignored
	KAIROS_AERON_DRIVER_BIN=$(CURDIR)/target/debug/kairos-aeron-driver \
		cargo test -p kairos-execution --lib \
		integration_tests::managed_binance_private_stream_reconnects_and_converges_without_duplicate_fill \
		-- --exact --ignored
	uv run pytest -q tests/test_execution_venue_certification_check.py
	python3 scripts/check/check_execution_venue_certification.py

python-type-check:
	uv run pyright --warnings

rust-fmt:
	cargo +$(RUSTFMT_TOOLCHAIN) fmt --all

rust-fmt-check:
	cargo +$(RUSTFMT_TOOLCHAIN) fmt --all -- --check

docs:
	python3 scripts/generate/validate_v2_schemas.py
	python3 scripts/check/check_execution_venue_certification.py
	python3 scripts/check/check_documentation.py
	python3 scripts/check/check_read_model_vocabulary.py
	./scripts/docs/build_all_scalar_docs.sh

docs-check:
	python3 scripts/generate/validate_v2_schemas.py
	./scripts/docs/build_all_scalar_docs.sh --check
	python3 scripts/check/check_execution_venue_certification.py
	python3 scripts/check/check_documentation.py
	python3 scripts/check/check_read_model_vocabulary.py
