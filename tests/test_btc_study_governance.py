import unittest

from research.btc_study_governance import PROFILES


class BtcStudyGovernanceTest(unittest.TestCase):
    def test_every_legacy_study_profile_has_unique_identity_and_claim(self):
        self.assertEqual(len({profile.study_id for profile in PROFILES}),len(PROFILES))
        self.assertTrue(all(profile.hypothesis and profile.claim for profile in PROFILES))
        self.assertTrue(any(profile.execution.value=="taker" for profile in PROFILES))


if __name__=="__main__":unittest.main()
