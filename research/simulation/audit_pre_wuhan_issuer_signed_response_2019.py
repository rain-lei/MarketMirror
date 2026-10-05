"""Describe signed issuer-message response; this is not causal identification."""

from __future__ import annotations

import argparse
import json
import math
from fractions import Fraction
from pathlib import Path

from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research_outputs/pre_wuhan_issuer_valuation_2019_v1"
OUTPUT = ROOT / "research_outputs/pre_wuhan_issuer_signed_response_2019_v1.json"


def summarize(rows: list[dict]) -> dict:
    size = len(rows)
    returns = []
    for row in rows:
        before, after = row["price_before_minor"], row["price_after_minor"]
        if type(before) is not int or type(after) is not int or min(before, after) <= 0:
            raise ValueError("signed response requires positive integer prices")
        if not math.isfinite(row["hypothetical_issuer_valuation_bps"]):
            raise ValueError("signed response requires finite declared messages")
        if (row["accepted_buy"] - row["accepted_sell"] != row["strategy_net"] + row["background_net"]
                or row["filled_buy"] != row["filled_sell"] or row["filled_buy"] != row["matched_volume"]
                or row["strategy_filled_net"] + row["background_filled_net"] != 0):
            raise ValueError("signed response source flow does not balance")
        returns.append(Fraction(after - before, before))
    return {"company_days": size,
            "mean_message_bps": float(sum(Fraction(str(row["hypothetical_issuer_valuation_bps"])) for row in rows) / size) if size else None,
            "mean_return_bps": float(sum(returns) * 10000 / size) if size else None,
            "positive_return_fraction": sum(value > 0 for value in returns) / size if size else None,
            "negative_return_fraction": sum(value < 0 for value in returns) / size if size else None,
            "zero_return_fraction": sum(value == 0 for value in returns) / size if size else None,
            "total_matched_volume": sum(row["matched_volume"] for row in rows),
            "accepted_buy": sum(row["accepted_buy"] for row in rows),
            "accepted_sell": sum(row["accepted_sell"] for row in rows),
            "strategy_accepted_net": sum(row["strategy_net"] for row in rows),
            "background_accepted_net": sum(row["background_net"] for row in rows)}


def compute() -> dict:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    bindings = {str(SOURCE / "manifest.json"): file_sha256(SOURCE / "manifest.json")}
    for field in ("inputs", "code_sha256"):
        bindings.update(manifest[field])
    for name, info in manifest["artifacts"].items():
        path = SOURCE / name
        if path.parent != SOURCE:
            raise ValueError("signed response artifact path escaped source directory")
        bindings[str(path)] = info["sha256"]
    for name, digest in bindings.items():
        if file_sha256(Path(name)) != digest:
            raise ValueError("signed response source or code hash differs")
    result = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    variants, paired_panels = [], {}
    for variant in result["variants"][1:]:
        rows = variant["daily_asset_rows"]
        keys = {(row["stock_code"], row["trade_date"]) for row in rows}
        if len(rows) != 5289 or len(keys) != len(rows):
            raise ValueError("signed response company-day coverage differs")
        declarations = {(row["stock_code"], row["trade_date"]): (row["hypothetical_issuer_valuation_bps"], row["lagged_stock_volatility"]) for row in rows}
        strength = variant["name"].rsplit("_", 1)[0]
        if strength in paired_panels and declarations != paired_panels[strength]:
            raise ValueError("coverage comparison changed the message or lagged risk")
        paired_panels[strength] = declarations
        all_rows = summarize(rows)
        if abs(all_rows["mean_return_bps"] / 10000 - variant["comparison"]["synthetic"]["mean"]) > 1e-12:
            raise ValueError("rational signed response aggregate differs from published mean")
        groups = {"positive": summarize([row for row in rows if row["hypothetical_issuer_valuation_bps"] > 0]),
                  "negative": summarize([row for row in rows if row["hypothetical_issuer_valuation_bps"] < 0]),
                  "zero": summarize([row for row in rows if row["hypothetical_issuer_valuation_bps"] == 0])}
        if sum(group["company_days"] for group in groups.values()) != len(rows):
            raise ValueError("signed response groups omit or duplicate company-days")
        variants.append({"name": variant["name"], "all": all_rows, "by_message_sign": groups})
    return {"pipeline_version": "pre-wuhan-issuer-signed-response-diagnostic-v1", "variants": variants,
            "company_days_checked": sum(variant["all"]["company_days"] for variant in variants),
            "interpretation": "Post-hoc conditional description on consumed development data. Message signs are synthetic; a price asymmetry is observed, but its cause is not isolated by this diagnostic. Flow totals were checked for conservation, not independently reconstructed from orders here.",
            "inputs": dict(sorted(bindings.items())), "code_sha256": file_sha256(Path(__file__))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("signed response output must be in research_outputs")
    result = compute()
    payload = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if args.audit_existing:
        if output.read_bytes() != payload:
            raise ValueError("signed response archive differs byte-for-byte")
    else:
        with output.open("xb") as handle:
            handle.write(payload)
    print(json.dumps({"company_days_checked": result["company_days_checked"], "variants": [{"name": row["name"],
                       "mean_return_bps": row["all"]["mean_return_bps"],
                       "positive_message_return_bps": row["by_message_sign"]["positive"]["mean_return_bps"],
                       "negative_message_return_bps": row["by_message_sign"]["negative"]["mean_return_bps"]} for row in result["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
