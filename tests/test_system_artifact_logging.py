from __future__ import annotations

import logging

from kairospy.application.support.system.artifacts.logging import LaunchOutputLog


def test_launch_output_log_captures_python_logging(tmp_path) -> None:
    logger = logging.getLogger("tests.launch-output-log")
    logger.setLevel(logging.INFO)

    with LaunchOutputLog(tmp_path):
        logger.info("strategy received quote symbol=%s ask=%s", "AAPL", "101.25")

    text = (tmp_path / "launch.log").read_text(encoding="utf-8")
    assert "INFO tests.launch-output-log: strategy received quote symbol=AAPL ask=101.25" in text
