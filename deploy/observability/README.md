# Kairos observability stack

This directory provides the reproducible local acceptance stack: OpenTelemetry
Collector, Tempo, Prometheus, Loki, and Grafana. It is intentionally local-only:
all published ports except OTLP are bound to loopback and no production secrets
are stored here.

Start it from the repository root:

```sh
KAIROS_LOG_ROOT="$PWD/.kairos/logs" docker compose -f deploy/observability/compose.yaml up -d
```

Applications can then use `KAIROS_OTEL_ENABLED=1` (or set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`). Collector health is
available on `http://127.0.0.1:13133`; Grafana is at
`http://127.0.0.1:3000`.

For production, deploy the overlay with the platform secret store:

```sh
docker compose \
  -f deploy/observability/compose.yaml \
  -f deploy/observability/compose.production.yaml \
  --env-file /run/secrets/kairos-observability.env up -d
```

`otel-collector.production.yaml` enforces mTLS for OTLP intake, TLS for every
backend export, bounded sending queues and finite retries. The production
overlay removes host ports, disables anonymous Grafana access and applies a
Collector resource limit. `production.env.example` is a key inventory only;
the real file must come from the secret store and be readable only by the
deployment identity.

Do not publish Collector, Prometheus, Loki, Tempo, or Grafana ports directly to
an untrusted network. The platform must provide encrypted persistent storage,
backups, retention (at least the incident-investigation window agreed by the
service owner), and least-privilege dashboard access. Validate the production
configuration with the real certificates and endpoints before rollout; the
local compose stack deliberately does not simulate those credentials.
