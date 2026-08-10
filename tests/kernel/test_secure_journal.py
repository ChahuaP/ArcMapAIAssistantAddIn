"""DPAPI-at-rest and hash-chain checks for the JournalStore security contract."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
import json
from pathlib import Path

from gateway_py3.kernel import contracts
from gateway_py3.kernel.contracts import CallerIdentity, RequestEnvelope, TargetSelector
from gateway_py3.kernel.store import JournalStore
from gateway_py3.model_runtime.credentials import DpapiCredentialVault
from gateway_py3.model_runtime.configuration import ModelConfigurationStore


def _request() -> RequestEnvelope:
    return RequestEnvelope(
        session_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), text="sensitive user request",
        caller=CallerIdentity(user_id="u", tenant_id="t", role="operator"),
        target_selector=TargetSelector(bridge_pid=1, bridge_port=2, arcmap_pid=3,
                                       hwnd=4, deployment_hash="deployment"),
    )


class SecureJournalTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "journal.sqlite"
        self.store = JournalStore(self.path)
        request = _request()
        self.store.create_session(request.session_id, request.caller.tenant_id)
        self.run_id = self.store.create_run(request)["run_id"]

    def test_event_payload_is_dpapi_encrypted_and_chain_verifies(self):
        self.store.append_event(self.run_id, "audit", "received", {"secret": "not-on-disk"})
        self.store.reserve_model_call(
            "call-key", self.run_id, "minimax", "MiniMax-M3", "request-hash",
            {"prompt": "not-on-disk", "api_key": "never-plaintext"},
        )
        with sqlite3.connect(str(self.path)) as conn:
            payload, previous_hash, chain_hash = conn.execute(
                "SELECT payload_json, previous_hash, chain_hash FROM run_events "
                "WHERE run_id=? ORDER BY event_seq DESC LIMIT 1", (self.run_id,)
            ).fetchone()
            run_text = conn.execute("SELECT text FROM runs WHERE run_id=?", (self.run_id,)).fetchone()[0]
            ledger = conn.execute("SELECT ledger_json FROM model_calls WHERE call_key='call-key'").fetchone()[0]
        self.assertTrue(payload.startswith("dpapi:v1:"))
        self.assertNotIn("not-on-disk", payload)
        self.assertNotIn("sensitive user request", run_text)
        self.assertNotIn("not-on-disk", ledger)
        self.assertNotIn("never-plaintext", ledger)
        self.assertTrue(previous_hash)
        self.assertTrue(chain_hash)
        self.assertEqual("not-on-disk", self.store.run_events(self.run_id)[-1]["payload"]["secret"])

    def test_chain_tampering_fails_on_reopen(self):
        self.store.append_event(self.run_id, "audit", "received", {"secret": "protected"})
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("UPDATE run_events SET chain_hash='tampered' WHERE run_id=?", (self.run_id,))
            conn.commit()
        with self.assertRaises(RuntimeError):
            JournalStore(self.path)

    def test_anchor_rejects_tail_deletion_full_deletion_and_anchor_deletion(self):
        for mutation in ("tail", "all", "anchor"):
            with self.subTest(mutation=mutation):
                path = Path(tempfile.mkdtemp()) / "journal.sqlite"
                store = JournalStore(path)
                request = _request()
                store.create_session(request.session_id, request.caller.tenant_id)
                run_id = store.create_run(request)["run_id"]
                store.append_event(run_id, "audit", "received", {"fact": mutation})
                with sqlite3.connect(str(path)) as conn:
                    if mutation == "tail":
                        conn.execute(
                            "DELETE FROM run_events WHERE event_seq=(SELECT MAX(event_seq) FROM run_events WHERE run_id=?)",
                            (run_id,),
                        )
                    elif mutation == "all":
                        conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
                    else:
                        conn.execute("DELETE FROM run_event_anchors WHERE run_id=?", (run_id,))
                    conn.commit()
                with self.assertRaises(RuntimeError):
                    JournalStore(path)

    def test_prepared_publication_has_no_plaintext_target_column(self):
        with sqlite3.connect(str(self.path)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(publication_prepared)")}
        self.assertNotIn("target_unit_path", columns)

    def test_live_read_rejects_complete_projection_tampering(self):
        for field, value in (
            ("session_id", "other-session"),
            ("outcome_json", "dpapi:v1:tampered"),
            ("created_at", 1.0),
            ("updated_at", 1.0),
        ):
            with self.subTest(field=field):
                path = Path(tempfile.mkdtemp()) / "journal.sqlite"
                store = JournalStore(path)
                request = _request()
                store.create_session(request.session_id, request.caller.tenant_id)
                run_id = store.create_run(request)["run_id"]
                store.append_event(
                    run_id, "failed", "context", {"reason": "test"},
                    outcome=contracts.outcome_failed(
                        contracts.INFRASTRUCTURE_FAILED, "context", "test", "test",
                    ),
                )
                with sqlite3.connect(str(path)) as conn:
                    conn.execute("UPDATE runs SET %s=? WHERE run_id=?" % field, (value, run_id))
                    conn.commit()
                with self.assertRaises(RuntimeError):
                    store.get_run(run_id)

    def test_filtered_reads_reject_hidden_tampered_runs(self):
        request = _request()
        original_session_id = request.session_id
        other_session_id = str(uuid.uuid4())
        self.store.create_session(other_session_id, request.caller.tenant_id)
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute(
                "UPDATE runs SET session_id=? WHERE run_id=?",
                (other_session_id, self.run_id),
            )
            conn.commit()
        with self.assertRaises(RuntimeError):
            self.store.list_recent_runs(session_id=original_session_id)
        with self.assertRaises(RuntimeError):
            self.store.session_messages(original_session_id)

    def test_active_run_read_rejects_hidden_stage_tampering(self):
        with sqlite3.connect(str(self.path)) as conn:
            conn.execute("UPDATE runs SET stage=? WHERE run_id=?", (contracts.PUBLISHED, self.run_id))
            conn.commit()
        with self.assertRaises(RuntimeError):
            self.store.list_active_runs()

    def test_live_read_paths_reject_tail_all_and_anchor_deletion(self):
        for mutation in ("tail", "all", "anchor"):
            with self.subTest(mutation=mutation):
                path = Path(tempfile.mkdtemp()) / "journal.sqlite"
                store = JournalStore(path)
                request = _request()
                store.create_session(request.session_id, request.caller.tenant_id)
                run_id = store.create_run(request)["run_id"]
                store.append_event(run_id, "audit", "received", {"fact": mutation})
                with sqlite3.connect(str(path)) as conn:
                    if mutation == "tail":
                        conn.execute(
                            "DELETE FROM run_events WHERE event_seq=(SELECT MAX(event_seq) "
                            "FROM run_events WHERE run_id=?)", (run_id,),
                        )
                    elif mutation == "all":
                        conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
                    else:
                        conn.execute("DELETE FROM run_event_anchors WHERE run_id=?", (run_id,))
                    conn.commit()
                with self.assertRaises(RuntimeError):
                    store.get_run(run_id)
                with self.assertRaises(RuntimeError):
                    store.list_recent_runs(session_id=request.session_id)
                with self.assertRaises(RuntimeError):
                    store.session_messages(request.session_id)
                with self.assertRaises(RuntimeError):
                    store.events_after(99999)

    def test_credential_vault_round_trip_and_plaintext_rejection(self):
        path = Path(tempfile.mkdtemp()) / "credentials.json"
        vault = DpapiCredentialVault(path)
        vault.put("credential:minimax", "secret")
        self.assertTrue(vault.has("credential:minimax"))
        self.assertEqual("secret", vault.get("credential:minimax"))
        self.assertNotIn("secret", path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({
            "schema": "geopilot-credential-vault-v1",
            "credentials": {"credential:minimax": "plaintext"},
        }), encoding="utf-8")
        with self.assertRaises(ValueError):
            DpapiCredentialVault(path).get("credential:minimax")

    def test_public_model_configuration_exposes_bindings_but_not_secrets(self):
        root = Path(tempfile.mkdtemp())
        vault = DpapiCredentialVault(root / "credentials.json")
        config = ModelConfigurationStore(root / "model_configuration.json", vault)
        config.save({
            "connections": [{
                "connection_id": "minimax-official",
                "provider_type": "minimax",
                "endpoint": "https://api.minimaxi.com/v1",
                "enabled_models": ["MiniMax-M3"],
                "api_key": "secret",
            }],
            "agent_model_plan": {
                role: {
                    "connection_id": "minimax-official",
                    "model_id": "MiniMax-M3",
                }
                for role in ("compiler", "planner", "auditor", "repairer")
            },
        })
        public = config.public()
        serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("credential:minimax-official", serialized)
        self.assertEqual(
            {"compiler", "planner", "auditor", "repairer"},
            set(public["agent_model_plan"]),
        )
        for obsolete in ({"provider": "x"}, {"model": "x"}, {"base_url": "x"}):
            with self.assertRaises(ValueError):
                config.save(obsolete)
