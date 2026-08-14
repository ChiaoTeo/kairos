.PHONY: docs docs-check schema-check

schema-check:
	python3 scripts/check/check_no_schema_v1.py

docs: docs-check
	./scripts/docs/build_all_scalar_docs.sh

docs-check:
	$(MAKE) schema-check
	python3 scripts/generate/validate_v2_schemas.py
	./scripts/docs/build_all_scalar_docs.sh --check
