from dataclasses import dataclass
from uuid import uuid4

from ..contracts import StudioError
from ..project.service import now


@dataclass(frozen=True)
class Budget:
    max_calls: int
    budget_micro_usd: int
    max_output_tokens: int
    input_micro_usd_per_million: int
    output_micro_usd_per_million: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise StudioError("INPUT_INVALID", "預算、用量上限與已確認費率必須為正整數。")

    def cost(self, inputs, outputs):
        return (
            inputs * self.input_micro_usd_per_million
            + outputs * self.output_micro_usd_per_million
            + 999_999
        ) // 1_000_000


class UsageLedger:
    def __init__(self, repository):
        self.repository = repository

    def reserve(
        self,
        project_id,
        model,
        request_hash,
        budget: Budget,
        input_bound,
        *,
        phase="design",
        revision_id=None,
        run_id=None,
        step_id=None,
    ):
        if phase not in {"design", "runtime"}:
            raise StudioError("INPUT_INVALID", "用量階段無效。")
        reserved = budget.cost(input_bound, budget.max_output_tokens)
        identity = "call_" + uuid4().hex
        with self.repository.transaction() as db:
            calls, used = db.execute(
                "SELECT count(*),coalesce(sum(coalesce(charged,reserved)),0) FROM studio_llm_calls WHERE project_id=? AND phase=?",
                (project_id, phase),
            ).fetchone()
            if calls >= budget.max_calls or used + reserved > budget.budget_micro_usd:
                raise StudioError("BUDGET_EXCEEDED", "模型呼叫次數或保留預算已達上限。")
            db.execute(
                """INSERT INTO studio_llm_calls
                (call_id,project_id,revision_id,run_id,step_id,phase,model,request_hash,reserved,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'reserved',?)""",
                (
                    identity,
                    project_id,
                    revision_id,
                    run_id,
                    step_id,
                    phase,
                    model,
                    request_hash,
                    reserved,
                    now(),
                ),
            )
        return identity

    def settle(self, call_id, budget: Budget, usage, status):
        usage = usage if isinstance(usage, dict) else {}
        ins, outs = usage.get("prompt_tokens"), usage.get("completion_tokens")
        valid = all(type(v) is int and v >= 0 for v in (ins, outs))
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if type(cached) is not int or cached < 0 or not valid or cached > ins:
            cached = None
        with self.repository.transaction() as db:
            db.execute(
                """UPDATE studio_llm_calls SET status=?,input_tokens=?,output_tokens=?,cached_tokens=?,charged=? WHERE call_id=?""",
                (
                    status,
                    ins if valid else None,
                    outs if valid else None,
                    cached,
                    budget.cost(ins, outs) if valid else None,
                    call_id,
                ),
            )

    def summary(self, project_id, phase="design"):
        rows = [
            dict(row)
            for row in self.repository.connection.execute(
                "SELECT * FROM studio_llm_calls WHERE project_id=? AND phase=? ORDER BY created_at",
                (project_id, phase),
            )
        ]
        return {
            "phase": phase,
            "calls": len(rows),
            "committed_micro_usd": sum(
                row["charged"] if row["charged"] is not None else row["reserved"] for row in rows
            ),
            "usage_unavailable": sum(row["input_tokens"] is None for row in rows),
            "receipts": rows,
        }
