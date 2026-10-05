"""Isolated whole-cohort initial-allocation study with the original Q1 inputs."""
from .temporal_information_study import (
    ROOT, CONFIG as BASE_CONFIG, OUTPUT as BASE_OUTPUT, CELLS, encoded, read, require,
    read_gzip, write_gzip, seed_paths, path_key, controls_for, shocks_for, summarize,
    load_study as load_base,
)
from .initial_resource_allocation import VERSION
from ..data_pipeline.provenance import file_sha256

CONFIG = ROOT / "research/configs/temporal_initial_allocation_2021_v1.json"
OUTPUT = ROOT / "research_outputs/temporal_initial_allocation_2021_v1"


def bindings_ok(cfg):
    for name, digest in cfg["bindings"].items():
        require(file_sha256(ROOT / name) == digest, "initial allocation frozen binding changed: " + name)


def load_study():
    cfg = read(CONFIG)
    require(cfg["version"] == "temporal-initial-allocation-complete-study-2021-v1"
        and cfg["initial_allocation"] == VERSION and cfg["semantic_gate_enabled"] is False
        and cfg["no_new_parameter_default"] is True, "initial allocation protocol differs")
    bindings_ok(cfg)
    base, risk, features, joined, mask = load_base()
    for key in ("stocks", "dates", "seeds", "baskets", "variants", "model_design", "agents",
                "issuer_parameters", "background_response", "joint_limits", "dense_audit_sessions"):
        require(cfg[key] == base[key], "initial allocation changed original comparison scope: " + key)
    return cfg, risk, features, joined, mask


def seal(folder, names, identity):
    with (folder / "checkpoint.json").open("xb") as f:
        f.write(encoded({"identity": identity, "protocol_sha256": file_sha256(CONFIG),
            "artifacts": {n: file_sha256(folder / n) for n in names}}))


def checkpoint(folder, identity):
    record = read(folder / "checkpoint.json")
    require(record["identity"] == identity and record["protocol_sha256"] == file_sha256(CONFIG), "allocation checkpoint identity differs")
    require({p.name for p in folder.iterdir()} == set(record["artifacts"]) | {"checkpoint.json"}, "allocation checkpoint scope differs")
    for name, digest in record["artifacts"].items():
        require(file_sha256(folder / name) == digest, "allocation checkpoint artifact changed")
    return record
