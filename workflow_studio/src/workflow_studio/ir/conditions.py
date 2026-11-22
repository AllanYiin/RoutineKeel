from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .models import Contract


class BranchCondition(Contract):
    operator: Literal["file_size_gt", "file_size_gte", "text_contains", "text_equals"]
    value: int | str

    @model_validator(mode="after")
    def typed_operand(self):
        if self.operator.startswith("file_size"):
            if type(self.value) is not int or self.value < 0:
                raise ValueError("File size requires a nonnegative integer")
        elif not isinstance(self.value, str):
            raise ValueError("Text condition requires a string")
        return self


class BranchArm(Contract):
    tool: str
    version: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class BranchPlan(Contract):
    condition: BranchCondition
    when_true: BranchArm
    when_false: BranchArm
