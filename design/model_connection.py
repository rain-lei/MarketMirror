"""Read local readiness and test the production extraction contract on synthetic text."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import threading
import time

from .text_analysis import analyze, ModelAnalysisError, DEFAULT_BASE_URL, DEFAULT_MODEL, PROMPT_VERSION
from research.semantic.local_credential import load_api_key

CHECK_SOURCE = '连接检测用合成文字：示例公司的新建生产线仍在办理审批，尚未取得正式批复，具体投产时间不确定。'


class ModelCheckInProgress(RuntimeError):
    pass


def read_configuration():
    try:
        key = load_api_key()
        credential_status = 'configured'
    except FileNotFoundError:
        key, credential_status = None, 'missing'
    except (OSError, ValueError):
        key, credential_status = None, 'unavailable'
    public = {'model': DEFAULT_MODEL, 'base_url': DEFAULT_BASE_URL,
              'prompt_version': PROMPT_VERSION, 'credential_status': credential_status}
    # This fingerprint stays inside the server, and binds the last check to
    # the exact credential used. It is never returned or persisted.
    fingerprint = hashlib.sha256(json.dumps({**public, 'credential': key}, sort_keys=True).encode()).hexdigest()
    return public, key, fingerprint


class ModelConnection:
    def __init__(self):
        self.lock = threading.Lock()
        self.checking = False
        self.fingerprint = None
        self.last_check = None

    def configuration(self):
        public, _, fingerprint = read_configuration()
        with self.lock:
            if fingerprint != self.fingerprint:
                self.last_check = None
                self.fingerprint = fingerprint
            return {**public, 'checking': self.checking, 'last_check': deepcopy(self.last_check)}

    def check(self):
        public, key, fingerprint = read_configuration()
        with self.lock:
            if self.checking:
                raise ModelCheckInProgress('连接检测正在进行，请等待完成后刷新状态。')
            if fingerprint != self.fingerprint:
                self.last_check = None
            self.fingerprint = fingerprint
            self.checking = True
        started = time.perf_counter()
        outcome = None
        try:
            if key is None:
                raise ModelAnalysisError('credential_unavailable', '本机模型凭据不可用，请检查项目的 .env.local 配置。')
            analysis = analyze(CHECK_SOURCE, api_key=key)
            outcome = {'status': 'passed', 'facts_count': len(analysis['facts']),
                       'error_code': None, 'message': '实际请求完成，事实结构和原文引文校验通过。'}
        except ModelAnalysisError as error:
            outcome = {'status': 'failed', 'error_code': error.code, 'message': str(error)}
        except Exception:
            outcome = {'status': 'failed', 'error_code': 'internal_error', 'message': '检测未完成，请重试或检查本机服务。'}
        finally:
            current, _, current_fingerprint = read_configuration()
            if current_fingerprint != fingerprint:
                outcome = {'status': 'configuration_changed', 'error_code': 'configuration_changed',
                           'message': '检测期间本机凭据或配置已改变，请重新检测。'}
            outcome.update(checked_at=datetime.now(timezone.utc).isoformat(),
                           elapsed_ms=round((time.perf_counter() - started) * 1000),
                           model=public['model'], prompt_version=public['prompt_version'],
                           synthetic_text=True, semantic_truth_verified=False)
            with self.lock:
                self.checking = False
                self.fingerprint = current_fingerprint
                self.last_check = deepcopy(outcome) if current_fingerprint == fingerprint else None
        return {**current, 'checking': False, 'last_check': deepcopy(self.last_check), 'check_result': outcome}
