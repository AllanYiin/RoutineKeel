"""Local runtime implementations of reusable Blueprint graph steps."""

from decimal import Decimal, InvalidOperation, localcontext

from routinekeel_contracts.base_steps import (
    DOCUMENT_SEARCH,
    RECONCILE_PLAN,
    RECONCILE_RUN,
    SKILL_LOAD,
    VERSION,
    ReconciliationPlan,
)

from ..runtime import atomic_json, canonical, digest, read_json

LOCAL_STEPS = frozenset({RECONCILE_PLAN, RECONCILE_RUN, DOCUMENT_SEARCH, SKILL_LOAD})


def cached_step(runtime, step, inputs, compute, *, valid=lambda value: True):
    from ..outputs import reject_links

    identity = digest([step, VERSION, inputs])
    path = runtime.home / "base_step_cache" / (identity + ".json")
    with runtime.lock("base-step:" + identity, timeout=300):
        reject_links(path)
        if path.is_file():
            try:
                receipt = read_json(path)
                value = receipt["value"]
                if receipt["sha256"] == digest([identity, value]) and valid(value):
                    return value, True
            except (OSError, ValueError, KeyError, TypeError):
                pass
        value = compute()
        atomic_json(path, {"value": value, "sha256": digest([identity, value])})
        return value, False


def _select(value, pointer):
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if (
                not key.isascii()
                or not key.isdecimal()
                or (key.startswith("0") and key != "0")
            ):
                raise ValueError("invalid list index")
            value = value[int(key)]
        else:
            value = value[key]
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("not a number")
    result = Decimal(str(value))
    if (
        not result.is_finite()
        or result.adjusted() > 60
        or result.as_tuple().exponent < -60
    ):
        raise ValueError("numeric value exceeds supported range")
    return result


def _measure(value, pointer, field, operation):
    selected = _select(value, pointer)
    if operation == "equals":
        return selected
    if operation == "count" and isinstance(selected, (list, dict)):
        return Decimal(len(selected))
    if operation == "sum" and isinstance(selected, list):
        return sum((_number(_select(row, field)) for row in selected), Decimal(0))
    raise ValueError("unsupported measurement input")


def _rows_by_key(rows, key, field):
    if not isinstance(rows, list):
        raise TypeError("expected rows")
    records = {}
    for row in rows:
        identity = _select(row, key)
        if type(identity) not in {str, int} or identity == "" or identity in records:
            raise ValueError("missing or duplicate row key")
        records[identity] = _select(row, field)
    return records


def _compare_keys(rule, left, right):
    actual = _rows_by_key(_select(left, rule.left), rule.left_key, rule.left_field)
    expected = _rows_by_key(
        _select(right, rule.right), rule.right_key, rule.right_field
    )
    tolerance = Decimal(rule.tolerance)
    different = []
    for key in actual.keys() & expected.keys():
        compared = (
            abs(_number(actual[key]) - _number(expected[key])) <= tolerance
            if tolerance
            else canonical(actual[key]) == canonical(expected[key])
        )
        if not compared:
            different.append(key)
    groups = {
        "left_only": actual.keys() - expected.keys(),
        "right_only": expected.keys() - actual.keys(),
        "different": different,
    }
    return not any(groups.values()), {
        name: {"count": len(keys), "examples": sorted(keys, key=canonical)[:100]}
        for name, keys in groups.items()
    }


def execute_reconciliation(plan, left, right):
    spec = ReconciliationPlan.model_validate(plan["spec"])
    fingerprint = digest([VERSION, spec.model_dump(mode="json")])
    if plan.get("version") != VERSION or plan.get("sha256") != fingerprint:
        raise ValueError("勾稽計畫版本或指紋不符")
    checks = []
    with localcontext() as context:
        context.prec = 140
        for rule in spec.rules:
            row = {"id": rule.id, "operation": rule.operation}
            try:
                if rule.operation == "keyed":
                    matched, differences = _compare_keys(rule, left, right)
                    checks.append(
                        {
                            **row,
                            **differences,
                            "status": "matched" if matched else "mismatched",
                        }
                    )
                    continue
                actual = _measure(left, rule.left, rule.left_field, rule.operation)
                expected = _measure(right, rule.right, rule.right_field, rule.operation)
                if rule.operation == "equals":
                    matched = canonical(actual) == canonical(expected)
                    row.update(
                        left_sha256=digest(actual), right_sha256=digest(expected)
                    )
                else:
                    delta = actual - expected
                    matched = abs(delta) <= Decimal(rule.tolerance)
                    row.update(
                        left=str(actual),
                        right=str(expected),
                        delta=str(delta),
                        tolerance=rule.tolerance,
                    )
                row["status"] = "matched" if matched else "mismatched"
            except (ValueError, KeyError, TypeError, IndexError, InvalidOperation):
                row["status"] = "invalid_input"
            checks.append(row)
    return {
        "matched": all(row["status"] == "matched" for row in checks),
        "report": {"version": VERSION, "plan_sha256": fingerprint, "checks": checks},
    }


def execute_local_step(session, capability, values, parameters):
    if capability == RECONCILE_PLAN:
        spec = ReconciliationPlan.model_validate(parameters).model_dump(mode="json")
        fingerprint = digest([VERSION, spec])
        plan, hit = cached_step(
            session.runtime,
            capability,
            spec,
            lambda: {"version": VERSION, "sha256": fingerprint, "spec": spec},
        )
        return {"plan": plan}, {
            "kind": "local_cache" if hit else "local_execution",
            "plan_sha256": fingerprint,
        }
    if capability == RECONCILE_RUN:
        return execute_reconciliation(**values), {"kind": "local_execution"}
    if capability == SKILL_LOAD:
        from routinekeel_contracts.documa import load_skills

        bundle = load_skills(session.runtime.workspace_root, **values, **parameters)
        return {"bundle": bundle}, {
            "kind": "documa_skill_graph",
            "status": bundle["status"],
        }
    from .documents import search_indexes

    return {
        "matches": search_indexes(
            session.runtime, session.assets, **values, **parameters
        )
    }, {"kind": "documa_search"}
