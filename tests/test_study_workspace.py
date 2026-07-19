from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from trading.research import StudyWorkspace, StudyWorkspaceRepository, StudyWorkspaceStatus


class StudyWorkspaceTests(unittest.TestCase):
    def test_sandbox_can_be_frozen_without_becoming_validation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = StudyWorkspaceRepository(directory)
            repository.create(StudyWorkspace(
                "study", "1.0.0", "hypothesis", "release", "a"*64, "available_time",
                "2025-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                created_at=datetime(2026, 7, 17, tzinfo=timezone.utc).isoformat(),
            ))
            frozen = repository.freeze("study", "1.0.0")

            self.assertEqual(repository.load("study", "1.0.0").status, StudyWorkspaceStatus.SANDBOX)
            self.assertTrue((frozen/"study_candidate.json").exists())
            self.assertTrue((frozen/"manifest.json").exists())
            self.assertFalse((Path(directory)/"studies"/"study").exists())


if __name__ == "__main__":
    unittest.main()
