from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from kairospy.application.support.launch.environment import LaunchEnvironment, ensure_launch_registered
from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.usecases.strategy.protocol import StrategyBase


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


def test_launch_environment_launches_custom_csv_data_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "news.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "news-launch"',
                'mode = "backtest"',
                "",
                "[paths]",
                'launches_root = ".kairos/launches"',
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

    env = LaunchEnvironment.from_config(config_path)
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

    result = env.launch(strategy=strategy, sources=[news])

    assert result.runtime.strategy_id == "news-test"
    assert result.runtime.event_count == 2
    assert strategy.seen[0].kind == "news.sentiment"
    assert strategy.seen[0].value["sentiment"] == 0.72
    group_dir = tmp_path / ".kairos" / "launches" / "backtest" / "news-launch"
    assert env.instance_dir.parent == group_dir / "instances"
    assert (env.instance_dir / "summary.json").exists()
    assert "Launch Environment" in (env.instance_dir / "launch.log").read_text(encoding="utf-8")
    current = json.loads((group_dir / "current.json").read_text(encoding="utf-8"))
    assert current["launch_instance_id"] == env.launch_instance_id
    assert current["directory"] == str(env.instance_dir)


def test_launch_environment_launches_custom_async_data_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "live-news.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
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

    env = LaunchEnvironment.from_config(config_path)
    strategy = NewsStrategy(threshold=float(env.params["threshold"]))
    source = env.sources.async_events("realtime-news", realtime_events())

    result = env.launch(strategy=strategy, sources=[source])

    assert result.runtime.strategy_id == "news-test"
    assert strategy.seen == [{"sentiment": 0.81}]


def test_launch_environment_clock_callbacks_use_strategy_time(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, launch_id="clock-launch")
    env = LaunchEnvironment.from_config(config_path)
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

    result = env.launch(strategy=strategy, sources=[data], clocks=[clock])

    assert result.runtime.event_count == 4
    assert strategy.callbacks == [
        ("clock", datetime.fromisoformat("2026-01-01T10:00:00+00:00")),
        ("data", datetime.fromisoformat("2026-01-01T10:00:01+00:00")),
        ("clock", datetime.fromisoformat("2026-01-01T10:00:02+00:00")),
    ]


def test_launch_environment_interval_clock_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, launch_id="interval-clock")
    env = LaunchEnvironment.from_config(config_path)
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


def test_launch_environment_uses_project_timezone_for_naive_clock_times(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "\n".join(["[project]", 'name = "tz-test"', 'timezone = "Asia/Shanghai"', 'language = "zh"']),
        encoding="utf-8",
    )
    config_path = _write_minimal_config(tmp_path, launch_id="timezone-clock")
    env = LaunchEnvironment.from_config(config_path)
    clock = env.clocks.interval(
        "local-clock",
        start="2026-01-01T09:30:00",
        end="2026-01-01T09:31:00",
        every="1m",
    )

    async def collect():
        return [event async for event in clock.events()]

    import asyncio

    events = asyncio.run(collect())

    assert env.timezone_name == "Asia/Shanghai"
    assert env.language == "zh-CN"
    assert env.normalized_config["project"]["timezone"] == "Asia/Shanghai"
    assert env.normalized_config["project"]["language"] == "zh-CN"
    assert [event.time.isoformat() for event in events] == [
        "2026-01-01T09:30:00+08:00",
        "2026-01-01T09:31:00+08:00",
    ]


def test_launch_environment_uses_project_timezone_for_naive_csv_times(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text(
        "\n".join(["[project]", 'name = "tz-test"', 'timezone = "America/New_York"']),
        encoding="utf-8",
    )
    config_path = _write_minimal_config(tmp_path, launch_id="timezone-csv")
    (tmp_path / "news.csv").write_text(
        "\n".join(
            [
                "available_at,observed_at,symbol,headline,sentiment",
                "2026-01-01T09:30:00,2026-01-01T09:29:30,BTC/USDT,local open,0.72",
            ]
        ),
        encoding="utf-8",
    )
    env = LaunchEnvironment.from_config(config_path)
    source = env.sources.csv_events(
        "news.csv",
        kind="news.sentiment",
        time_field="available_at",
        observed_at_field="observed_at",
        available_at_field="available_at",
        subject_id_field="symbol",
    )

    async def collect():
        return [event async for event in source.events()]

    import asyncio

    events = asyncio.run(collect())

    assert env.parse_time("2026-01-01T09:30:00").isoformat() == "2026-01-01T09:30:00-05:00"
    assert events[0].time.isoformat() == "2026-01-01T09:30:00-05:00"
    assert events[0].payload.observed_at.isoformat() == "2026-01-01T09:29:30-05:00"


def test_launch_environment_resolves_registered_launch_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos" / "state").mkdir(parents=True)
    (tmp_path / ".kairos" / "kairos.toml").write_text("[project]\nname = \"env-test\"\n", encoding="utf-8")
    config_path = tmp_path / "registered.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                'id = "registered-launch"',
                'mode = "backtest"',
                "",
                "[params]",
                "threshold = 0.7",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".kairos" / "state" / "launch-index.json").write_text(
        '{\n  "schema_version": 1,\n  "launches": {\n    "news-registered": {"config": "registered.toml"}\n  }\n}\n',
        encoding="utf-8",
    )

    env = LaunchEnvironment.from_config("news-registered")

    assert env.launch_id == "registered-launch"
    assert env.params["threshold"] == 0.7


def test_launch_environment_rejects_launch_group_as_instance_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, launch_id="group-rejected")
    group_dir = tmp_path / ".kairos" / "launches" / "backtest" / "group-rejected"

    try:
        LaunchEnvironment.from_config(config_path, instance_dir=group_dir)
    except ValueError as error:
        assert "launch group directory" in str(error)
    else:
        raise AssertionError("LaunchEnvironment accepted a launch group as an instance directory")


def test_launch_environment_accepts_explicit_instance_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, launch_id="explicit-instance")
    instance_dir = tmp_path / ".kairos" / "launches" / "backtest" / "explicit-instance" / "instances" / "manual-1"

    env = LaunchEnvironment.from_config(config_path, instance_dir=instance_dir)

    assert env.instance_dir == instance_dir
    assert env.launch_instance_id == "manual-1"


def test_launch_environment_ensure_registers_launch_and_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = _write_minimal_config(tmp_path, launch_id="auto-registered")

    registered_path = ensure_launch_registered("auto-news", config_path)
    env = LaunchEnvironment.open("auto-news")

    assert registered_path == config_path
    assert env.launch_id == "auto-registered"
    assert (tmp_path / ".kairos" / "kairos.toml").exists()
    assert (tmp_path / ".kairos" / "state" / "launch-index.json").exists()


def _write_minimal_config(root: Path, *, launch_id: str) -> Path:
    config_path = root / f"{launch_id}.toml"
    config_path.write_text(
        "\n".join(
            [
                "[launch]",
                f'id = "{launch_id}"',
                'mode = "backtest"',
                "",
                "[paths]",
                'launches_root = ".kairos/launches"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path
