"""Local experiment storage, source-bound LLM extraction and synthetic market runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
from .engine import run_market
from .workspace_lease import WorkspaceLease
from .text_analysis import analyze, source_digest, validate_analysis_id, validate_saved_analysis, validate_source
from .strategy_config import defaults, load_model, parameters_digest, profile_view, validate_parameters

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "research_outputs/platform_workspace"
SITE = Path(__file__).resolve().parent
MAX_BODY = 128 * 1024
ROLES = (
    {"key": "aggressive", "name": "激进型", "sensitivity": 0.90, "risk_budget": 0.040, "base_weight": 0.30},
    {"key": "conservative", "name": "保守型", "sensitivity": 0.25, "risk_budget": 0.008, "base_weight": 0.20},
    {"key": "institutional", "name": "机构型", "sensitivity": 0.50, "risk_budget": 0.016, "base_weight": 0.35},
)
TYPES = {"政策消息", "宏观消息", "公司问答", "其他消息"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def analysis_digest(record: dict) -> str:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def validate_experiment(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("请求必须是 JSON 对象")
    allowed = {"title", "source", "type", "published_at", "signal", "uncertainty", "duration", "sessions", "seed", "cash", "analysis_id", "strategy_parameters"}
    if set(value) - allowed:
        raise ValueError("请求包含不支持的字段")
    title, source, kind = value.get("title"), value.get("source"), value.get("type")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError("实验名称须为 1–120 个字符")
    validate_source(source)
    analysis_id = value.get('analysis_id')
    if analysis_id is not None:
        validate_analysis_id(analysis_id)
    if not isinstance(kind, str) or kind not in TYPES:
        raise ValueError("消息类型不受支持")
    if not isinstance(value.get("published_at"), str) or "T" not in value["published_at"]:
        raise ValueError("发布时间必须为 ISO 日期时间")
    try:
        datetime.fromisoformat(value["published_at"])
    except ValueError:
        raise ValueError("发布时间不是有效的日期时间") from None
    def number(name: str, default: float, integer: bool = False):
        raw = value.get(name, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{name} 必须是数值")
        if not math.isfinite(raw) or (integer and raw != int(raw)):
            raise ValueError(f"{name} 必须是有限{'整数' if integer else '数值'}")
        return int(raw) if integer else float(raw)
    signal, uncertainty = number("signal", 0.0), number("uncertainty", 0.0)
    duration, sessions = number("duration", 6, True), number("sessions", 18, True)
    seed, cash = number("seed", 7, True), number("cash", 1_000_000)
    if not -1 <= signal <= 1 or not 0 <= uncertainty <= 1:
        raise ValueError("信号须在 -1 到 1，不确定性须在 0 到 1")
    if duration not in (3, 6, 9) or not 1 <= sessions <= 60:
        raise ValueError("持续步数或模拟步数不在允许范围")
    if not 0 <= seed <= 999999 or seed != int(seed) or not 10_000 <= cash <= 100_000_000:
        raise ValueError("随机种子或初始资金不在允许范围")
    return {"title": title.strip(), "source": source, "type": kind, "published_at": value["published_at"],
            "signal": signal, "uncertainty": uncertainty, "duration": duration, "sessions": sessions,
            "seed": seed, "cash": cash, "analysis_id": analysis_id,
            'strategy_parameters': validate_parameters(value['strategy_parameters']) if 'strategy_parameters' in value else None}


def experiment_path(data_dir: Path, experiment_id: str) -> Path:
    if len(experiment_id) != 32 or any(c not in "0123456789abcdef" for c in experiment_id):
        raise ValueError("实验 ID 无效")
    return data_dir / experiment_id


def preview_run(experiment: dict) -> dict:
    """Deterministic, inspectable platform preview; not a research protocol run."""
    signal = experiment["signal"] * (1 - experiment["uncertainty"] * 0.35)
    negative = signal < 0
    sessions = experiment["sessions"]
    rows, roles = [], []
    for step in range(1, sessions + 1):
        visible = step > 4
        active = visible and step <= 4 + experiment["duration"]
        phase = (step - 4) / max(1, experiment["duration"]) if active else 0
        price = 100 + (signal * 4.5 * min(1, phase) if active else (signal * 4.5 if step > 4 + experiment["duration"] else 0))
        rows.append({"step": step, "price": round(price, 6), "message_visible": visible, "message_active": active})
        if step == sessions:
            for role in ROLES:
                change = role["sensitivity"] * signal if active or step > 4 + experiment["duration"] else 0
                weight = max(0, min(1, role["base_weight"] + change * 0.2))
                requested = int(abs(change) * experiment["cash"] / max(price, 1) / 100) * 100
                filled = int(requested * (0.66 if role["key"] == "aggressive" else 0.82))
                roles.append({"key": role["key"], "name": role["name"], "action": ("卖出" if negative and requested else "买入" if requested else "等待"),
                              "target_weight": round(weight * 100, 4), "requested_shares": requested,
                              "filled_shares": filled, "reason": "信息信号触发目标仓位调整。" if requested else "当前信息未触发新增交易。"})
    return {"mode": "platform_preview", "status": "completed", "generated_at": utc_now(), "sessions": rows,
            "roles": roles, "limitations": ["平台预览使用显式情景变量，不调用 LLM。", "不是冻结研究协议的统计结果。", "成交量为界面演示值。"]}


class RunInProgress(Exception):
    pass


class PlatformStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.active_runs = set()

    def recover_interrupted(self):
        """Call only after obtaining the exclusive server workspace lease."""
        for path in self.data_dir.glob('*/experiment.json'):
            try:
                record = load_json(path)
                if not isinstance(record, dict):
                    raise ValueError('实验记录必须为对象')
            except (OSError, ValueError, TypeError):
                # A damaged record is surfaced by list(); it must not prevent
                # the rest of the workspace from recovering on startup.
                continue
            if record.get('run_status') == 'running':
                atomic_json(path, {**record, 'run_status': 'interrupted',
                                  'run_finished_at': utc_now(),
                                  'run_error': {'type': 'ServiceInterrupted',
                                                'message': '上次运行因服务中断未确认完成，可以重新运行。'}})

    def list(self) -> list[dict]:
        rows = []
        for path in self.data_dir.glob("*/experiment.json"):
            try:
                row = load_json(path)
                if (not isinstance(row, dict) or row.get('id') != path.parent.name
                        or any(not isinstance(row.get(k), str) for k in ('title', 'type', 'created_at'))):
                    raise ValueError('实验记录摘要字段无效')
                rows.append({k: row[k] for k in ("id", "title", "type", "created_at", "status", "run_status") if k in row})
            except (OSError, ValueError, KeyError, TypeError):
                # Keep a visible tombstone so the UI can identify a damaged
                # record instead of silently shrinking the experiment list.
                rows.append({"id": path.parent.name, "title": "记录损坏 · 需要恢复",
                             "type": "未知", "created_at": "", "status": "corrupt",
                             "run_status": "corrupt"})
        return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)

    def get(self, experiment_id: str) -> dict | None:
        path = experiment_path(self.data_dir, experiment_id) / "experiment.json"
        return load_json(path) if path.is_file() else None

    def strategies(self) -> dict:
        path = self.data_dir / 'settings' / 'strategies.json'
        with self.lock:
            saved = load_json(path) if path.is_file() else None
        if saved is not None and (not isinstance(saved, dict) or 'parameters' not in saved):
            raise ValueError('工作区策略配置格式无效')
        parameters = validate_parameters(saved['parameters']) if saved is not None else defaults()
        return profile_view(parameters, saved.get('updated_at') if saved is not None else None)

    def save_strategies(self, parameters: object) -> dict:
        parameters = validate_parameters(parameters)
        record = {'parameters': parameters, 'updated_at': utc_now(), 'schema_version': 'platform-strategies-v1'}
        with self.lock:
            atomic_json(self.data_dir / 'settings' / 'strategies.json', record)
        return profile_view(parameters, record['updated_at'])

    def save_analysis(self, source: str, analysis: dict) -> dict:
        validated = validate_saved_analysis(source, {**analysis, 'source': source})
        analysis_id = uuid4().hex
        record = {k: validated[k] for k in ('model', 'source_sha256', 'facts', 'raw_response', 'prompt_sha256')}
        if isinstance(validated.get('prompt_version'), str):
            record['prompt_version'] = validated['prompt_version']
        record.update(schema_version='platform-text-analysis-v1', analysis_id=analysis_id,
                      source=source, created_at=utc_now(), evidence_verified=True, semantic_truth_verified=False)
        if isinstance(validated.get('provider_base_url'), str):
            record['provider_base_url'] = validated['provider_base_url']
        with self.lock:
            atomic_json(self.data_dir / 'analyses' / (analysis_id + '.json'), record)
        return record

    def bound_analysis(self, analysis_id: str, source: str) -> dict:
        validate_analysis_id(analysis_id)
        path = self.data_dir / 'analyses' / (analysis_id + '.json')
        if not path.is_file():
            raise ValueError('关联的文本分析不存在，请重新分析或取消关联')
        record = validate_saved_analysis(source, load_json(path))
        if record.get('analysis_id', analysis_id) != analysis_id:
            raise ValueError('文本分析 ID 与存档不一致')
        return {**record, 'analysis_id': analysis_id}

    def create(self, payload: dict) -> dict:
        config = validate_experiment(payload)
        experiment_id = uuid4().hex
        analysis = self.bound_analysis(config['analysis_id'], config['source']) if config['analysis_id'] else None
        parameter_origin = 'explicit' if config['strategy_parameters'] is not None else 'workspace_profile'
        config['strategy_parameters'] = config['strategy_parameters'] or self.strategies()['parameters']
        record = {"id": experiment_id, **config, "created_at": utc_now(), "status": "ready", "run_status": "not_started", "platform_version": "v3",
                  'source_sha256': source_digest(config['source']), 'text_analysis': analysis,
                  'analysis_sha256': analysis_digest(analysis) if analysis else None,
                  'scenario_variables_origin': 'manual', 'strategy_parameters_origin': parameter_origin,
                  'strategy_parameters_sha256': parameters_digest(config['strategy_parameters']),
                  'mechanism_config_sha256': load_model()[1]}
        with self.lock:
            atomic_json(self.data_dir / experiment_id / "experiment.json", record)
        return record

    def run(self, experiment_id: str) -> dict:
        with self.lock:
            record = self.get(experiment_id)
            if record is None:
                raise FileNotFoundError(experiment_id)
            if experiment_id in self.active_runs:
                raise RunInProgress('该实验正在运行，请刷新查看状态')
            record = {**record, 'run_status': 'running', 'run_started_at': utc_now(),
                      'run_attempts': record.get('run_attempts', 0) + 1, 'run_error': None}
            atomic_json(self.data_dir / experiment_id / 'experiment.json', record)
            self.active_runs.add(experiment_id)
        try:
            return self._execute_run(experiment_id, record)
        except Exception as exc:
            failed = {**record, 'run_status': 'failed', 'run_finished_at': utc_now(),
                      'run_error': {'type': type(exc).__name__,
                                    'message': '实验运行失败，可检查配置后重试；已有成功结果仍保留。'}}
            atomic_json(self.data_dir / experiment_id / 'experiment.json', failed)
            raise
        finally:
            with self.lock:
                self.active_runs.discard(experiment_id)

    def _execute_run(self, experiment_id: str, record: dict) -> dict:
        if record.get('text_analysis') is not None:
            validate_saved_analysis(record['source'], record['text_analysis'])
            if record['analysis_sha256'] != analysis_digest(record['text_analysis']):
                raise ValueError('实验的文本分析快照已改变')
        provenance = {'experiment_id': experiment_id, 'source_sha256': source_digest(record['source']),
                      'run_attempts': record.get('run_attempts'),
                      'analysis_id': record.get('analysis_id'), 'analysis_sha256': record.get('analysis_sha256'),
                      'text_analysis_role': 'reference_only' if record.get('text_analysis') else 'none',
                      'scenario_variables_origin': 'manual',
                      'strategy_parameters_sha256': record.get('strategy_parameters_sha256')}
        result_id = uuid4().hex
        provenance['result_id'] = result_id
        result = {**run_market(record), 'generated_at': utc_now(), 'provenance': provenance}
        record = {**record, "run_status": "completed", "last_run_at": result["generated_at"],
                  'run_finished_at': utc_now(), 'run_error': None}
        # Publish only after the immutable result is safely stored. A failed
        # record commit leaves the previous successful result selected.
        atomic_json(self.data_dir / experiment_id / 'runs' / (result_id + '.json'), result)
        record['result_id'] = result_id
        with self.lock:
            atomic_json(self.data_dir / experiment_id / "experiment.json", record)
        return result

    def result(self, experiment_id: str) -> dict | None:
        directory = experiment_path(self.data_dir, experiment_id)
        record = self.get(experiment_id)
        result_id = record.get('result_id') if record else None
        if result_id is not None:
            validate_analysis_id(result_id)
            return load_json(directory / 'runs' / (result_id + '.json'))
        # Existing workspaces used a single result.json before versioned runs.
        path = directory / "result.json"
        return load_json(path) if path.is_file() else None

    def export(self, experiment_id: str) -> dict | None:
        with self.lock:
            record = self.get(experiment_id)
            if record is None:
                return None
            result = self.result(experiment_id)
            return {'artifact': 'MarketMirror experiment', 'schema_version': 'platform-export-v2',
                    'mode': result['mode'] if result else 'not_run',
                    'experiment': {**record, 'backendResult': result}, 'fixture': None}


def create_server(data_dir: Path = DEFAULT_DATA, port: int = 8770) -> ThreadingHTTPServer:
    from .batches import BatchManager, BatchInProgress
    store = PlatformStore(data_dir)
    batches = BatchManager(store)
    class Handler(BaseHTTPRequestHandler):
        def json(self, code: int, payload: object, download: str | None = None) -> None:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(raw)
        def local(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
        def report(self, batch_id: str) -> None:
            raw = batches.report(batch_id).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/markdown; charset=utf-8')
            self.send_header('Content-Disposition', f'attachment; filename="marketmirror-batch-{batch_id}.md"')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self) -> None:
            if not self.local(): self.json(403, {"error": "仅允许本机访问"}); return
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/platform/health": self.json(200, {"status": "ok", "platform_version": "v3", "mode": "local"}); return
                if path == '/api/platform/strategies': self.json(200, store.strategies()); return
                if path == '/api/platform/batches': self.json(200, batches.list()); return
                if path.startswith('/api/platform/batches/'):
                    tail = path.removeprefix('/api/platform/batches/')
                    if tail.endswith('/report'):
                        self.report(tail[:-7]); return
                    if '/results/' in tail:
                        batch_id, experiment_id = tail.split('/results/', 1)
                        self.json(200, batches.result(batch_id, experiment_id)); return
                    payload = batches.get(tail)
                    self.json(200 if payload is not None else 404, payload or {'error': '批次不存在'}); return
                if path == "/api/platform/experiments": self.json(200, store.list()); return
                if path.startswith("/api/platform/experiments/"):
                    tail = path.removeprefix("/api/platform/experiments/")
                    if tail.endswith('/export'):
                        experiment_id = tail[:-7]
                        payload = store.export(experiment_id)
                        self.json(200 if payload is not None else 404, payload or {'error': '实验不存在'},
                                  f'marketmirror-{experiment_id}.json' if payload is not None else None)
                        return
                    if tail.endswith("/result"): payload = store.result(tail[:-7])
                    else: payload = store.get(tail)
                    self.json(200 if payload is not None else 404, payload or {"error": "实验不存在"}); return
                name = "index.html" if path == "/" else path.removeprefix("/")
                types = {"index.html":"text/html","app.js":"text/javascript","batch-view.js":"text/javascript","analysis-state.js":"text/javascript","strategy-state.js":"text/javascript","decision-view.js":"text/javascript","styles.css":"text/css","README.md":"text/markdown"}
                if name not in types: self.json(404, {"error": "资源不存在"}); return
                raw = (SITE / name).read_bytes(); self.send_response(200); self.send_header("Content-Type", types[name] + '; charset=utf-8'); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            except FileNotFoundError: self.json(404, {'error': '记录或归档结果不存在'})
            except (ValueError, OSError, json.JSONDecodeError): self.json(400, {"error": "请求无法处理"})
        def do_POST(self) -> None:
            if not self.local() or self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}"):
                self.json(403, {"error": "仅允许本机来源"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_BODY: raise ValueError("请求过大")
                payload = json.loads(self.rfile.read(length)); path = self.path.split("?", 1)[0]
                if path == '/api/platform/batches': self.json(201, batches.create(payload)); return
                if path.startswith('/api/platform/batches/') and path.endswith('/run'):
                    if payload != {}: raise ValueError('运行批次只接受空对象')
                    self.json(202, batches.start(path[len('/api/platform/batches/'):-4])); return
                if path == '/api/platform/strategies':
                    if not isinstance(payload, dict) or set(payload) != {'parameters'}:
                        raise ValueError('仅接受完整策略参数')
                    self.json(200, store.save_strategies(payload['parameters'])); return
                if path == '/api/platform/analyze':
                    if not isinstance(payload, dict) or set(payload) != {'source'}:
                        raise ValueError('仅接受消息原文')
                    record = store.save_analysis(payload['source'], analyze(payload['source']))
                    self.json(200, record); return
                if path == "/api/platform/experiments": self.json(201, store.create(payload)); return
                prefix = "/api/platform/experiments/"
                if path.startswith(prefix) and path.endswith("/run"):
                    result = store.run(path[len(prefix):-4]); self.json(202, result); return
                self.json(404, {"error": "接口不存在"})
            except FileNotFoundError: self.json(404, {"error": "记录不存在"})
            except RunInProgress as exc: self.json(409, {'error': str(exc)})
            except BatchInProgress as exc: self.json(409, {'error': str(exc)})
            except RuntimeError: self.json(502, {'error': '运行服务暂不可用，请刷新状态后重试'})
            except OSError: self.json(500, {'error': '本机记录读写失败，请检查存储目录'})
            except (ValueError, TypeError, json.JSONDecodeError) as exc: self.json(400, {"error": str(exc)})
        def log_message(self, *_args: object) -> None: return
    class PlatformHTTPServer(ThreadingHTTPServer):
        lease = None

        def server_close(self):
            try:
                super().server_close()
            finally:
                batches.close()
                if self.lease is not None:
                    self.lease.close()

    server = PlatformHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.lease = WorkspaceLease(store.data_dir)
        store.recover_interrupted()
        batches.recover_interrupted()
    except Exception:
        server.server_close()
        raise
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()
    server = create_server(args.data_dir, args.port)
    print(f"MarketMirror platform: http://127.0.0.1:{server.server_port}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
