"""Run the frozen Wuhan v3 validation and gated portfolio ablation locally.

The gateway credential is loaded from the git-ignored .env.local file, or
requested once through getpass on first setup. It is never printed or written
into experiment artifacts.
"""

from __future__ import annotations

import argparse
import getpass
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from .local_credential import (DEFAULT_CREDENTIAL_PATH, load_api_key,
                               save_api_key)
from .score_wuhan_v3_validation import score
from .wuhan_model_v3 import run as run_model
from .wuhan_protocol_v3 import audit_v3, normalize_v3, verify_pack
from .wuhan_v3_signal_adapter import run_adapter
from ..simulation.wuhan_v3_portfolio_ablation import run_ablation


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_ROOT = REPO_ROOT / "research_outputs"
PACK_DIR = OUTPUTS_ROOT / "wuhan_pre_event_independent_protocol_v3_v1"
BASELINE_DIR = OUTPUTS_ROOT / "wuhan_validation_pit_portfolio_no_text_2020_v1"
CONFIG_PATH = REPO_ROOT / "research/configs/wuhan_validation_pit_portfolio_no_text_2020.json"
STAGE_NAMES = ("raw", "normalized", "score", "signals", "paired_portfolio")


def default_output_root(now: datetime | None = None) -> Path:
    """Return a unique, timestamped run location under ignored research outputs."""
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S_%f")
    return OUTPUTS_ROOT / f"wuhan_v3_validation_{stamp}"


def _stage_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in STAGE_NAMES}


def _check_layout(root: Path, pack_dir: Path, *, resume: bool) -> dict[str, Path]:
    root, pack_dir = root.resolve(), pack_dir.resolve()
    outputs_root = OUTPUTS_ROOT.resolve()
    if root == outputs_root or outputs_root not in root.parents:
        raise ValueError("output root must be a new child of the repository research_outputs directory")
    for source in (pack_dir, BASELINE_DIR.resolve(), CONFIG_PATH.resolve()):
        if root == source or root in source.parents or source in root.parents:
            raise ValueError("output root must be separate from all frozen experiment inputs")

    stages = _stage_paths(root)
    if not resume:
        if root.exists() and any(root.iterdir()):
            raise ValueError("output root already contains data; choose a new root")
        return stages

    if not root.is_dir() or not stages["raw"].is_dir():
        raise ValueError("resume requires an existing output root and raw checkpoint directory")
    unexpected = {entry.name for entry in root.iterdir()} - set(STAGE_NAMES)
    if unexpected:
        raise ValueError("resume output root contains unexpected files")
    if any(path.exists() and (not path.is_dir() or any(path.iterdir()))
           for name, path in stages.items() if name != "raw"):
        raise ValueError("resume only supports a model checkpoint before normalization")
    return stages


def run_pipeline(pack_dir: Path = PACK_DIR, output_root: Path | None = None, *,
                 resume: bool = False,
                 replace_api_key: bool = False,
                 credential_path: Path = DEFAULT_CREDENTIAL_PATH,
                 api_key_provider: Callable[[str], str] = getpass.getpass) -> dict:
    """Run the full local validation, stopping before signals if the gate fails."""
    pack_dir = pack_dir.resolve()
    output_root = (output_root or default_output_root()).resolve()
    stages = _check_layout(output_root, pack_dir, resume=resume)

    items, pack = verify_pack(pack_dir)
    print(json.dumps({"stage": "preflight", "output_root": str(output_root),
                      "pack_experiment_id": pack["experiment_id"], "items": len(items)},
                     ensure_ascii=False), flush=True)

    credential_loaded = False
    if replace_api_key:
        api_key = None
    else:
        try:
            api_key = load_api_key(credential_path)
            credential_loaded = True
            print("Loaded the API key from the local environment file.", flush=True)
        except FileNotFoundError:
            api_key = None
    if api_key is None:
        print("No local API key is saved; the first-time key will be saved in the ignored .env.local file.",
              flush=True)
        api_key = api_key_provider("Gateway API key (masked; saved to .env.local): ")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("no API key entered; no model request was made")
    if not credential_loaded:
        save_api_key(api_key, credential_path)
        print("Saved the API key in the local environment file.", flush=True)
    try:
        raw_manifest = run_model(pack_dir, stages["raw"], api_key=api_key, resume=resume)
    finally:
        # Python strings cannot be reliably zeroed, but drop the reference as
        # soon as the request stage has returned. Nothing persists it to disk.
        api_key = None  # type: ignore[assignment]

    print(json.dumps({"stage": "model", "model_rows": raw_manifest["model_rows"],
                      "request_failures": raw_manifest["request_failures"]}, ensure_ascii=False),
          flush=True)
    normalized = normalize_v3(pack_dir, stages["raw"], stages["normalized"])
    audited = audit_v3(pack_dir, stages["raw"], stages["normalized"])
    comparison = score(pack_dir, stages["raw"], stages["normalized"], stages["score"])
    print(json.dumps({"stage": "score", "model_f1": comparison["model"]["event_detection"]["f1"],
                      "keyword_f1": comparison["keyword"]["event_detection"]["f1"],
                      "parse_errors": normalized["parse_errors"],
                      "research_signal_gate_passed": comparison["research_signal_gate"]["passed"],
                      "raw_audit_passed": True, "reply_only_evidence": audited["reply_only_evidence"]},
                     ensure_ascii=False), flush=True)

    if comparison["research_signal_gate"]["passed"] is not True:
        return {"output_root": str(output_root), "gate_passed": False,
                "model_rows": raw_manifest["model_rows"], "parse_errors": normalized["parse_errors"],
                "signals_created": False, "portfolio_created": False}

    signal_result = run_adapter(pack_dir, stages["raw"], stages["normalized"],
                                stages["score"], stages["signals"])
    portfolio = run_ablation(CONFIG_PATH, BASELINE_DIR, pack_dir, stages["raw"],
                             stages["normalized"], stages["score"], stages["signals"],
                             stages["paired_portfolio"])
    return {"output_root": str(output_root), "gate_passed": True,
            "model_rows": raw_manifest["model_rows"], "parse_errors": normalized["parse_errors"],
            "signals_created": signal_result["items"],
            "portfolio_created": True,
            "paired_paths": portfolio["paired_paths"],
            "audited_portfolio_days": portfolio["audited_portfolio_days"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-dir", type=Path, default=PACK_DIR)
    parser.add_argument("--output-root", type=Path,
                        help="new directory under research_outputs; defaults to a timestamped path")
    parser.add_argument("--resume", action="store_true",
                        help="resume a partial model request in this run's raw checkpoint directory")
    parser.add_argument("--replace-api-key", action="store_true",
                        help="replace the API key in .env.local using one masked prompt")
    args = parser.parse_args()
    try:
        result = run_pipeline(args.pack_dir, args.output_root, resume=args.resume,
                              replace_api_key=args.replace_api_key)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps({"stage": "complete", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
