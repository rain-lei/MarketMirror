"""Freeze and run financing-factor increments without altering prior studies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import issuer_financing_increment as diagnostic

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_financing_increment_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_financing_increment_2019_v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def compute():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if (config["blocks"] != diagnostic.BLOCKS or config["agent_signal_enabled"] is not False
            or config["interactions"] != {k: list(v) for k, v in diagnostic.INTERACTIONS.items()}):
        raise ValueError("prespecified financing blocks/interactions or disabled gate changed")
    sources, bindings = {}, {str(CONFIG): digest(CONFIG)}

    def bind(path, expected):
        key = str((ROOT / path).resolve())
        if key in bindings and bindings[key] != expected:
            raise ValueError("conflicting financing-study source bindings")
        bindings[key] = expected

    for name, record in config["inputs"].items():
        path = (CONFIG.parent / record["path"]).resolve()
        bind(path, record["sha256"])
        sources[name] = json.loads(path.read_text(encoding="utf-8"))
    for name in ("base_manifest", "financing_states", "financing_state_audit"):
        for field in ("inputs", "code_sha256"):
            for path, sha in sources[name][field].items():
                bind(path, sha)
    if (sources["base_independent_audit"]["status"] != "PASS"
            or sources["financing_state_audit"]["status"] != "PASS_STATE_CONTRACT"
            or sources["financing_states"]["agent_signal_enabled"] is not False):
        raise ValueError("base or financing independent source audit does not pass")
    for name, artifact in sources["base_manifest"]["artifacts"].items():
        expected = "base_panel" if name == "panel.json" else "base_results"
        if config["inputs"][expected]["sha256"] != artifact["sha256"]:
            raise ValueError("base panel/results differ from their frozen manifest")
    paths = [Path(__file__).resolve(), Path(diagnostic.__file__).resolve(), ROOT / "research/baselines/issuer_financial_increment.py"]
    code = {str(p): digest(p) for p in paths}

    def verify():
        for path, expected in {**bindings, **code}.items():
            if digest(path) != expected:
                raise ValueError("financing input or producer changed: " + path)

    verify()
    base = sources["base_panel"]
    if (base["split"] != sources["base_results"]["split"] or len(base["parent_stock_codes"]) != config["expected_parent_companies"]
            or base["split"] != config["frozen_split"] or base["agent_signal_enabled"] is not False):
        raise ValueError("frozen financing baseline cohort/date split differs")
    companies = sources["financing_states"]["companies"]
    financing = {c["stock_code"]: c for c in companies}
    if len(financing) != len(companies):
        raise ValueError("financing states have duplicate companies")
    panels, results = {}, {}
    for block in config["blocks"]:
        panel = diagnostic.extend_panel(base, financing, block)
        panels[block] = panel
        results[block] = diagnostic.fit_block(panel, base["split"], block)
    primary = panels["primary_restricted_cash_buffer"]
    interactions = panels["secondary_restriction_market_interactions"]
    if [(r["stock_code"], r["trade_date"], r["paired_exclusion_reason"]) for r in primary] != [
            (r["stock_code"], r["trade_date"], r["paired_exclusion_reason"]) for r in interactions]:
        raise ValueError("restriction and lagged-interaction blocks must have identical eligibility")
    artifacts = {
        "panel.json": {"pipeline_version": config["version"], "parent_stock_codes": base["parent_stock_codes"],
                       "split": base["split"], "blocks": panels, "agent_signal_enabled": False},
        "results.json": {"pipeline_version": config["version"], "split": base["split"], "blocks": results,
                         "limitations": config["limitations"], "agent_signal_enabled": False,
                         "interpretation": "All three prespecified financing blocks retained. Same-row comparisons within each block; existing development dates, no new blind validation, complete restricted-cash claim, policy causality or calibrated Agent effect."},
    }
    artifacts["manifest.json"] = {
        "pipeline_version": config["version"], "inputs": dict(sorted(bindings.items())), "code_sha256": code,
        "artifacts": {n: {"sha256": hashlib.sha256(serialize(v)).hexdigest()} for n, v in artifacts.items()},
    }
    verify()
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("financing increment requires a direct research_outputs directory")
    artifacts = compute()
    if args.audit_existing:
        if any((output / name).read_bytes() != serialize(value) for name, value in artifacts.items()):
            raise ValueError("financing increment differs from frozen reconstruction")
        print("Financing increment artifacts rebuilt byte-identically.")
    else:
        output.mkdir(exist_ok=False)
        for name, value in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(serialize(value))
    print(json.dumps({k: {"training_rows": r["training_rows"], "evaluation_rows": r["evaluation_rows"],
                          "financing_improvement": r["financing_comparison"]["relative_error_improvement"]}
                      for k, r in artifacts["results.json"]["blocks"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
