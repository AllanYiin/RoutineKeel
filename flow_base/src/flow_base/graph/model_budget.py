"""Input-dependent model budgets for independently metered host tools."""

from ..runtime import FlowError, digest


def batch_rule(resources, inputs):
    if "model_call_batch" not in resources:
        return None
    rule = resources["model_call_batch"]
    if (
        not isinstance(rule, dict)
        or rule.keys() != {"input", "size"}
        or not isinstance(rule["input"], str)
        or inputs.get(rule["input"], {}).get("type") != "array"
        or type(rule["size"]) is not int or rule["size"] < 1
        or resources.get("cost_micro_usd") != 0
        or ("model_calls" in resources and (
            type(resources["model_calls"]) is not int or resources["model_calls"] < 0
        ))
    ):
        raise FlowError("MODEL_BUDGET_CONTRACT", "分批模型工具須宣告陣列輸入、正整數批次大小與零成本。")
    return rule


def batch_count(resources, values):
    rule = resources["model_call_batch"]
    items = values.get(rule["input"])
    if not isinstance(items, list):
        raise FlowError("MODEL_BUDGET_CONTRACT", "分批模型輸入須為陣列。")
    return (len(items) + rule["size"] - 1) // rule["size"]


def scoped_resources(binding, values):
    resources = binding.get("resources")
    if not resources or "model_call_batch" not in resources:
        return resources
    required = batch_count(resources, values)
    declared = resources.get("model_calls")
    if declared is not None and required > declared:
        raise FlowError(
            "MODEL_BUDGET_CONFLICT", "實際輸入所需的模型呼叫數超過工具的固定上限。",
            tool=binding["tool"], version=binding["version"], input_hash=digest(values),
            declared_model_calls=declared, required_model_calls=required,
        )
    return {**resources, "model_calls": required}


def require_verified_budget(binding, values):
    resources = scoped_resources(binding, values) or {}
    if unverified_locked_model(binding):
        raise FlowError(
            "MODEL_BUDGET_UNVERIFIED", "這個已鎖定的模型工具缺少可核對的呼叫次數，須重新驗證。",
            tool=binding["tool"], version=binding["version"], input_hash=digest(values),
            declared_model_calls=resources.get("model_calls"),
        )


def unverified_locked_model(binding):
    resources = binding.get("resources") or {}
    return (
        binding.get("lock_hash") and not binding.get("model_budget_verified")
        and ("model_call_batch" in resources or resources.get("model_calls", 0) > 0)
    )


def metered_candidate(candidate):
    resources = candidate.get("model_budget") or {}
    return "model_call_batch" in resources or resources.get("model_calls", 0) > 0


def usage_evidence(resources, measured):
    calls = measured.get("model_calls")
    elapsed = measured.get("elapsed_ms")
    if type(calls) is not int or calls < 0 or type(elapsed) is not int or elapsed < 0:
        return False
    batches = measured.get("batches")
    if "model_call_batch" not in resources:
        return True
    return isinstance(batches, list) and len(batches) == calls and all(
        isinstance(row, dict) and type(row.get("index")) is int and row["index"] == index
        and type(row.get("elapsed_ms")) is int and 0 <= row["elapsed_ms"] <= elapsed
        and row.get("outcome") in {"succeeded", "failed", "unknown"}
        for index, row in enumerate(batches, 1)
    )
