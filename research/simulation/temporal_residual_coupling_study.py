"""Original full 2021 grid with only residual dependence changed."""
from .temporal_information_study import (ROOT, CONFIG as BASE_CONFIG, OUTPUT as BASE_OUTPUT,
    CELLS, encoded, read, require, read_gzip, write_gzip, path_key, controls_for, shocks_for,
    summarize, load_study as load_base, seed_paths as baseline_seed_paths)
from .temporal_residual_coupling import VERSION, build_path, source_windows
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_residual_coupling_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_residual_coupling_2021_v1"


def bindings_ok(cfg):
    for name, digest in cfg["bindings"].items():
        require(file_sha256(ROOT / name) == digest, "frozen residual coupling binding changed: " + name)


def load_study():
    cfg = read(CONFIG)
    require(cfg["version"] == "temporal-residual-coupling-complete-study-2021-v1"
        and cfg["residual_coupling_version"] == VERSION and cfg["semantic_gate_enabled"] is False
        and cfg["no_new_parameter_default"] is True, "residual coupling protocol differs")
    bindings_ok(cfg)
    base, risk, features, joined, mask = load_base()
    for key in ("stocks", "dates", "seeds", "baskets", "variants", "model_design", "agents",
                "issuer_parameters", "background_response", "joint_limits", "dense_audit_sessions"):
        require(cfg[key] == base[key], "residual coupling changed original comparison scope: " + key)
    require(source_windows(features) == read(ROOT / cfg["residual_rank_preparation_path"])["windows"],
        "complete residual source preparation differs")
    return cfg, risk, features, joined, mask


def seed_paths(cfg, risk, features, seed):
    core = {**cfg["model_design"]["core"], "seed": seed["arrival_seed"],
        "scenario_id": f"endogenous_cohort_seed{seed['arrival_seed']}"}
    background = {**cfg["model_design"]["background"], "seed": seed["background_seed"]}
    params = {**cfg["issuer_parameters"], "innovation_seed": seed["innovation_seed"],
        "information_seed": seed["information_seed"]}
    common = "marketmirror-public-market-factor-v1:" + seed["innovation_seed"]
    coupling_seed = cfg["residual_common_seed_namespace"] + seed["innovation_seed"]
    paths = {m + "_" + d: build_path(risk, features, "temporal_candidate_2021", params, m, common, d, coupling_seed)
        for m in ("market_independent", "market_shared") for d in ("masked", "public")}
    return core, background, paths


def seal(folder, names, identity, protocol_path=CONFIG):
    with (folder / "checkpoint.json").open("xb") as f:
        f.write(encoded({"identity": identity, "protocol_sha256": file_sha256(protocol_path),
            "artifacts": {n: file_sha256(folder / n) for n in names}}))


def checkpoint(folder, identity, protocol_path=CONFIG):
    record = read(folder / "checkpoint.json")
    require(record["identity"] == identity and record["protocol_sha256"] == file_sha256(protocol_path),
        "residual coupling checkpoint identity differs")
    require({p.name for p in folder.iterdir()} == set(record["artifacts"]) | {"checkpoint.json"},
        "residual coupling checkpoint scope differs")
    for name, digest in record["artifacts"].items():
        require(file_sha256(folder / name) == digest, "residual coupling checkpoint artifact changed")
    return record
