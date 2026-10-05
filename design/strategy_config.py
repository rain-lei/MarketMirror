"""Editable platform strategy assumptions; frozen research config is read-only."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'research/configs/agent_decision_probes_2026_v1.json'
ROLE_NAMES = {'aggressive': '激进型', 'conservative': '保守型', 'institutional': '机构型'}
FIELDS = ('text_sensitivity', 'base_weight', 'risk_budget')


def load_model() -> tuple[dict, str]:
    raw = CONFIG.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def defaults(model: dict | None = None) -> dict:
    model = model if model is not None else load_model()[0]
    return {p['role']: {key: float(p[key]) for key in FIELDS} for p in model['agent_parameters']}


def limits(model: dict | None = None) -> dict:
    model = model if model is not None else load_model()[0]
    return {p['role']: {'text_sensitivity': [0., 2.], 'base_weight': [0., p['max_weight']],
                        'risk_budget': [.001, .08]} for p in model['agent_parameters']}


def validate_parameters(value: object, model: dict | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != set(ROLE_NAMES):
        raise ValueError('策略配置须完整包含激进型、保守型、机构型')
    bounds = limits(model)
    result = {}
    for role, label in ROLE_NAMES.items():
        row = value[role]
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError(f'{label}须包含文本敏感度、基础股票权重与风险预算')
        result[role] = {}
        for key in FIELDS:
            number = row[key]
            low, high = bounds[role][key]
            if (isinstance(number, bool) or not isinstance(number, (int, float))
                    or not math.isfinite(number) or not low <= number <= high):
                raise ValueError(f'{label} {key} 须为 {low}–{high} 的有限数值')
            result[role][key] = float(number)
    return result


def parameters_digest(parameters: dict) -> str:
    raw = json.dumps(parameters, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def profile_view(parameters: dict, updated_at: str | None = None) -> dict:
    model, model_hash = load_model()
    return {'parameters': parameters, 'defaults': defaults(model), 'limits': limits(model),
            'strategy_parameters_sha256': parameters_digest(parameters), 'updated_at': updated_at,
            'mechanism_config_sha256': model_hash,
            'fixed_rules': {p['role']: {k: p[k] for k in ('max_weight', 'confirmation_steps', 'rebalance_interval')}
                            for p in model['agent_parameters']}}
