"""Admission and executable examples for model-generated data-transform handlers."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from ..contracts import StudioError
from ..ir.models import Contract
from ..project.models import canonical_json
from .runtime_program import (
    Limits,
    ProgramError,
    run_program,
    validate_arguments,
    validate_output,
    validate_program,
)

Scalar = str | int | float | bool | None


class HandlerExample(Contract):
    input: str = Field(max_length=65536)
    expected: str = Field(max_length=65536)
    arguments: dict[str, Scalar] = Field(default_factory=dict)


class GeneratedHandler(Contract):
    purpose: str = Field(min_length=1, max_length=200)
    program: str = Field(min_length=1, max_length=32768)
    arguments: dict[str, Scalar] = Field(default_factory=dict)
    argument_types: dict[str, Literal["string", "integer", "number", "boolean", "null"]] = Field(
        default_factory=dict
    )
    tests: Annotated[list[HandlerExample], Field(min_length=2, max_length=8)]

    @field_validator("program")
    @classmethod
    def safe_program(cls, value):
        validate_program(value)
        return value

    @model_validator(mode="after")
    def typed_arguments(self):
        validate_arguments(self.arguments, self.argument_types)
        for example in self.tests:
            validate_arguments(example.arguments, self.argument_types)
        return self


def validate_handler(parameters):
    try:
        contract = GeneratedHandler.model_validate(parameters)
    except (ValidationError, ProgramError) as exc:
        detail = (
            str(exc)
            if isinstance(exc, ProgramError)
            else "; ".join(
                ".".join(map(str, e["loc"])) + ": " + e["msg"]
                for e in exc.errors(include_input=False)
            )
        )
        raise StudioError("CONTRACT_MISMATCH", "新 Handler 契約不合法：" + detail[:1000]) from exc
    if (
        len(
            {
                canonical_json({"input": test.input, "arguments": test.arguments})
                for test in contract.tests
            }
        )
        < 2
    ):
        raise StudioError("CONTRACT_MISMATCH", "新 Handler 至少需要兩組不同的合成測試輸入。")
    return contract


def verify_examples(parameters, output_format):
    contract = validate_handler(parameters)
    results = []
    for index, example in enumerate(contract.tests, 1):
        try:
            actual = run_program(
                contract.program,
                example.input,
                example.arguments,
                limits=Limits(max_bytes=256 * 1024, max_items=2000, max_steps=100_000, timeout=2),
            )
            validate_output(actual, output_format)
        except ProgramError as exc:
            raise StudioError(
                "TEST_FAILED", f"新 Handler 第 {index} 個合成測試執行失敗：{str(exc)[:500]}"
            ) from exc
        if actual != example.expected:
            raise StudioError(
                "TEST_FAILED",
                f"新 Handler 第 {index} 個合成測試成果不符。預期：{json.dumps(example.expected[:512], ensure_ascii=False)}；實際：{json.dumps(actual[:512], ensure_ascii=False)}",
            )
        results.append(
            {
                "case": index,
                "status": "passed",
                "output_sha256": hashlib.sha256(actual.encode()).hexdigest(),
            }
        )
    return {
        "program_sha256": hashlib.sha256(contract.program.encode()).hexdigest(),
        "evidence_kind": "model_proposed_examples_not_business_acceptance",
        "tests": results,
    }


def generation_contract():
    return {
        "tools": {
            "data.transform_text@1.0.0": "UTF-8 text",
            "data.transform_csv@1.0.0": "CSV with unique header and fixed column count",
            "data.transform_json@1.0.0": "valid finite JSON without duplicate keys",
        },
        "parameters_schema": GeneratedHandler.model_json_schema(),
        "instructions": "Prefer existing tools. If no existing operation implements the goal, use the appropriate data.transform_* tool and generate transform(text, params) with at least two distinct synthetic examples in parameters.tests. Return UTF-8 text matching the selected output format. This is a bounded Python subset: assignments, if/for, list/dict comprehensions, indexing, arithmetic and approved calls only. No imports, while, additional functions, classes, decorators, recursion, eval, exec, open, OS, network or attribute reflection. Do not embed private input in synthetic examples. Generated tests are not business acceptance.",
        "helpers": [
            "json_loads(text)",
            "json_dumps(value, indent=0..8)",
            "csv_read(text, delimiter=',')",
            "csv_write(rows, fieldnames, delimiter=',')",
            "sort_rows(rows, field, numeric=False, reverse=False)",
            "len/str/int/float/bool/abs/round/sum/min/max/all/any/sorted",
            "range/enumerate/zip/list",
        ],
        "methods": [
            "str: strip/lstrip/rstrip/lower/upper/replace/split/splitlines/startswith/endswith/join",
            "dict: get/keys/values/items",
            "list: append",
        ],
        "limits": {
            "input_output_bytes": 10 * 1024 * 1024,
            "collection_items": 20000,
            "operations": 500000,
            "seconds": 5,
        },
    }
