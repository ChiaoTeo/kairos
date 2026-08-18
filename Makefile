.PHONY: docs docs-check

docs:
	python3 scripts/generate/validate_v2_schemas.py
	python3 scripts/check/check_documentation.py
	./scripts/docs/build_all_scalar_docs.sh

docs-check:
	python3 scripts/generate/validate_v2_schemas.py
	./scripts/docs/build_all_scalar_docs.sh --check
	python3 scripts/check/check_documentation.py
