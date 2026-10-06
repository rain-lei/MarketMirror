"""Persisted, asynchronous scenario batches in the platform workspace."""
from copy import deepcopy
import platform
import threading
from uuid import uuid4

from .batch_metrics import digest, summarize_scenarios, write_report
from .server import atomic_json, load_json, utc_now
from .strategy_config import load_model, parameters_digest
from .text_analysis import validate_analysis_id


SCENARIOS = (('neutral', 0, 0), ('positive', .6, .2),
             ('negative', -.6, .2), ('uncertain', 0, .8))
SOURCE = '合成对照实验：方向与不确定性均为预设变量，不来自真实公告或模型推断。'


class BatchInProgress(Exception):
    pass


class BatchCheckError(ValueError):
    """A known validation failure whose message is safe to show in the UI."""


def validate_batch(payload):
    if not isinstance(payload, dict) or set(payload) - {'title', 'seeds', 'sessions', 'duration', 'cash'}:
        raise ValueError('批次配置包含不支持的字段')
    title = payload.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError('批次名称须为 1–80 个字符')
    seeds = payload.get('seeds')
    if (not isinstance(seeds, list) or not 1 <= len(seeds) <= 10
            or any(type(s) is not int or not 0 <= s <= 999999 for s in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError('请提供 1–10 个不重复的种子，须为 0–999999 的整数')
    sessions, duration, cash = payload.get('sessions', 18), payload.get('duration', 6), payload.get('cash', 1000000)
    if type(sessions) is not int or not 1 <= sessions <= 60:
        raise ValueError('实验总步数须为 1–60 的整数')
    if type(duration) is not int or duration not in (3, 6, 9):
        raise ValueError('消息持续步数须为 3、6 或 9')
    if type(cash) not in (int, float) or not 10000 <= cash <= 100000000:
        raise ValueError('每类初始资金须在 1万–1亿 模型元之间')
    return {'title': title.strip(), 'seeds': seeds[:], 'sessions': sessions, 'duration': duration, 'cash': cash}


def progress(record):
    counts = {key: 0 for key in ('completed', 'failed', 'pending', 'running', 'interrupted')}
    for row in record['records']:
        counts[row['status']] += 1
    return {'total': len(record['records']), **counts}


class BatchManager:
    def __init__(self, store):
        self.store = store
        self.directory = store.data_dir / 'batches'
        self.lock = threading.RLock()
        self.workers = {}
        self.stopping = threading.Event()

    def path(self, batch_id):
        validate_analysis_id(batch_id)
        return self.directory / batch_id

    def get(self, batch_id):
        path = self.path(batch_id) / 'batch.json'
        with self.lock:
            record = load_json(path) if path.is_file() else None
        if record is None:
            return None
        return {**record, 'progress': progress(record), 'summary': summarize_scenarios(record)}

    def list(self):
        rows = []
        with self.lock:
            for path in self.directory.glob('*/batch.json'):
                try:
                    record = self.get(path.parent.name)
                    rows.append({k: record[k] for k in ('id', 'title', 'created_at', 'status', 'progress', 'sessions')})
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return sorted(rows, key=lambda row: row['created_at'], reverse=True)

    def _save(self, record):
        with self.lock:
            atomic_json(self.path(record['id']) / 'batch.json', record)

    def create(self, payload):
        config = validate_batch(payload)
        _, model_hash = load_model()
        parameters = deepcopy(self.store.strategies()['parameters'])
        record = {'schema': 'platform-scenario-batch-v2', 'id': uuid4().hex, **config,
                  'created_at': utc_now(), 'status': 'ready', 'mode': 'synthetic_market',
                  'llm_called': False, 'python_version': platform.python_version(),
                  'mechanism_config_sha256': model_hash, 'strategy_parameters': parameters,
                  'strategy_parameters_sha256': parameters_digest(parameters),
                  'scenarios': SCENARIOS, 'records': [
                      {'scenario': name, 'seed': seed, 'status': 'pending',
                       'experiment_id': None, 'attempts': 0}
                      for seed in config['seeds'] for name, _, _ in SCENARIOS]}
        self._save(record)
        return self.get(record['id'])

    def start(self, batch_id):
        with self.lock:
            record = self.get(batch_id)
            if record is None:
                raise FileNotFoundError(batch_id)
            if self.stopping.is_set():
                raise BatchInProgress('服务正在关闭，请稍后重新运行')
            if batch_id in self.workers:
                raise BatchInProgress('该批次已在运行')
            if record['status'] == 'completed':
                raise BatchInProgress('该批次已完成，请创建新批次进行比较')
            record.pop('progress', None)
            record.pop('summary', None)
            record.update(status='running', started_at=utc_now(), finished_at=None, error=None)
            self._save(record)
            worker = threading.Thread(target=self._execute, args=(record,), daemon=True,
                                      name='marketmirror-batch-' + batch_id[:8])
            self.workers[batch_id] = worker
            try:
                worker.start()
            except Exception:
                self.workers.pop(batch_id)
                record.update(status='interrupted', finished_at=utc_now())
                self._save(record)
                raise
            return self.get(batch_id)

    def _execute(self, record):
        try:
            expected_baselines = {row['seed']: row['baseline_sha256'] for row in record['records']
                                  if row['status'] == 'completed'}
            for row in record['records']:
                if row['status'] == 'completed':
                    continue
                if self.stopping.is_set():
                    break
                row.update(status='running', attempts=row['attempts'] + 1, error=None)
                self._save(record)
                try:
                    name, signal, uncertainty = next(s for s in SCENARIOS if s[0] == row['scenario'])
                    if row['experiment_id'] is None:
                        experiment = self.store.create({
                            'title': f"{record['title']} · {name} · seed {row['seed']}",
                            'source': SOURCE, 'type': '其他消息', 'published_at': '2000-01-01T00:00',
                            'signal': signal, 'uncertainty': uncertainty, 'duration': record['duration'],
                            'sessions': record['sessions'], 'seed': row['seed'], 'cash': record['cash'],
                            'strategy_parameters': record['strategy_parameters']})
                        row['experiment_id'] = experiment['id']
                        self._save(record)
                    else:
                        experiment = self.store.get(row['experiment_id'])
                    if (not experiment or experiment['mechanism_config_sha256'] != record['mechanism_config_sha256']
                            or experiment['strategy_parameters_sha256'] != record['strategy_parameters_sha256']
                            or any(experiment[k] != v for k, v in (
                                ('signal', signal), ('uncertainty', uncertainty), ('seed', row['seed']),
                                ('sessions', record['sessions']), ('duration', record['duration']), ('cash', record['cash'])))):
                        raise BatchCheckError('实验参数与批次冻结快照不一致，需恢复原配置后重试。')
                    result = self.store.run(row['experiment_id'])
                    if not result['audit']['passed']:
                        raise BatchCheckError('账本审计未通过，此项结果未计入汇总。')
                    baseline, message = result['paths']['baseline'], result['paths']['with_message']
                    baseline_hash = digest(baseline)
                    if row['seed'] in expected_baselines and expected_baselines[row['seed']] != baseline_hash:
                        raise BatchCheckError('同种子的无消息基线发生变化，此项结果未计入汇总。')
                    expected_baselines[row['seed']] = baseline_hash
                    row.update(status='completed', baseline_sha256=baseline_hash,
                               paths_sha256=digest(result['paths']), audit=result['audit'],
                               result_id=result['provenance']['result_id'],
                               role_return_difference_pp={role: 100 * (value - baseline['summary']['role_wealth_multiple'][role])
                                   for role, value in message['summary']['role_wealth_multiple'].items()},
                               requested=message['summary']['strategy_requested'],
                               accepted=message['summary']['strategy_accepted'],
                               filled=message['summary']['strategy_filled'])
                except Exception as exc:
                    row.update(status='failed', error={'type': type(exc).__name__,
                        'message': str(exc) if isinstance(exc, BatchCheckError) else
                        '此项运行失败，未计入汇总。可重试；已创建的实验仍保留在实验空间。'})
                self._save(record)
            counts = progress(record)
            status = ('completed' if counts['completed'] == counts['total'] else 'interrupted' if self.stopping.is_set()
                      else 'partial' if counts['completed'] else 'failed')
            record.update(status=status, finished_at=utc_now())
            self._save(record)
        except Exception as exc:
            record.update(status='interrupted', finished_at=utc_now(),
                          error={'type': type(exc).__name__, 'message': '批次执行中断，已完成结果保留。'})
            for row in record['records']:
                if row['status'] == 'running':
                    row['status'] = 'interrupted'
            self._save(record)
        finally:
            with self.lock:
                self.workers.pop(record['id'], None)

    def recover_interrupted(self):
        for path in self.directory.glob('*/batch.json'):
            try:
                record = load_json(path)
                if record['status'] == 'running':
                    record.update(status='interrupted', finished_at=utc_now())
                    for row in record['records']:
                        if row['status'] == 'running':
                            row['status'] = 'interrupted'
                    self._save(record)
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def report(self, batch_id):
        with self.lock:
            record = self.get(batch_id)
            if record is None:
                raise FileNotFoundError(batch_id)
            write_report(self.path(batch_id), record)
            return (self.path(batch_id) / 'report.md').read_text(encoding='utf-8')

    def result(self, batch_id, experiment_id):
        validate_analysis_id(experiment_id)
        record = self.get(batch_id)
        if record is None:
            raise FileNotFoundError(batch_id)
        row = next((r for r in record['records'] if r['experiment_id'] == experiment_id
                    and r['status'] == 'completed'), None)
        if row is None:
            raise FileNotFoundError(experiment_id)
        validate_analysis_id(row['result_id'])
        result = load_json(self.store.data_dir / experiment_id / 'runs' / (row['result_id'] + '.json'))
        if digest(result['paths']) != row['paths_sha256']:
            raise ValueError('批次归档结果的路径校验失败')
        return result

    def close(self):
        self.stopping.set()
        with self.lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.join()
