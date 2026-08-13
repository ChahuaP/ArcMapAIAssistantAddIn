from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from experiments.supervisor import CampaignError, _assert_same_provenance, _json_digest
from experiments.synthetic_city.source_provenance import repository_state


class RepositoryProvenanceTest(unittest.TestCase):
    def test_clean_means_no_tracked_or_untracked_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("one", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            self.assertTrue(repository_state(root).clean)
            (root / "tracked.txt").write_text("two", encoding="utf-8")
            self.assertFalse(repository_state(root).clean)
            subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=root, check=True)
            (root / "untracked.txt").write_text("one", encoding="utf-8")
            first = repository_state(root)
            self.assertFalse(first.clean)
            (root / "untracked.txt").write_text("two", encoding="utf-8")
            self.assertNotEqual(first.digest, repository_state(root).digest)

    def test_every_frozen_campaign_dimension_rejects_resume_drift(self):
        base = {
            "repository": {"digest": "repo"},
            "runtime_identity": {"planner": {"provider": "minimax", "model": "MiniMax-M3",
                                                  "connection_id": "c", "endpoint_fingerprint": "e",
                                                  "deployment_fingerprint": "d"}},
            "arcmap_target": {"arcmap_pid": 1, "deployment_hash": "a"},
            "dataset_manifest_sha256": "m", "dataset_manifest": {"files": []},
            "experiment_contract_sha256": "s",
        }
        base["digest"] = _json_digest(base)
        changes = [
            ("repository", {"digest": "changed"}),
            ("runtime_identity", {"planner": {"provider": "minimax", "model": "MiniMax-M3",
                                                 "connection_id": "c2", "endpoint_fingerprint": "e2",
                                                 "deployment_fingerprint": "d2"}}),
            ("arcmap_target", {"arcmap_pid": 2, "deployment_hash": "b"}),
            ("dataset_manifest_sha256", "changed"),
            ("experiment_contract_sha256", "changed"),
        ]
        for key, value in changes:
            current = copy.deepcopy(base)
            current[key] = value
            current["digest"] = _json_digest({k: v for k, v in current.items() if k != "digest"})
            with self.subTest(key=key), self.assertRaises(CampaignError):
                _assert_same_provenance(base, current)
        _assert_same_provenance(base, copy.deepcopy(base))


if __name__ == "__main__":
    unittest.main()
