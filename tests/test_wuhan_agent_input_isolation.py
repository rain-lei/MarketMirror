import hashlib
import unittest

from research.semantic.audit_wuhan_agent_inputs import expected_text


class WuhanAgentInputIsolationTest(unittest.TestCase):
    def test_reply_becomes_visible_only_after_cutoff(self):
        item = {"item_id": "item-1", "available_at": "2020-02-10T17:00:00+08:00"}
        digest = hashlib.sha256(b"item-1").hexdigest()
        rows = {"000001": item}
        self.assertEqual(expected_text("000001", "2020-02-09", rows, 0.25),
                         (0.0, "semantic:none-through:2020-02-09"))
        self.assertEqual(expected_text("000001", "2020-02-10", rows, 0.25),
                         (0.25, f"semantic:{digest}"))
        self.assertEqual(expected_text("000002", "2020-02-10", rows, 1.0),
                         (0.0, "semantic:none-through:2020-02-10"))
