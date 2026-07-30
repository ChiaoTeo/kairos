# Run Environment and Entrypoints

KairosPy supports two ways to run strategy code:

- Managed runs through `kairospy run start`.
- User-owned Python programs through `python path/to/main.py`.

Both paths should use the same run environment model. The difference is who owns the program entrypoint.

## Workspace Manifest

`.kairos/kairos.toml` owns project-level defaults:

```toml
[project]
name = "my-project"
timezone = "UTC"
language = "en"
```

`project.timezone` is the default timezone used by `RunEnvironment` helpers when a user-owned main program supplies local wall-clock strings without an explicit offset. Runtime events remain timezone-aware.

`project.language` is the preferred system/display language for CLI output, logs, and reports. Use `zh-CN` for Simplified Chinese. It should not change strategy event domains, payload keys, or data formats.

## Run Environment

A run TOML file describes the Kairos runtime environment. It is not required to describe the user's program entrypoint.

Typical responsibilities:

- Run identity and mode.
- Workspace and artifact paths.
- Backtest window and execution settings.
- Account references for paper/live runs.
- User-defined `[params]`.

Example:

```toml
[run]
id = "news-factor-backtest"
mode = "backtest"

[paths]
runs_root = ".kairos/runs"
data_root = ".kairos/data"

[backtest]
start = "2026-01-01T00:00:00Z"
end = "2026-01-02T00:00:00Z"

[params]
news_path = "examples/data/news_factor.csv"
sentiment_threshold = 0.65
symbol = "BTC/USDT"
```

`[params]` belongs to the run environment. Kairos stores and exposes these values but does not interpret their business meaning.

## Managed Entrypoint

Managed runs are for users who want Kairos to own the runtime lifecycle:

```bash
kairospy run start configs/news.toml --strategy strategies.news_factor:strategy
```

In this mode Kairos:

- Loads `RunEnvironment`.
- Loads the strategy entrypoint.
- Creates the run instance directory.
- Runs the runtime kernel.
- Writes logs and artifacts.

The strategy entrypoint can accept the full environment:

```python
def strategy(env):
    return MyStrategy(threshold=env.params["sentiment_threshold"])
```

Older factory style remains valid:

```python
def strategy(**params):
    return MyStrategy(**params)
```

## User-Owned Main

Custom main programs are for users who want to own data source construction, preprocessing, research workflow, or external integrations:

```bash
uv run python examples/strategies/news_factor.py
```

The user program still uses the same environment:

```python
from kairospy import RunEnvironment

def main():
    env = RunEnvironment.from_config("examples/configs/news_factor_backtest.toml")
    source = env.sources.csv_events(env.params["news_path"], kind="news.sentiment")
    result = env.run(strategy=MyStrategy(...), sources=[source])
```

`RunEnvironment.from_config(...)` accepts a config path or a registered run name. It uses the same workspace run index as:

```bash
kairospy run register news-factor-backtest examples/configs/news_factor_backtest.toml
```

User programs can also resolve explicitly:

```python
from kairospy import RunEnvironment, ensure_run_registered

ensure_run_registered("news-factor-backtest", "examples/configs/news_factor_backtest.toml")
env = RunEnvironment.open("news-factor-backtest")
```

`kairospy run start` does not call user `main` functions. A Python file with `if __name__ == "__main__"` is the user's program entrypoint.

## Runtime Data Sources

The stable extension point for custom data is `RuntimeDataSource`:

```python
class RuntimeDataSource(Protocol):
    source_id: str

    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...
```

Kairos provides small adapters:

- `IterableEventSource` for in-memory or prebuilt events.
- `CsvEventSource` for historical file-backed events.
- `AsyncEventSource` for realtime streams such as websocket feeds, queues, and custom async clients.

Historical and realtime sources are peers. CSV is only one adapter, not the core abstraction.

## Runtime Clock

Runtime time is a first-class event stream. Strategies should treat `context.now` as the strategy-visible time of the current event, not as the machine wall clock.

Clock events use `domain="clock"` and are delivered to `Strategy.on_clock`:

```python
clock = env.clocks.interval(
    "rebalance",
    start="2026-01-01T00:00:00Z",
    end="2026-01-02T00:00:00Z",
    every="1h",
)
result = env.run(strategy=strategy, sources=[market_data], clocks=[clock])
```

For fixed historical schedules:

```python
clock = env.clocks.ticks("open-close", [
    "2026-01-01T09:30:00-05:00",
    "2026-01-01T16:00:00-05:00",
])
```

For realtime runs:

```python
clock = env.clocks.realtime("heartbeat", every="30s")
```

Finite historical clocks are sorted with the other finite event sources. Realtime clocks are merged asynchronously with realtime data sources.

## Event Domains

Custom factor data should normally use `domain="data"`:

```python
RuntimeEnvelope(
    domain="data",
    kind="news.sentiment",
    time=available_at,
    sequence=1,
    payload=DataObservation(...),
)
```

`domain="data"` events are delivered to `Strategy.on_data`, the same hook used by market data.

For historical factor backtests, use the time when the strategy could have known the data as `RuntimeEnvelope.time`. For news, this is usually `available_at`, not the article's original observed or published time.

## Boundary

Use managed runs when Kairos should own assembly and lifecycle.

Use user-owned main programs when the strategy needs custom data sources, factor pipelines, external services, or research-specific setup.

Both should consume `RunEnvironment`.

## Run Paths

Run artifacts must be written to a run instance directory:

```text
.kairos/runs/<mode>/<run-id>/instances/<run-instance-id>/
```

The run group directory is reserved for group-level control and discovery files such as `current.json`:

```text
.kairos/runs/<mode>/<run-id>/current.json
```

User code should read `env.instance_dir` for artifacts. It should not write summaries, logs, fills, metrics, or custom outputs directly into `env.run_group_dir`.

## Logs and Attach

Every run instance writes `run.log` under `env.instance_dir`. Kairos writes small status sections for:

- Run environment.
- System status.
- Account status when an account view exists.

User-owned main programs can mirror run output to stdout:

```python
result = env.run(strategy=strategy, sources=[source], echo=True)
```

Managed daemon runs can be attached from the CLI:

```bash
kairospy run logs news-factor-backtest --follow
kairospy run daemon attach news-factor-backtest
```
