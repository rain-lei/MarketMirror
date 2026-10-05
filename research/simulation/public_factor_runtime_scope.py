"""One declared, non-executed native renderer difference for numeric experiments."""
from __future__ import annotations

import json
from pathlib import Path
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / "research/configs/pre_wuhan_public_factor_runtime_scope_2019_v2.json"
RENDERER = Path("C:/Users/rain_/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe")
HISTORICAL = "3f2440140fef98ddd1ef0772a10d8e58dc3564a9b15a14b4611b5bbd5bdcd37f"
OBSERVED = "aac096ff054753776e9efe804a3480e3fadeaff1e08c7c4d22e4b87dbbce025e"


def allowed_nonexecuted_renderer(path, expected, actual):
    return Path(path) == RENDERER and expected == HISTORICAL and actual == OBSERVED


def validate_declaration(cfg):
    if (cfg["version"] != "public-factor-nonexecuted-renderer-scope-v2"
            or cfg["scope"] != "NUMERIC_SIMULATION_FROM_FROZEN_CSV_JSON_GZIP_ONLY"
            or cfg["renderer_path"] != str(RENDERER) or cfg["historical_sha256"] != HISTORICAL
            or cfg["observed_sha256"] != OBSERVED or cfg["allow_new_pdf_parsing_or_rendering"] is not False
            or cfg["allow_numeric_simulation"] is not True or cfg["alter_numerical_protocol"] is not False):
        raise ValueError("public factor numeric-only runtime scope differs")


def runtime_scope():
    cfg = json.loads(SCOPE.read_text(encoding="utf-8"))
    validate_declaration(cfg)
    for name, expected in cfg["inputs"].items():
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        if file_sha256(path) != expected:
            raise ValueError("runtime scope code/evidence differs: " + name)
    if file_sha256(RENDERER) != OBSERVED:
        raise ValueError("declared nonexecuted renderer changed again")
    return cfg


def verify_bindings(bindings):
    runtime_scope()
    differences = []
    for name, expected in bindings.items():
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        actual = file_sha256(path)
        if actual != expected:
            if not allowed_nonexecuted_renderer(path, expected, actual):
                raise ValueError("public numeric source/code binding changed: " + str(path))
            differences.append({"path": str(path), "historical_sha256": expected, "observed_sha256": actual,
                                "executed_in_this_experiment": False})
    return differences
