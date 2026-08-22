from __future__ import annotations

from io import StringIO
import time

from kairospy.surface.cli.activity import TerminalActivity


class TtyBuffer(StringIO):
    def isatty(self) -> bool:
        return True


def test_terminal_activity_shows_elapsed_time_and_completion() -> None:
    output = TtyBuffer()
    activity = TerminalActivity(
        "查询账户余额",
        output,
        delay_seconds=0,
        interval_seconds=0.001,
    )

    activity.start()
    time.sleep(0.01)
    activity.finish(succeeded=True)

    rendered = output.getvalue()
    assert "查询账户余额" in rendered
    assert "s" in rendered
    assert "✓" in rendered


def test_terminal_activity_stays_silent_off_tty() -> None:
    output = StringIO()
    activity = TerminalActivity("查询账户余额", output, delay_seconds=0)

    activity.start()
    activity.finish(succeeded=True)

    assert output.getvalue() == ""
