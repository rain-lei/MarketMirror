"""Archive local, previously retrieved source pages without replacing prior evidence."""

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .provenance import file_sha256


def archive_evidence(catalog_path: Path, output_dir: Path) -> dict:
    catalog_path, output_dir = catalog_path.resolve(), output_dir.resolve()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    records = catalog.get("sources")
    if not isinstance(records, list) or not records:
        raise ValueError("evidence catalog requires a nonempty sources list")
    prepared = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"url", "path", "filename"}:
            raise ValueError("source requires url, path and filename")
        if not all(isinstance(v, str) and v for v in record.values()):
            raise ValueError("evidence catalog values must be nonempty strings")
        name = record["filename"]
        if Path(name).name != name or name in {".", "..", "evidence_manifest.json"}:
            raise ValueError("evidence filename must be a plain filename")
        source = (catalog_path.parent / record["path"]).resolve()
        if source == output_dir / name:
            raise ValueError("source must be separate from archive target")
        prepared.append({"url": record["url"], "path": name, "sha256": file_sha256(source), "source": source})
    if len({v["path"] for v in prepared}) != len(prepared) or len({v["url"] for v in prepared}) != len(prepared):
        raise ValueError("evidence filenames and URLs must be unique")
    public = [{k: v[k] for k in ("url", "path", "sha256")} for v in prepared]
    target_manifest = output_dir / "evidence_manifest.json"
    if output_dir.exists() and any(output_dir.iterdir()):
        if target_manifest.is_file():
            existing = json.loads(target_manifest.read_text(encoding="utf-8"))
            if existing.get("sources") == public and all(file_sha256(output_dir / v["path"]) == v["sha256"] for v in public):
                return existing
        raise ValueError("existing evidence differs; choose a fresh archive directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"pipeline_version": "evidence-archive-v1", "archived_at": datetime.now(timezone.utc).isoformat(),
                "catalog_sha256": file_sha256(catalog_path), "code_sha256": file_sha256(Path(__file__)), "sources": public}
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        staging = Path(tmp)
        for record in prepared:
            target = staging / record["path"]
            shutil.copyfile(record["source"], target)
            if file_sha256(target) != record["sha256"] or file_sha256(record["source"]) != record["sha256"]:
                raise RuntimeError("evidence source changed during archive")
        (staging / "evidence_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in staging.iterdir():
            path.replace(output_dir / path.name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Archive fetched evidence pages with source and content hashes")
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = archive_evidence(args.catalog, args.output_dir)
    print(f"Archived or verified {len(result['sources'])} evidence sources")


if __name__ == "__main__":
    main()
