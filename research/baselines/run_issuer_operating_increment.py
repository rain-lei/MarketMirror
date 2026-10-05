"""Run source-frozen operating increment blocks without changing the old study."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import issuer_operating_increment as diagnostic

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "research/configs/issuer_operating_increment_2019.json"
OUTPUT = ROOT / "research_outputs/issuer_operating_increment_2019_v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def compute():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["blocks"] != diagnostic.BLOCKS or config["agent_signal_enabled"] is not False:
        raise ValueError("operating increment block definitions or disabled signal gate changed")
    sources, bindings = {}, {str(CONFIG): digest(CONFIG)}

    def bind(path, expected):
        key = str(Path(path).resolve())
        if key in bindings and bindings[key] != expected:
            raise ValueError("conflicting operating-study source bindings")
        bindings[key] = expected

    for name, record in config["inputs"].items():
        path = (CONFIG.parent / record["path"]).resolve()
        bind(path, record["sha256"])
        sources[name] = json.loads(path.read_text(encoding="utf-8"))
    for source in (sources["base_manifest"], sources["operating_states"]):
        for field in ("inputs", "code_sha256"):
            for path, sha in source[field].items():
                bind(path, sha)
    if sources["base_independent_audit"]["status"] != "PASS" or sources["operating_states"]["agent_signal_enabled"] is not False:
        raise ValueError("operating sources or base independent audit do not pass")
    for name, artifact in sources["base_manifest"]["artifacts"].items():
        expected = "base_panel" if name == "panel.json" else "base_results"
        if config["inputs"][expected]["sha256"] != artifact["sha256"]:
            raise ValueError("base panel/result identity differs from original manifest")
    code_paths = [Path(__file__).resolve(), Path(diagnostic.__file__).resolve(), ROOT / "research/baselines/issuer_financial_increment.py"]
    code = {str(p): digest(p) for p in code_paths}

    def verify():
        for path, expected in {**bindings, **code}.items():
            if digest(path) != expected:
                raise ValueError("operating experiment input or producer changed: " + path)

    verify()
    base = sources["base_panel"]
    if base["split"] != sources["base_results"]["split"] or len(base["parent_stock_codes"]) != config["expected_parent_companies"]:
        raise ValueError("frozen baseline cohort/date split differs")
    operating = {c["stock_code"]: c for c in sources["operating_states"]["companies"]}
    balances = {c["stock_code"]: c for c in sources["balance_states"]["companies"]}
    if len(operating) != len(sources["operating_states"]["companies"]) or len(balances) != len(sources["balance_states"]["companies"]):
        raise ValueError("operating states duplicate company identities")
    panels, results = {}, {}
    for block in config["blocks"]:
        panel = diagnostic.extend_panel(base, operating, balances, block)
        panels[block] = panel
        results[block] = diagnostic.fit_block(panel, base["split"], block)
    artifacts = {
        "panel.json": {"pipeline_version": config["version"], "parent_stock_codes": base["parent_stock_codes"], "split": base["split"],
                       "blocks": panels, "agent_signal_enabled": False},
        "results.json": {"pipeline_version": config["version"], "split": base["split"], "blocks": results,
                         "limitations": config["limitations"], "agent_signal_enabled": False,
                         "interpretation": "Three prespecified blocks, paired within each source-visible development sample. All results retained; no best-block selection, new blind-validation, causal effect or market calibration claim. No Agent response is enabled."},
    }
    artifacts["manifest.json"] = {"pipeline_version": config["version"], "inputs": dict(sorted(bindings.items())), "code_sha256": code,
                                  "artifacts": {n: {"sha256": hashlib.sha256(serialize(v)).hexdigest()} for n, v in artifacts.items()}}
    verify()
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--audit-existing", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.parent != (ROOT / "research_outputs").resolve():
        raise ValueError("operating increment requires a direct research_outputs directory")
    artifacts = compute()
    if args.audit_existing:
        if any((output / name).read_bytes() != serialize(value) for name, value in artifacts.items()):
            raise ValueError("operating increment artifacts differ from frozen reconstruction")
        print("Operating increment artifacts rebuilt byte-identically.")
    else:
        output.mkdir(exist_ok=False)
        for name, value in artifacts.items():
            with (output / name).open("xb") as handle:
                handle.write(serialize(value))
    print(json.dumps({k: {"training_rows": r["training_rows"], "evaluation_rows": r["evaluation_rows"],
                          "operating_improvement": r["operating_comparison"]["relative_error_improvement"]} for k, r in artifacts["results.json"]["blocks"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
