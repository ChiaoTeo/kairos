# Kairos live run deployment examples

These templates run a live strategy as a long-lived foreground daemon under an
external process supervisor. They intentionally use:

```bash
kairospy run live start --foreground --config /opt/kairos/configs/runs/binance-live.toml --run-id binance-btc-live --confirm-live
```

Use the same `run_id` from another CLI process for operator control:

```bash
kairospy run status --run-id binance-btc-live
kairospy run stop --run-id binance-btc-live --actor ops --reason "maintenance" --timeout-seconds 30
kairospy run metrics --run-id binance-btc-live --prometheus --output /var/lib/kairos/metrics/binance-btc-live.prom
kairospy run export --run-id binance-btc-live --output /var/lib/kairos/exports/binance-btc-live
```

The default background `run live start` path is useful for workstation use. For
systemd, launchd, Docker, and Kubernetes, prefer `--foreground` so the supervisor
owns process lifetime and restart policy.

Files:

- `systemd/kairos-live-run.service`
- `launchd/com.example.kairos.live-run.plist`
- `docker/Dockerfile`
- `docker/entrypoint.sh`
- `kubernetes/deployment.yaml`
- `kubernetes/configmap.yaml`

Before using these templates, replace all `/opt/kairos`, `/var/lib/kairos`,
image, secret, and run config placeholders with deployment-specific values.
