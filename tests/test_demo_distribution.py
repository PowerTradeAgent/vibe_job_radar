"""Distribution regression: checked-out demo bytes must match their evidence hashes."""
import unittest
from pathlib import Path

from vibe_job_radar.config import load_config
from vibe_job_radar.synthesis import load_candidate


class DemoDistributionTests(unittest.TestCase):
    def test_shipped_demo_evidence_is_byte_identical_on_every_os(self):
        root = Path(__file__).resolve().parents[1]
        candidate = load_candidate(root / "examples" / "candidate.synthetic.json", load_config())
        self.assertTrue(candidate["evidence"])
        for evidence in candidate["evidence"]:
            self.assertEqual(evidence["scope"], "synthetic")
            self.assertEqual(evidence["integrity_status"], "file_hash_verified_content_not_audited")


if __name__ == "__main__":
    unittest.main()
