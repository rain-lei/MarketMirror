"""Reopen every source ledger and independently reconstruct the saved stages."""
import argparse
from collections import Counter
import gzip
import json

from .temporal_transmission_study import CONFIG, OUTPUT, load, read, require, encoded, conditions, process_condition
from .audit_signal_transmission_diagnostics import independent_diagnose, independent_statistics
from .verify_pre_wuhan_order_price_diagnostics_2019 import compare
from ..data_pipeline.provenance import file_sha256


def audit_seed(si):
    protocol,cfg,mask=load()
    require(type(si) is int and 0<=si<5,"explicit independent seed required")
    folder=OUTPUT/f"seed_{si}"
    target=folder/"independent.json"
    require(not target.exists(),"preserve completed transmission audit")
    seal=read(folder/"checkpoint.json")
    require(seal["protocol_sha256"]==file_sha256(CONFIG),"transmission checkpoint differs")
    for name,digest in seal["artifacts"].items():
        require(file_sha256(folder/name)==digest,"transmission archive changed")
    saved=read(folder/"summary.json")
    counts=Counter()
    prepared=conditions(si,cfg)
    with gzip.open(folder/"positions.jsonl.gz","rt",encoding="utf-8") as archive:
        def verify_record(expected):
            line=next(archive,None)
            require(line is not None,"diagnostic archive ended early")
            compare(json.loads(line),expected)
        index=0
        for arm in ("baseline","candidate"):
            for ci in range(16):
                result=process_condition(si,arm,ci,cfg,mask,prepared,verify_record,
                    analyser=independent_diagnose,statistics_function=independent_statistics)
                compare(result,saved["conditions"][index])
                counts.update(result["checks"])
                index+=1
                print(f"Independent transmission seed {si+1}/5 {arm} condition {ci+1}/16 passed.",flush=True)
        require(next(archive,None) is None,"diagnostic archive contains extra records")
    require(dict(counts)==saved["checks"] and index==32,"whole independent transmission scope differs")
    load()
    target.write_bytes(encoded({"status":"PASS_FULL_SIGNAL_TRANSMISSION_SEED","protocol_sha256":file_sha256(CONFIG),
        "seed_id":cfg["seeds"][si]["seed_id"],"checks":dict(counts),"conditions_independently_reconstructed":32,
        "methods":"Independent rational tick arithmetic, direct price candidate enumeration, and separately centred panel statistics.",
        "artifacts":{name:file_sha256(folder/name) for name in ("checkpoint.json","positions.jsonl.gz","summary.json")}}))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed",type=int,required=True)
    audit_seed(parser.parse_args().seed)
