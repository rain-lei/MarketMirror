import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from openpyxl import Workbook

from research.data_pipeline.aggregate_qa_features import KEYWORDS
from research.data_pipeline.build_dataset import build_dataset
from research.data_pipeline.provenance import file_sha256
from research.semantic.annotation_pack import build_annotation_pack, canonical_hash, split_for_company
from research.semantic.holdout_pack import build_holdout_pack, filtered_rows
from research.semantic.prompt_contract import QUOTE_PROMPT_VERSION, prompt_path
from research.semantic.signal_validation import load_pack, read_jsonl, validate_labels


def codes_for(seed):
    groups = {name: [] for name in ("train", "validation", "test")}
    for number in range(1, 30000):
        code = str(number).zfill(6)
        split = split_for_company(code, seed, {"train": 60, "validation": 20, "test": 20})
        if len(groups[split]) < 8:
            groups[split].append(code)
        if all(len(rows) == 8 for rows in groups.values()):
            break
    return groups


def sampling_config(seed, source_hash, window):
    return {"run_id": seed, "qa_database": "dataset/dataset.sqlite",
            "qa_source_sha256": source_hash,
            "visibility_window": {"start_date": window[0], "end_date": window[1]},
            "seed": seed, "company_split_percent": {"train": 60, "validation": 20, "test": 20},
            "per_stage_stratum_quota": {"train": 1, "validation": 1, "test": 1},
            "selection_basis": "Synthetic company-disjoint temporal holdout fixture"}


class HoldoutPackTest(unittest.TestCase):
    def test_filter_excludes_development_id_and_visible_context_before_sampling(self):
        context = canonical_hash([{"source": "question", "text": "重复内容"}])
        rows = [("used-id", "000001", "time", "新内容", None, None, 0),
                ("new-id", "000002", "time", "重复内容", None, None, 0),
                ("keep-id", "000002", "time", "全新内容", None, None, 0)]
        audit = Counter()
        remaining = list(filtered_rows(rows, {"000001"}, {"used-id"}, {context}, audit))
        self.assertEqual([row[0] for row in remaining], ["keep-id"])
        self.assertEqual(audit["excluded_identity_rows"], 1)
        self.assertEqual(audit["excluded_exact_context_rows"], 1)

    def test_full_holdout_is_later_and_company_disjoint(self):
        dev_seed, holdout_seed = "synthetic-development", "synthetic-holdout"
        dev_codes = codes_for(dev_seed)
        holdout_codes = codes_for(holdout_seed)
        taken = {code for codes in dev_codes.values() for code in codes}
        if any(code in taken for codes in holdout_codes.values() for code in codes):
            groups = {name: [] for name in holdout_codes}
            for number in range(1, 30000):
                code = str(number).zfill(6)
                split = split_for_company(code, holdout_seed, {"train": 60, "validation": 20, "test": 20})
                if code not in taken and len(groups[split]) < 8:
                    groups[split].append(code)
                if all(len(codes) == 8 for codes in groups.values()):
                    break
            holdout_codes = groups
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = Workbook()
            sheet = book.active
            sheet.title = "问答"
            sheet.append(["股票代码", "提问时间", "提问内容", "上市公司是否回复", "回复时间", "回复内容"])
            for codes, question_time, reply_time in (
                    (dev_codes, "2020-01-10 10:00:00", "2020-01-11 10:00:00"),
                    (dev_codes, "2020-08-10 10:00:00", "2020-08-11 10:00:00"),
                    (holdout_codes, "2020-08-12 10:00:00", "2020-08-13 10:00:00")):
                for selected in codes.values():
                    for category, code in zip((*KEYWORDS, "none"), selected):
                        term = KEYWORDS[category][0] if category != "none" else "日常经营"
                        sheet.append([code, question_time, f"请说明{term}事项{code}", "已回复",
                                      reply_time, f"公司说明{term}事项{code}"])
            source = root / "qa.xlsx"
            book.save(source)
            book.close()
            build_dataset([source], root / "dataset")
            dev_config = sampling_config(dev_seed, file_sha256(source), ("2020-01-01", "2020-06-30"))
            dev_path = root / "development.json"
            dev_path.write_text(json.dumps(dev_config), encoding="utf-8")
            build_annotation_pack(dev_path, root / "development_pack")
            holdout_config = sampling_config(holdout_seed, file_sha256(source), ("2020-07-01", "2020-12-31"))
            holdout_config.update({"development_pack": "development_pack",
                                   "frozen_prompt_version": QUOTE_PROMPT_VERSION,
                                   "frozen_prompt_sha256": file_sha256(prompt_path(QUOTE_PROMPT_VERSION)),
                                   "frozen_model_id": "synthetic-model"})
            holdout_path = root / "holdout.json"
            holdout_path.write_text(json.dumps(holdout_config), encoding="utf-8")
            manifest = build_holdout_pack(holdout_path, root / "holdout_pack")
            dev, _ = load_pack(root / "development_pack")
            holdout, _ = load_pack(root / "holdout_pack")
            self.assertEqual(manifest["counts"]["items"], 48)
            self.assertEqual(len(holdout), 48)
            self.assertFalse({row["stock_code"] for row in dev.values()} &
                             {row["stock_code"] for row in holdout.values()})
            self.assertFalse({row["source_text_sha256"] for row in dev.values()} &
                             {row["source_text_sha256"] for row in holdout.values()})
            self.assertTrue(all(row["available_at"].startswith("2020-08-") for row in holdout.values()))
            labels = read_jsonl(root / "holdout_pack/annotation_template.jsonl")
            self.assertEqual(validate_labels(holdout, labels)["reviewed_items"], 0)
            with self.assertRaisesRegex(ValueError, "new empty"):
                build_holdout_pack(holdout_path, root / "holdout_pack")
            holdout_config["frozen_prompt_sha256"] = "0" * 64
            holdout_path.write_text(json.dumps(holdout_config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "frozen prompt hash"):
                build_holdout_pack(holdout_path, root / "different_pack")


if __name__ == "__main__":
    unittest.main()
