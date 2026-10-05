import hashlib
import unittest
from datetime import datetime

from research.semantic.annotation_pack import VERSION as PACK_VERSION, canonical_hash
from research.semantic.audit_wuhan_snapshot_source import check_selected_items


class WuhanSnapshotSourceAuditTest(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime.fromisoformat("2020-01-22T23:59:59+08:00")
        self.selected = {"000001": (
            "qa-1", "2020-01-20T09:00:00+08:00", "question",
            "2020-01-21T10:00:00+08:00", "reply")}
        segments = [{"source": "question", "text": "question"},
                    {"source": "reply", "text": "reply"}]
        self.item = {"item_id": hashlib.sha256(f"qa-1|reply|{PACK_VERSION}".encode()).hexdigest(),
                     "qa_id": "qa-1", "stock_code": "000001", "split": "pre_event_snapshot",
                     "stage": "reply", "available_at": self.selected["000001"][3],
                     "question_available_at": self.selected["000001"][1],
                     "reply_available_at": self.selected["000001"][3],
                     "selection_stratum": "none", "segments": segments,
                     "source_text_sha256": canonical_hash(segments)}

    def test_exact_visible_reply_matches_source(self):
        check_selected_items({self.item["item_id"]: self.item}, self.selected, self.cutoff)

    def test_future_reply_and_unrelated_fields_are_rejected(self):
        future = {**self.item, "available_at": "2020-01-24T10:00:00+08:00"}
        with self.assertRaisesRegex(ValueError, "differs"):
            check_selected_items({future["item_id"]: future}, self.selected, self.cutoff)
        contaminated = {**self.item, "financial_snapshot": 123}
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            check_selected_items({contaminated["item_id"]: contaminated}, self.selected, self.cutoff)

    def test_replaced_reply_text_is_rejected(self):
        changed = {**self.item, "segments": [self.item["segments"][0],
                                              {"source": "reply", "text": "future reply"}]}
        with self.assertRaisesRegex(ValueError, "differs"):
            check_selected_items({changed["item_id"]: changed}, self.selected, self.cutoff)
