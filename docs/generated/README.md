# Generated API documentation

The v2 control-plane OpenAPI schemas are rendered as static Scalar pages:

```bash
make docs
```

Open [`index.html`](api/index.html) for the API documentation index. The
individual pages are generated from every `schemas/v2/*/control.openapi.yaml`
file and do not send requests to a running process.

To validate the v2 schema registry and bundle every OpenAPI document without
writing HTML files:

```bash
make docs-check
```
