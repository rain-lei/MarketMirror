"""Run and score the frozen v3 prompt on its previously reviewed development set.

Development results can guide prompt repair but never make Agent signals eligible.
The API key is loaded from the git-ignored .env.local file.
"""

from __future__ import annotations

import argparse
import getpass
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from .local_credential import DEFAULT_CREDENTIAL_PATH, load_api_key, save_api_key
from .score_wuhan_v3_development import score
from .wuhan_model_v3 import run as run_model
from .wuhan_protocol_v3 import audit_v3, normalize_v3, verify_pack


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_ROOT = REPO_ROOT / "research_outputs"
PACK_DIR = OUTPUTS_ROOT / "wuhan_pre_event_dev_protocol_v3_v2"
DEVELOPMENT_SOURCE_DIR = OUTPUTS_ROOT / "wuhan_pre_event_pit_snapshot_2020_v3"
STAGE_NAMES = ("raw", "normalized", "score")


def default_output_root(now: datetime | None = None) -> Path:
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S_%f")
    return OUTPUTS_ROOT / f"wuhan_v3_development_{stamp}"


def _stage_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in STAGE_NAMES}


def _check_layout(root: Path, pack_dir: Path, *, resume: bool) -> dict[str, Path]:
    root, pack_dir = root.resolve(), pack_dir.resolve()
    outputs_root = OUTPUTS_ROOT.resolve()
    if root == outputs_root or outputs_root not in root.parents:
        raise ValueError("output root must be a child of the repository research_outputs directory")
    if (root == pack_dir or root in pack_dir.parents or pack_dir in root.parents):
        raise ValueError("output root must be separate from the frozen development pack")

    stages = _stage_paths(root)
    if not resume:
        if root.exists() and any(root.iterdir()):
            raise ValueError("output root already contains data; choose a new root")
        return stages

    if not root.is_dir() or not stages["raw"].is_dir():
        raise ValueError("resume requires an existing output root and raw checkpoint directory")
    if {entry.name for entry in root.iterdir()} - set(STAGE_NAMES):
        raise ValueError("resume output root contains unexpected files")
    if any(path.exists() and (not path.is_dir() or any(path.iterdir()))
           for name, path in stages.items() if name != "raw"):
        raise ValueError("resume only supports a raw model checkpoint before normalization")
    return stages


def run_pipeline(pack_dir: Path = PACK_DIR, output_root: Path | None = None, *,
                 resume: bool = False, replace_api_key: bool = False,
                 credential_path: Path = DEFAULT_CREDENTIAL_PATH,
                 api_key_provider: Callable[[str], str] = getpass.getpass) -> dict:
    """Run model, normalization, audit, and development-only scoring."""
    pack_dir = pack_dir.resolve()
    output_root = (output_root or default_output_root()).resolve()
    stages = _check_layout(output_root, pack_dir, resume=resume)
    items, pack = verify_pack(pack_dir)
    source_pack = Path(pack.get("derived_protocol", {}).get("source_pack_directory", "")).resolve()
    if source_pack != DEVELOPMENT_SOURCE_DIR.resolve():
        raise ValueError("development runner only accepts the previously reviewed v3 development cohort")
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
        api_key = None  # type: ignore[assignment]
    print(json.dumps({"stage": "model", "model_rows": raw_manifest["model_rows"],
                      "request_failures": raw_manifest["request_failures"]}, ensure_ascii=False),
          flush=True)

    normalized = normalize_v3(pack_dir, stages["raw"], stages["normalized"])
    audited = audit_v3(pack_dir, stages["raw"], stages["normalized"])
    comparison = score(pack_dir, stages["raw"], stages["normalized"], stages["score"])
    result = {"output_root": str(output_root), "pack_experiment_id": pack["experiment_id"],
              "model_rows": raw_manifest["model_rows"],
              "request_failures": raw_manifest["request_failures"],
              "parse_errors": normalized["parse_errors"],
              "reply_only_evidence": audited["reply_only_evidence"],
              "model_f1": comparison["model"]["event_detection"]["f1"],
              "keyword_f1": comparison["keyword"]["event_detection"]["f1"],
              "model_type_macro_f1": comparison["model"]["event_type_macro_f1_supported"],
              "development_targets_met": comparison["development_targets"]["met"],
              "agent_signal_eligible": False}
    print(json.dumps({"stage": "score", **result}, ensure_ascii=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-dir", type=Path, default=PACK_DIR)
    parser.add_argument("--output-root", type=Path,
                        help="new directory under research_outputs; defaults to a timestamped path")
    parser.add_argument("--resume", action="store_true",
                        help="resume an incomplete raw checkpoint before normalization")
    parser.add_argument("--replace-api-key", action="store_true",
                        help="replace the local API key using one masked prompt")
    args = parser.parse_args()
    try:
        result = run_pipeline(args.pack_dir, args.output_root, resume=args.resume,
                              replace_api_key=args.replace_api_key)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps({"stage": "complete", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
