"""Reopen complete original source fields before each transport ledger audit."""
import gzip
import json

from .temporal_information_study import ROOT, require, read_gzip
from ..data_pipeline.audit_temporal_transport_numeric import audit_numeric_contract
from ..data_pipeline.temporal_transport_trade_status import load_reviewed_pdf_intervals, reconcile_transport_positions


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def audit_source_inputs(cfg, numeric, mask):
    original = rows(ROOT / cfg["original_source_positions_path"])
    sources = rows(ROOT / cfg["reconciled_source_positions_path"])
    raw_stocks = [row for row in original if row["kind"] == "stock"]
    benchmark = [row for row in original if row["kind"] == "benchmark"]
    expected = {(stock, fqt, day) for stock in cfg["stocks"] for fqt in (0, 1, 2) for day in cfg["source_trade_dates"]}
    raw_index = {(row["secid"][2:], row["fqt"], row["trade_date"]): row for row in raw_stocks}
    require(len(raw_stocks) == len(raw_index) == len(expected) and set(raw_index) == expected,
        "Original transport raw stock grid changed")
    require(len(sources) == len(raw_stocks), "Reconciled transport grid omitted source positions")
    for row in sources:
        prior = raw_index[row["secid"][2:], row["fqt"], row["trade_date"]]
        require(all(key in row and row[key] == value for key, value in prior.items()), "Transport reconciliation altered raw fields")
    evidence = load_reviewed_pdf_intervals(ROOT, cfg["issuer_review_paths"], cfg["source_trade_dates"], cfg["stocks"])
    rebuilt, reconciliation = reconcile_transport_positions(raw_stocks, cfg["source_trade_dates"], cfg["stocks"], evidence,
        require_endpoints=True)
    require(rebuilt == sources and reconciliation["all_endpoint_quotes_corroborated"] is True,
        "Transport status differs from issuer PDF clauses and genuine endpoint quotes")
    windows = read_gzip(ROOT / cfg["residual_rank_windows_path"])["windows"]
    result = audit_numeric_contract(sources, benchmark, cfg["stocks"], cfg["source_trade_dates"], cfg["dates"],
        numeric, windows, mask, cfg["history_window"])
    result["original_raw_fields_independently_matched_positions"] = len(sources)
    result["strict_primary_pdf_resumption_endpoint_reconciliation"] = reconciliation
    return result
