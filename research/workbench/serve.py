"""Serve the local workbench and allow one catalog-pinned rerun at a time."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from ..data_pipeline.provenance import file_sha256
from .build import CONFIG as INTEGRITY_CONFIG
from .run import CONFIG, DEFAULT_OUTPUT_ROOT, ROOT, available_runs, read_public_job, run_selected, version_catalog

DEFAULT_SITE = ROOT / "research_outputs/workbench_2018_2020_feedback_v20"
ASSET_TYPES = {"index.html": "text/html", "app.js": "text/javascript",
               "data.js": "text/javascript", "style.css": "text/css", "report.md": "text/markdown"}


def validate_site(site_dir: Path) -> None:
    manifest = json.loads((site_dir / "workbench_manifest.json").read_text(encoding="utf-8"))
    if set(manifest["artifacts"]) != set(ASSET_TYPES):
        raise ValueError("workbench site has an unexpected artifact set")
    if (manifest.get("integrity_catalog_sha256") != file_sha256(INTEGRITY_CONFIG)
            or manifest.get("code_sha256") != file_sha256(Path(__file__).with_name("build.py"))):
        raise ValueError("workbench site was built from another catalog or builder version")
    for name, details in manifest["artifacts"].items():
        if file_sha256(site_dir / name) != details["sha256"]:
            raise ValueError(f"workbench site artifact changed: {name}")


class JobManager:
    def __init__(self, output_root: Path, config_path: Path = CONFIG):
        self.output_root = output_root.resolve()
        self.config_path = config_path.resolve()
        self.allowed = available_runs(self.config_path)
        self.versions = version_catalog(self.config_path)
        self.lock = threading.Lock()
        self.active: threading.Thread | None = None
        self.active_id: str | None = None
        self.active_run: str | None = None
        self.active_error: str | None = None

    def start(self, run_id: str, data_version: str, model_version: str) -> str | None:
        if run_id not in self.allowed:
            raise ValueError("run_id is absent from the pinned catalog")
        expected = self.versions[run_id]
        if data_version != expected["data_version"] or model_version != expected["model_version"]:
            raise ValueError("version selection is not pinned for this run")
        with self.lock:
            if self.active is not None and self.active.is_alive():
                return None
            job_id = uuid4().hex
            self.active_id, self.active_run, self.active_error = job_id, run_id, None
            self.active = threading.Thread(target=self._execute,
                                           args=(job_id, run_id, data_version, model_version), daemon=False)
            self.active.start()
            return job_id

    def _execute(self, job_id: str, run_id: str, data_version: str, model_version: str) -> None:
        try:
            run_selected(run_id, self.output_root, self.config_path, job_id,
                         data_version=data_version, model_version=model_version)
        except Exception as exc:
            with self.lock:
                self.active_error = type(exc).__name__

    def get(self, job_id: str) -> dict | None:
        stored = read_public_job(self.output_root, job_id)
        if stored is not None:
            return stored
        with self.lock:
            if job_id != self.active_id:
                return None
            state = "queued" if self.active is not None and self.active.is_alive() else "failed"
            return {"job_id": job_id, "run_id": self.active_run, "status": state,
                    **({"error_type": self.active_error} if self.active_error else {})}

    def recent(self) -> list[dict]:
        records = []
        if self.output_root.is_dir():
            for directory in self.output_root.iterdir():
                if directory.is_dir():
                    record = read_public_job(self.output_root, directory.name)
                    if record is not None:
                        records.append(record)
        with self.lock:
            active_id = self.active_id
        if active_id and not any(row["job_id"] == active_id for row in records):
            active = self.get(active_id)
            if active:
                records.append(active)
        return sorted(records, key=lambda row: row.get("started_at", ""), reverse=True)[:20]


def create_server(site_dir: Path = DEFAULT_SITE, output_root: Path = DEFAULT_OUTPUT_ROOT,
                  port: int = 8766, config_path: Path = CONFIG) -> ThreadingHTTPServer:
    site_dir = site_dir.resolve()
    validate_site(site_dir)
    manager = JobManager(output_root, config_path)

    class Handler(BaseHTTPRequestHandler):
        def _host_ok(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def _json(self, code: int, payload: dict | list) -> None:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if not self._host_ok():
                self._json(403, {"error": "invalid host"})
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/capabilities":
                self._json(200, {"runs": manager.allowed,
                                 "versions": manager.versions,
                                 "master_config_sha256": file_sha256(manager.config_path)})
                return
            if path == "/api/jobs":
                self._json(200, manager.recent())
                return
            if path.startswith("/api/jobs/"):
                record = manager.get(path.removeprefix("/api/jobs/"))
                self._json(200 if record else 404, record or {"error": "job not found"})
                return
            name = "index.html" if path == "/" else path.removeprefix("/")
            if name not in ASSET_TYPES:
                self._json(404, {"error": "not found"})
                return
            raw = (site_dir / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ASSET_TYPES[name] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self._host_ok() or self.headers.get("Origin") != origin:
                self._json(403, {"error": "local origin required"})
                return
            if self.path != "/api/jobs" or self.headers.get("Content-Type") != "application/json":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 512:
                    raise ValueError("invalid request length")
                request = json.loads(self.rfile.read(length))
                required = {"run_id", "data_version", "model_version"}
                if (not isinstance(request, dict) or set(request) != required
                        or any(not isinstance(request[key], str) for key in required)):
                    raise ValueError("request requires run_id, data_version and model_version")
                job_id = manager.start(request["run_id"], request["data_version"], request["model_version"])
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "invalid run request"})
                return
            self._json(202 if job_id else 409, {"job_id": job_id} if job_id else {"error": "a run is active"})

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = create_server(args.site_dir, port=args.port)
    print(f"MarketMirror local workbench: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
