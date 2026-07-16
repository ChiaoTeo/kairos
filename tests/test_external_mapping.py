from __future__ import annotations

from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from trading.catalog.external import ExternalInstrumentMapping, ExternalMappingRepository
from trading.domain.identity import InstrumentId


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


class ExternalMappingTests(unittest.TestCase):
    def test_round_trip_and_point_in_time_resolution(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "mappings.json"
            repository = ExternalMappingRepository(path)
            repository.add(ExternalInstrumentMapping("massive", "options", "O:SPXW", InstrumentId("option:us:SPXW"), NOW))
            repository.save()
            loaded = ExternalMappingRepository(path)
            self.assertEqual(loaded.resolve("massive", "options", "O:SPXW", NOW), InstrumentId("option:us:SPXW"))

    def test_overlapping_conflict_is_rejected(self):
        repository = ExternalMappingRepository("/tmp/does-not-exist-mapping-test.json")
        repository.add(ExternalInstrumentMapping("massive", "options", "O:SPXW", InstrumentId("one"), NOW))
        with self.assertRaises(ValueError):
            repository.add(ExternalInstrumentMapping("massive", "options", "O:SPXW", InstrumentId("two"), NOW))


if __name__ == "__main__":
    unittest.main()
