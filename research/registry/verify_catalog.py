"""Audit declared hashes across local research runs without rerunning experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data_pipeline.provenance import file_sha256

VERSION = "research-integrity-catalog-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
INPUT_SECTIONS = ("inputs", "input_sha256", "market_source_inputs", "evidence_inputs", "sources", "financial_inputs")


def load_catalog(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"runs"} or not isinstance(data["runs"], list) or not data["runs"]:
        raise ValueError("catalog requires a nonempty runs list")
    runs = data["runs"]
    for run in runs:
        if (not isinstance(run, dict) or set(run) != {"run_id", "manifest", "manifest_sha256", "extra_hashes"}
                or not isinstance(run["run_id"], str) or not run["run_id"].strip()
                or not isinstance(run["manifest"], str) or not run["manifest"].strip()
                or not isinstance(run["manifest_sha256"], str) or not SHA256.fullmatch(run["manifest_sha256"])
                or not isinstance(run["extra_hashes"], dict)):
            raise ValueError("catalog run must pin its manifest hash and extra hash paths")
        if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in run["extra_hashes"].items()):
            raise ValueError("extra hash fields and paths must be strings")
    if len({run["run_id"] for run in runs}) != len(runs):
        raise ValueError("duplicate catalog run identifiers")
    return runs


def _hash_references(node: Any) -> list[tuple[str, str]]:
    """Find explicit path/hash pairs and path-keyed hash maps, never infer an unnamed hash."""
    if isinstance(node, list):
        return [item for child in node for item in _hash_references(child)]
    if not isinstance(node, dict):
        return []
    if isinstance(node.get("path"), str) and isinstance(node.get("sha256"), str):
        return [(node["path"], node["sha256"])]
    found = []
    for key, value in node.items():
        if isinstance(value, str) and SHA256.fullmatch(value) and ("/" in key or "\\" in key or "." in key):
            found.append((key, value))
        else:
            found.extend(_hash_references(value))
    return found


def _resolve_reference(raw: str, manifest_dir: Path, output_root: Path, expected_sha256: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    local = (manifest_dir / path).resolve()
    if local.is_file():
        return local
    if len(path.parts) != 1:
        raise ValueError(f"relative input reference is missing: {raw}")
    matches = [p.resolve() for p in output_root.rglob(path.name) if p.is_file() and p.name == path.name]
    if len(matches) == 1:
        return matches[0]
    matching_hash = [candidate for candidate in matches if file_sha256(candidate) == expected_sha256]
    if len(matching_hash) == 1:
        return matching_hash[0]
    raise ValueError(f"relative input reference is missing or ambiguous: {raw}")


def _resolve_code(raw: str, research_root: Path) -> Path:
    suffix = raw.replace("\\", "/")
    direct = (research_root / suffix).resolve()
    if direct.is_file() and research_root in direct.parents:
        return direct
    matches = [p.resolve() for p in research_root.rglob(Path(suffix).name)
               if p.is_file() and p.relative_to(research_root).as_posix().endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"code reference is missing or ambiguous: {raw}")
    return matches[0]


def audit_run(run: dict[str, Any], catalog_dir: Path, research_root: Path, output_root: Path,
              hash_cache: dict[Path, str]) -> dict[str, Any]:
    manifest_path = (catalog_dir / run["manifest"]).resolve()
    failures = []
    counts = {"artifacts": 0, "inputs": 0, "code": 0, "extra": 0}

    def check(path: Path, expected: str, category: str) -> None:
        counts[category] += 1
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            failures.append(f"{category}: malformed SHA-256 declaration: {path.name}")
            return
        if not path.is_file():
            failures.append(f"{category}: missing file: {path}")
            return
        if path not in hash_cache:
            hash_cache[path] = file_sha256(path)
        actual = hash_cache[path]
        if actual != expected:
            failures.append(f"{category}: hash mismatch: {path}")

    if not manifest_path.is_file():
        return {"run_id": run["run_id"], "manifest": str(manifest_path), "status": "failed",
                "checks": counts, "failures": ["manifest file is missing"]}
    manifest_digest = file_sha256(manifest_path)
    hash_cache[manifest_path] = manifest_digest
    if manifest_digest != run["manifest_sha256"]:
        failures.append("manifest hash differs from the version-controlled catalog pin")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts")
    code_hashes = manifest.get("code_sha256")
    if not isinstance(artifacts, dict) or not artifacts:
        failures.append("manifest has no artifact hash map")
        artifacts = {}
    if not isinstance(code_hashes, dict) or not code_hashes:
        failures.append("manifest has no resolvable code hash map")
        code_hashes = {}
    for name, info in artifacts.items():
        destination = (manifest_path.parent / name).resolve()
        if destination.parent != manifest_path.parent:
            failures.append(f"artifact path escapes run directory: {name}")
            continue
        check(destination, info.get("sha256") if isinstance(info, dict) else None, "artifacts")
    input_refs = []
    for key in INPUT_SECTIONS:
        if key in manifest:
            input_refs.extend(_hash_references(manifest[key]))
    if isinstance(manifest.get("config_path"), str) and isinstance(manifest.get("config_sha256"), str):
        input_refs.append((manifest["config_path"], manifest["config_sha256"]))
    seen_inputs = {}
    for raw, digest in input_refs:
        if raw in seen_inputs:
            if seen_inputs[raw] != digest:
                failures.append(f"inputs: conflicting declared hashes for {raw}")
            continue
        seen_inputs[raw] = digest
        try:
            check(_resolve_reference(raw, manifest_path.parent, output_root, digest), digest, "inputs")
        except ValueError as exc:
            counts["inputs"] += 1
            failures.append(f"inputs: {exc}")
    for raw, digest in code_hashes.items():
        try:
            check(_resolve_code(raw, research_root), digest, "code")
        except ValueError as exc:
            counts["code"] += 1
            failures.append(f"code: {exc}")
    for field, raw in run["extra_hashes"].items():
        if field not in manifest:
            failures.append(f"extra: manifest lacks declared field {field}")
            continue
        try:
            check(_resolve_reference(raw, catalog_dir, output_root, manifest[field]), manifest[field], "extra")
        except ValueError as exc:
            counts["extra"] += 1
            failures.append(f"extra: {exc}")
    if sum(counts.values()) == 0:
        failures.append("manifest did not yield any verifiable files")
    return {"run_id": run["run_id"], "manifest": str(manifest_path),
            "manifest_sha256": manifest_digest, "status": "passed" if not failures else "failed",
            "checks": counts, "failures": failures}


def render_report(results: dict[str, Any]) -> str:
    lines = ["# 本地研究产物完整性核验", "", "先将每份运行清单与版本控制目录中固定的清单哈希比对，再核验其声明的输入、代码和输出文件 SHA-256。通过只表示这些本地字节与固定清单一致；没有重新执行研究、复核原始数据口径或证明统计结论。", "",
             "| 运行 | 状态 | 输出文件 | 输入文件 | 代码文件 | 额外字段 |", "|---|---|---:|---:|---:|---:|"]
    for run in results["runs"]:
        counts = run["checks"]
        lines.append(f"| {run['run_id']} | {run['status']} | {counts['artifacts']} | {counts['inputs']} | {counts['code']} | {counts['extra']} |")
    for run in results["runs"]:
        if run["failures"]:
            lines.extend(["", f"## {run['run_id']} 的未通过项", ""])
            lines.extend(f"- {issue}" for issue in run["failures"])
    lines += ["", f"通过 {results['passed_runs']}/{len(results['runs'])} 项运行。输入路径指向当前机器上的文件；迁移环境时须重新取得来源并验证同一哈希。", ""]
    return "\n".join(lines)


def verify_catalog(catalog_path: Path, output_dir: Path) -> dict[str, Any]:
    catalog_path, output_dir = catalog_path.resolve(), output_dir.resolve()
    repo_root = Path(__file__).resolve().parents[2]
    research_root, output_root = repo_root / "research", repo_root / "research_outputs"
    runs = load_catalog(catalog_path)
    catalog_digest = file_sha256(catalog_path)
    cache: dict[Path, str] = {}
    audited = [audit_run(run, catalog_path.parent, research_root, output_root, cache) for run in runs]
    unstable = [path for path, digest in cache.items() if not path.is_file() or file_sha256(path) != digest]
    if file_sha256(catalog_path) != catalog_digest:
        unstable.append(catalog_path)
    if unstable:
        for run in audited:
            run["status"] = "failed"
            run["failures"].append("one or more audited files changed during verification")
    result = {"pipeline_version": VERSION, "catalog_sha256": catalog_digest,
              "passed_runs": sum(run["status"] == "passed" for run in audited), "runs": audited,
              "unstable_files": [str(path) for path in unstable],
              "scope": "File integrity only. Re-execution, data meaning, temporal validity and statistical claims are outside this audit."}
    if output_dir == catalog_path or output_dir in catalog_path.parents:
        raise ValueError("audit output cannot contain its catalog input")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("use a new empty audit output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        (staging / "integrity_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / "integrity_report.md").write_text(render_report(result), encoding="utf-8")
        manifest = {"pipeline_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "catalog_sha256": result["catalog_sha256"], "auditor_code_sha256": file_sha256(Path(__file__)),
                    "artifacts": {p.name: {"sha256": file_sha256(p)} for p in staging.iterdir()}}
        (staging / "integrity_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = verify_catalog(args.catalog, args.output_dir)
    print(json.dumps({"passed_runs": result["passed_runs"], "total_runs": len(result["runs"])}, ensure_ascii=False))
    if result["passed_runs"] != len(result["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
