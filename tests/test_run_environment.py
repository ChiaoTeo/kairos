from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from kairospy import RunEnvironment, ensure_run_registered
from kairospy.application.runtime import RuntimeEnvelope
from kairospy.application.strategy import StrategyBase


class NewsStrategy(StrategyBase):
    strategy_id = "news-test"

    def __init__(self, *, threshold: float) -> None:
        self.threshold = threshold
        self.seen: list[object] = []

    def on_data(self, context, signal) -> None:
        self.seen.append(signal.payload)


class ClockStrategy(StrategyBase):
    strategy_id = "clock-test"

    def __init__(self) -> None:
        self.callbacks: list[tuple[str, datetime | None]] = []

    def on_data(self, context, signal) -> None:
        self.callbacks.append(("data", context.now))

    def on_clock(self, context, signal) -> None:
        self.callbacks.append(("clock", context.now))


def test_run_environment_runs_custom_csv_data_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "news.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "news-run"',
                'mode = "backtest"',
                "",
                "[paths]",
                'runs_root = ".kairos/runs"',
                "",
                "[params]",
                'news_path = "news.csv"',
                "threshold = 0.65",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "news.csv").write_text(
        "\n".join(
            [
                "available_at,observed_at,symbol,headline,sentiment",
                "2026-01-01T10:00:03+00:00,2026-01-01T09:59:30+00:00,BTC/USDT,good news,0.72",
            ]
        ),
        encoding="utf-8",
    )

    env = RunEnvironment.from_config(config_path)
    strategy = NewsStrategy(threshold=float(env.params["threshold"]))
    news = env.sources.csv_events(
        env.params["news_path"],
        kind="news.sentiment",
        time_field="available_at",
        observed_at_field="observed_at",
        available_at_field="available_at",
        subject_type="instrument",
        subject_id_field="symbol",
    )

    result = env.run(strategy=strategy, sources=[news])

    assert result.runtime.strategy_id == "news-test"
    assert result.runtime.event_count == 2
    assert strategy.seen[0].kind == "news.sentiment"
    assert strategy.seen[0].value["sentiment"] == 0.72
    group_dir = tmp_path / ".kairos" / "runs" / "backtest" / "news-run"
    assert env.instance_dir.parent == group_dir / "instances"
    assert (env.instance_dir / "summary.json").exists()
    assert "Run Environment" in (env.instance_dir / "run.log").read_text(encoding="utf-8")
    current = json.loads((group_dir / "current.json").read_text(encoding="utf-8"))
    assert current["run_instance_id"] == env.run_instance_id
    assert current["directory"] == str(env.instance_dir)


def test_run_environment_runs_custom_async_data_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "live-news.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "live-news"',
                'mode = "paper"',
                "",
                "[params]",
                "threshold = 0.65",
            ]
        ),
        encoding="utf-8",
    )

    async def realtime_events():
        yield RuntimeEnvelope(
            "data",
            "news.sentiment",
            datetime.fromisoformat("2026-01-01T10:00:03+00:00"),
            1,
            {"sentiment": 0.81},
        )

    env = RunEnvironment.from_config(config_path)
    strategy = NewsStrategy(threshold=float(env.params["threshold"]))
    source = env.sources.async_events("realtime-news", realtime_events())

    result = env.run(strategy=strategy, sources=[source])

    assert result.runtime.strategy_id == "news-test"
    assert strategy.seen == [{"sentiment": 0.81}]


def test_run_environment_clock_callbacks_use_strategy_time(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, run_id="clock-run")
    env = RunEnvironment.from_config(config_path)
    strategy = ClockStrategy()
    clock = env.clocks.ticks(
        "rebalance-clock",
        [
            "2026-01-01T10:00:00+00:00",
            "2026-01-01T10:00:02+00:00",
        ],
    )
    data = env.sources.iterable(
        "news",
        [
            RuntimeEnvelope(
                "data",
                "news.sentiment",
                datetime.fromisoformat("2026-01-01T10:00:01+00:00"),
                1,
                {"sentiment": 0.71},
            )
        ],
    )

    result = env.builder().strategy(strategy).source(data).clock(clock).run()

    assert result.runtime.event_count == 4
    assert strategy.callbacks == [
        ("clock", datetime.fromisoformat("2026-01-01T10:00:00+00:00")),
        ("data", datetime.fromisoformat("2026-01-01T10:00:01+00:00")),
        ("clock", datetime.fromisoformat("2026-01-01T10:00:02+00:00")),
    ]


def test_run_environment_interval_clock_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, run_id="interval-clock")
    env = RunEnvironment.from_config(config_path)
    clock = env.clocks.interval(
        "minute-clock",
        start="2026-01-01T10:00:00+00:00",
        end="2026-01-01T10:02:00+00:00",
        every="1m",
    )

    async def collect():
        return [event async for event in clock.events()]

    import asyncio

    events = asyncio.run(collect())

    assert [event.time for event in events] == [
        datetime.fromisoformat("2026-01-01T10:00:00+00:00"),
        datetime.fromisoformat("2026-01-01T10:01:00+00:00"),
        datetime.fromisoformat("2026-01-01T10:02:00+00:00"),
    ]


def test_run_environment_resolves_registered_run_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos" / "state").mkdir(parents=True)
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"env-test\"\n", encoding="utf-8")
    config_path = tmp_path / "registered.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                'id = "registered-run"',
                'mode = "backtest"',
                "",
                "[params]",
                "threshold = 0.7",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".kairos" / "state" / "run-index.json").write_text(
        '{\n  "schema_version": 1,\n  "runs": {\n    "news-registered": {"config": "registered.toml"}\n  }\n}\n',
        encoding="utf-8",
    )

    env = RunEnvironment.from_config("news-registered")

    assert env.run_id == "registered-run"
    assert env.params["threshold"] == 0.7


def test_run_environment_rejects_run_group_as_instance_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, run_id="group-rejected")
    group_dir = tmp_path / ".kairos" / "runs" / "backtest" / "group-rejected"

    try:
        RunEnvironment.from_config(config_path, instance_dir=group_dir)
    except ValueError as error:
        assert "run group directory" in str(error)
    else:
        raise AssertionError("RunEnvironment accepted a run group as an instance directory")


def test_run_environment_accepts_explicit_instance_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, run_id="explicit-instance")
    instance_dir = tmp_path / ".kairos" / "runs" / "backtest" / "explicit-instance" / "instances" / "manual-1"

    env = RunEnvironment.from_config(config_path, instance_dir=instance_dir)

    assert env.instance_dir == instance_dir
    assert env.run_instance_id == "manual-1"


def test_run_environment_ensure_registers_run_and_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, run_id="auto-registered")

    registered_path = ensure_run_registered("auto-news", config_path)
    env = RunEnvironment.open("auto-news")

    assert registered_path == config_path
    assert env.run_id == "auto-registered"
    assert (tmp_path / ".kairos" / "kairos.toml").exists()
    assert (tmp_path / ".kairos" / "state" / "run-index.json").exists()


def _write_minimal_config(root: Path, *, run_id: str) -> Path:
    config_path = root / f"{run_id}.toml"
    config_path.write_text(
        "\n".join(
            [
                "[run]",
                f'id = "{run_id}"',
                'mode = "backtest"',
                "",
                "[paths]",
                'runs_root = ".kairos/runs"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path
