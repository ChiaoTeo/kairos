RUSTFMT_TOOLCHAIN ?= nightly-2025-08-26

.PHONY: docs docs-check python-type-check rust-fmt rust-fmt-check

python-type-check:
	uv run pyright --warnings

rust-fmt:
	cargo +$(RUSTFMT_TOOLCHAIN) fmt --all

rust-fmt-check:
	cargo +$(RUSTFMT_TOOLCHAIN) fmt --all -- --check

docs:
	python3 scripts/generate/validate_v2_schemas.py
	python3 scripts/check/check_documentation.py
	python3 scripts/check/check_read_model_vocabulary.py
	./scripts/docs/build_all_scalar_docs.sh

docs-check:
	python3 scripts/generate/validate_v2_schemas.py
	./scripts/docs/build_all_scalar_docs.sh --check
	python3 scripts/check/check_documentation.py
	python3 scripts/check/check_read_model_vocabulary.py
