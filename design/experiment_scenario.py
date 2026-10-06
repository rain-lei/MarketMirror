"""Explicit platform assumptions and fixed-state preview references."""
from __future__ import annotations

import hashlib
import json
import math

from .strategy_config import load_model, validate_parameters
from .strategy_preview import validate_scenario

DEFAULT_ASSUMPTIONS = {"scope": "A", "market": 0., "volatility": .01}


def validate_assumptions(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_ASSUMPTIONS):
        raise ValueError("市场假设须包含消息范围、共同市场偏移和波动率下限")
    if not isinstance(value["scope"], str) or value["scope"] not in {"A", "public"}:
        raise ValueError("消息范围仅支持资产 A 或全部资产")
    result = {"scope": value["scope"]}
    for key, low, high in (("market", -1., 1.), ("volatility", .001, .5)):
        number = value[key]
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or not low <= number <= high):
            raise ValueError(f"{key} 须为 {low}–{high} 的有限数值")
        result[key] = float(number)
    return result


def assumptions_digest(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_preview_reference(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"parameters", "scenario", "mechanism_config_sha256"}:
        raise ValueError("预览参考须包含完整输入与基础配置标识")
    model, model_hash = load_model()
    if value["mechanism_config_sha256"] != model_hash:
        raise ValueError("预览基础配置已变化，请重新计算后创建实验")
    return {"parameters": validate_parameters(value["parameters"], model),
            "scenario": validate_scenario(value["scenario"]), "mechanism_config_sha256": model_hash}
