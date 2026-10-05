"""Execution revision 2: unchanged model contracts after a terminal import failure."""
from .temporal_resource_unit_study import *
from .temporal_resource_unit_study import load_study as load_revision_one

CONFIG = ROOT / "research/configs/temporal_resource_unit_study_2022_v2.json"
OUTPUT = ROOT / "research_outputs/temporal_transport_resource_units_2022_v2"


def load_study(protocol_path=CONFIG):
    cfg, *data = load_revision_one(protocol_path)
    require(cfg["execution_revision"] == 2 and cfg["startup_import_repair_only"] is True,
            "Execution revision 2 must retain the unchanged resource study contract")
    return (cfg, *data)
