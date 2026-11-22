"""Shared shape checks for the Graph v2 verifier's executable examples."""


def trial_case_gaps(cases, outputs):
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        return [{"code": "TRIAL_CASES_REQUIRED", "reason": "需要 1–20 組可執行的驗收案例。"}]
    return [
        {
            "code": "TRIAL_CASE_NOT_EXECUTABLE",
            "case": index,
            "reason": "案例須提供 inputs 物件及包含各具名輸出的 expected 物件。",
        }
        for index, case in enumerate(cases, 1)
        if not isinstance(case, dict)
        or not isinstance(case.get("inputs"), dict)
        or not isinstance(case.get("expected"), dict)
        or set(case["expected"]) != set(outputs)
    ]
