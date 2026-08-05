from __future__ import annotations

import logging

from kairospy.application.support.launch.application.artifacts import LaunchOutputLog


def test_launch_output_log_emits_info_and_restores_root_level(tmp_path) -> None:
    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.WARNING)
    try:
        with LaunchOutputLog(tmp_path, stdout=None, stderr=None):
            logging.getLogger("kairospy.system").info("system=smoke phase=started")
        text = (tmp_path / "launch.log").read_text(encoding="utf-8")
        assert "system=smoke phase=started" in text
        assert root.level == logging.WARNING
    finally:
        root.setLevel(original_level)
