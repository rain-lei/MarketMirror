"""Numeric execution scope: preserve historical renderer evidence outside input gate."""
from __future__ import annotations
import json
from pathlib import Path
from ..data_pipeline.provenance import file_sha256
from .run_pre_wuhan_own_price_feedback_2019_v2 import merge

ROOT = Path(__file__).resolve().parents[2]
CONFIG_V3 = ROOT / "research/configs/pre_wuhan_public_factor_checkpoint_execution_v3.json"
RENDERER = Path("C:/Users/rain_/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe")


def separate(bindings):
    numeric, historical = {}, {}
    for name, expected in bindings.items():
        path = (ROOT / name).resolve()
        target = historical if path == RENDERER else numeric
        target[str(path)] = expected
    return numeric, historical


def verify_numeric(bindings):
    numeric, unused = separate(bindings)
    if unused:
        raise ValueError("nonexecuted renderer must be explicitly separated before numeric verification")
    for name, expected in numeric.items():
        if file_sha256(Path(name)) != expected:
            raise ValueError("numeric experiment input/code changed: " + name)


def load_inputs():
    from .run_pre_wuhan_public_factor_channels_2019 import (CONFIG, CELLS, VERSION, SOURCE, PRIOR,
        feedback_inputs, DEFAULT_LIMITS, load_source_groups, MARKET)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    declaration = json.loads(CONFIG_V3.read_text(encoding="utf-8"))
    if (declaration["version"] != "public-factor-resumable-numeric-execution-v3"
            or declaration["numerical_protocol_sha256"] != file_sha256(CONFIG)
            or declaration["nonexecuted_dependency_path"] != str(RENDERER)
            or declaration["allow_pdf_processing"] is not False):
        raise ValueError("checkpoint execution scope differs")
    loaded = feedback_inputs()
    (_, base, replay, market, codes, dates, _, joined, inputs, code, _, _, _, shared, issuer,
     feature_checks, industry_config, _) = loaded
    source = json.loads((SOURCE / "results.json").read_text(encoding="utf-8"))["result"]
    if (cfg["version"] != VERSION or cfg["variants"] != CELLS or cfg["joint_limits"] != DEFAULT_LIMITS
            or cfg["seeds"] != source["seeds"] or cfg["agent_signal_enabled"] is not False
            or cfg["dense_audit_sessions"] != [0, 10] or source["sample"]["selected_stock_codes"] != codes
            or len(codes) != 123 or len(dates) != 43
            or cfg["public_parameters"] != {"history_window": 20, "benchmark_id": "sh.000300", "belief_scale_bps": 1000,
                                           "max_shift_bps": 500, "risk_multiplier": 1.0}):
        raise ValueError("original frozen public design differs")
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    if prior["status"] != "PASS_FIXED_TOTAL_CASH_RESOURCE_AUDIT":
        raise ValueError("prior verified source receipt differs")
    merge(inputs, prior["inputs"])
    merge(inputs, cfg["inputs"])
    merge(inputs, declaration["inputs"])
    merge(inputs, {str(CONFIG): file_sha256(CONFIG), str(PRIOR): file_sha256(PRIOR), str(CONFIG_V3): file_sha256(CONFIG_V3)})
    inputs, unused = separate(inputs)
    if unused != declaration["historical_nonexecuted_binding"]:
        raise ValueError("historical renderer evidence was not retained exactly")
    verify_numeric(inputs)
    verify_numeric(code)
    groups = load_source_groups(MARKET.parent / "market_daily.csv", market)
    return cfg, base, replay, market, codes, dates, groups, joined, inputs, code, shared, issuer, feature_checks, industry_config, source
