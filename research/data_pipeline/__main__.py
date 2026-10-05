"""Command entry point for the research data pipeline."""

import argparse
import json
from pathlib import Path

from .aggregate_qa_features import aggregate_workbook
from .profile_sources import profile_workbook
from .extract_financial import extract_financial
from .build_dataset import build_dataset
from .asof_features import export_asof_features
from .market_data import import_market


def main() -> None:
    parser = argparse.ArgumentParser(description="MarketMirror research data pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    profile = subparsers.add_parser("profile")
    profile.add_argument("sources", nargs="+", type=str)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("input", type=str)
    aggregate.add_argument("output", type=str)
    financial = subparsers.add_parser("financial")
    financial.add_argument("input", type=Path)
    financial.add_argument("--output-dir", type=Path, required=True)
    dataset = subparsers.add_parser("dataset")
    dataset.add_argument("sources", nargs="+", type=Path)
    dataset.add_argument("--financial-db", action="append", type=Path, default=[])
    dataset.add_argument("--output-dir", type=Path, required=True)
    features = subparsers.add_parser("features")
    features.add_argument("database", type=Path)
    features.add_argument("--window-start", required=True)
    features.add_argument("--as-of", required=True)
    features.add_argument("--stock-code")
    features.add_argument("--output", type=Path, required=True)
    market = subparsers.add_parser("market")
    market.add_argument("config", type=Path)
    market.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "profile":
        for source in args.sources:
            print(profile_workbook(Path(source)))
    elif args.command == "aggregate":
        print(aggregate_workbook(Path(args.input), Path(args.output)))
    elif args.command == "financial":
        report = extract_financial(args.input, args.output_dir)
        print(json.dumps(report["counts"], ensure_ascii=False))
    elif args.command == "dataset":
        report = build_dataset(args.sources, args.output_dir, args.financial_db)
        print(json.dumps(report["counts"], ensure_ascii=False))
    elif args.command == "market":
        report = import_market(args.config, args.output_dir)
        print(json.dumps(report["counts"], ensure_ascii=False))
    else:
        report = export_asof_features(args.database, args.output, args.window_start, args.as_of, args.stock_code)
        print(json.dumps({key: report[key] for key in ("company_rows", "question_rows", "visible_reply_rows")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
