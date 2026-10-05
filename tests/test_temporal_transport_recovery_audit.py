"""Actual process completion is distinct from stale launch and partial exits."""
import json
from pathlib import Path
import tempfile
import unittest

from research.data_pipeline.audit_temporal_transport_sources import verify_source_completion
from research.data_pipeline.provenance import file_sha256


class TestTransportRecoveryCompletion(unittest.TestCase):
    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def fixture(self, name):
        root = Path(name).resolve()
        raw = root / "raw"
        out = root / "research_outputs"
        out.mkdir()
        helper = out / "acquire_temporal_transport_sources_20261003_v1.py"
        helper.write_text("frozen fixture producer", encoding="utf-8")
        helpers = [out / "resume_registered_transport_sources_20261003_v1.py", out / "recovery_fixture.py"]
        for path in helpers:
            path.write_text("recovery fixture helper", encoding="utf-8")
        claim_paths = [raw / "full" / f"run_claim_{i:03}.json" for i in range(3)]
        for i, path in enumerate(claim_paths):
            self.save(path, {"pid": 100 + i, "creation_ticks": 1000 + i, "protocol_sha256": "frozen"})
        kept = self.save(raw / "full/0.000001_fqt0/receipt.json", {"unchanged": True})
        processes = [out / f"temporal_transport_source_full_process_fixture{i}.json" for i in range(3)]
        command = ["python", "-X", "utf8", str(helper), "full", "--child"]
        self.save(processes[0], {"pid": 100, "stage": "full", "status": "RUNNING", "command": command,
            "actual_child_exit_code": None})
        regs = []
        for i in (1, 2):
            log = out / f"process{i}.txt"
            log.write_text("observed fixture exit", encoding="utf-8")
            reg = {"protocol_sha256": "frozen", "fixed_request_count": 1,
                "producer_or_protocol_changed": False, "existing_attempts_overwritten": False,
                "new_period_model_effects_evaluated": False, "related_original_python_node_processes_observed": [],
                "existing_terminal_receipts": 1, "existing_receipt_bindings": {kept.relative_to(root).as_posix(): file_sha256(kept)},
                "global_retry_reservations_already_spent": 1, "recovery_helper_sha256": file_sha256(helpers[i - 1])}
            if i == 1:
                reg.update(status="SAME_FROZEN_GRID_RECOVERY_AFTER_VERIFIED_ORIGINAL_OWNER_ABSENCE",
                    original_actual_exit_observed=False, original_exit_code=None, original_owner_at_recovery=None,
                    original_process_receipt=processes[0].relative_to(root).as_posix(),
                    original_process_receipt_sha256=file_sha256(processes[0]), original_claim_sha256=file_sha256(claim_paths[0]))
            else:
                reg.update(status="SAME_FROZEN_GRID_RECOVERY_AFTER_OBSERVED_INCOMPLETE_EXIT_AND_VERIFIED_OWNER_ABSENCE",
                    previous_actual_exit_observed=True, previous_actual_exit_code=1, previous_manifest_completed=False,
                    previous_process_receipt_path=processes[1].relative_to(root).as_posix(),
                    previous_process_receipt_sha256=file_sha256(processes[1]),
                    previous_owner_checks=[{"claim_path": p.relative_to(root).as_posix(), "claim_sha256": file_sha256(p),
                        "registered_pid": 100 + j, "registered_creation_ticks": 1000 + j,
                        "actual_identity_at_recovery": None} for j, p in enumerate(claim_paths[:i])],
                    recovery_helper_path=helpers[1].relative_to(root).as_posix())
            reg_path = self.save(out / f"recovery{i}.json", reg)
            regs.append(reg_path)
            self.save(processes[i], {"pid": 100 + i, "stage": "full", "command": command,
                "protocol_sha256": "frozen", "actual_child_exit_code": 1 if i == 1 else 0,
                "status": "OBSERVED_CHILD_EXIT_NONZERO" if i == 1 else "OBSERVED_CHILD_EXIT_ZERO",
                "log_path": log.relative_to(root).as_posix(), "log_sha256": file_sha256(log),
                "recovery_registration_path": reg_path.relative_to(root).as_posix(), "recovery_registration_sha256": file_sha256(reg_path)})
        return root, raw, processes, regs, kept

    def resign_chain(self, root, processes, regs):
        first = json.loads(processes[1].read_text())
        first["recovery_registration_sha256"] = file_sha256(regs[0])
        self.save(processes[1], first)
        last_reg = json.loads(regs[1].read_text())
        last_reg["previous_process_receipt_sha256"] = file_sha256(processes[1])
        self.save(regs[1], last_reg)
        last = json.loads(processes[2].read_text())
        last["recovery_registration_sha256"] = file_sha256(regs[1])
        self.save(processes[2], last)

    def check(self, root, raw, expected=0):
        return verify_source_completion(root, raw, {"requests": [{}], "total_extra_retry_ceiling": 100}, "frozen", expected)

    def test_complete_recovery_preserves_unobserved_original_and_observed_partial_exit(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            result = self.check(root, raw)
            self.assertEqual(result["run_claims_checked"], 3)
            self.assertEqual([r["actual_exit"] for r in result["preserved_process_lineage"]], [0, 1, None])
            self.assertEqual(json.loads(processes[0].read_text())["status"], "RUNNING")

    def test_live_same_original_owner_rejected_even_with_recomputed_hashes(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            reg = json.loads(regs[0].read_text())
            reg["original_owner_at_recovery"] = {"live": True, "creation_ticks": 1000}
            self.save(regs[0], reg)
            self.resign_chain(root, processes, regs)
            with self.assertRaisesRegex(ValueError, "original owner was live"):
                self.check(root, raw)

    def test_latest_running_receipt_does_not_prove_completion(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            process = json.loads(processes[2].read_text())
            process.update(status="RUNNING", actual_child_exit_code=None)
            self.save(processes[2], process)
            with self.assertRaisesRegex(ValueError, "exit is unobserved"):
                self.check(root, raw)

    def test_partial_nonzero_exit_is_not_whole_success(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            with self.assertRaisesRegex(ValueError, "exit differs"):
                self.check(root, raw, expected=1)

    def test_preserved_terminal_receipt_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            self.save(kept, {"unchanged": False})
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                self.check(root, raw)

    def test_missing_prior_owner_check_is_not_safe_recovery(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            reg = json.loads(regs[1].read_text())
            reg["previous_owner_checks"].pop()
            self.save(regs[1], reg)
            self.resign_chain(root, processes, regs)
            with self.assertRaisesRegex(ValueError, "owner check"):
                self.check(root, raw)

    def test_boolean_exit_is_not_an_actual_integer_exit(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            process = json.loads(processes[2].read_text())
            process["actual_child_exit_code"] = False
            self.save(processes[2], process)
            with self.assertRaisesRegex(ValueError, "exit type/status"):
                self.check(root, raw)

    def test_recovery_cannot_invent_zero_original_exit(self):
        with tempfile.TemporaryDirectory() as name:
            root, raw, processes, regs, kept = self.fixture(name)
            reg = json.loads(regs[0].read_text())
            reg.update(original_actual_exit_observed=True, original_exit_code=0)
            self.save(regs[0], reg)
            self.resign_chain(root, processes, regs)
            with self.assertRaisesRegex(ValueError, "fabricated"):
                self.check(root, raw)


if __name__ == "__main__":
    unittest.main()
