"""Bounded Python data-transform interpreter. Never evals/imports generated code.

Only JSON-like values, explicit pure helpers and an allowlisted AST are available.
This is a language boundary, not a claim of an OS sandbox for arbitrary Python.
"""

import ast
import csv
import io
import json
import math
import operator
import re
import time
from dataclasses import dataclass


class ProgramError(ValueError):
    pass


def load_json(text):
    def constant(_value):
        raise ProgramError("Non-finite JSON value")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProgramError("Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(text, parse_constant=constant, object_pairs_hook=pairs)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 10 * 1024 * 1024
    max_items: int = 20_000
    max_steps: int = 500_000
    max_allocation: int = 64 * 1024 * 1024
    timeout: float = 5.0


HELPERS = frozenset(
    {
        "json_loads",
        "json_dumps",
        "csv_read",
        "csv_write",
        "sort_rows",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "abs",
        "round",
        "sum",
        "min",
        "max",
        "all",
        "any",
        "sorted",
        "range",
        "enumerate",
        "zip",
        "list",
    }
)
METHODS = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "replace",
        "split",
        "splitlines",
        "startswith",
        "endswith",
        "join",
        "get",
        "keys",
        "values",
        "items",
        "append",
    }
)
ALLOWED = (
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Expr,
    ast.Constant,
    ast.Assign,
    ast.AugAssign,
    ast.Return,
    ast.If,
    ast.For,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.ListComp,
    ast.DictComp,
    ast.comprehension,
    ast.Subscript,
    ast.Slice,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.UAdd,
    ast.USub,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Call,
    ast.Attribute,
    ast.keyword,
)
BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def validate_program(source):
    if not isinstance(source, str) or len(source.encode("utf-8")) > 32768:
        raise ProgramError("Program source exceeds 32 KiB")
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError, ValueError) as exc:
        raise ProgramError("Invalid Python source") from exc
    body = list(tree.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    if len(body) != 1 or not isinstance(body[0], ast.FunctionDef):
        raise ProgramError("Expected one transform(text, params) function")
    function = body[0]
    args = function.args
    if (
        function.name != "transform"
        or function.decorator_list
        or function.returns
        or getattr(function, "type_params", [])
        or args.posonlyargs
        or args.kwonlyargs
        or args.defaults
        or args.kw_defaults
        or args.vararg
        or args.kwarg
        or [a.arg for a in args.args] != ["text", "params"]
        or any(a.annotation for a in args.args)
    ):
        raise ProgramError(
            "Expected unannotated transform(text, params) without decorators/defaults"
        )
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    stack, count = [(tree, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 1500 or depth > 64:
            raise ProgramError("Program AST is too large or deeply nested")
        if not isinstance(node, ALLOWED):
            raise ProgramError(f"Unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.FunctionDef) and node is not function:
            raise ProgramError("Nested functions are not allowed")
        if isinstance(node, ast.Name):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", node.id):
                raise ProgramError("Invalid variable name")
            if isinstance(node.ctx, ast.Store) and node.id in HELPERS | {"transform"}:
                raise ProgramError("Cannot shadow a helper")
        if isinstance(node, ast.Constant) and type(node.value) not in {
            str,
            int,
            float,
            bool,
            type(None),
        }:
            raise ProgramError("Only JSON-like literals are allowed")
        if isinstance(node, ast.Attribute):
            parent = parents.get(node)
            if (
                node.attr not in METHODS
                or not isinstance(parent, ast.Call)
                or parent.func is not node
            ):
                raise ProgramError("Attribute access is limited to approved data methods")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Attribute)
            or isinstance(node.func, ast.Name)
            and node.func.id in HELPERS
        ):
            raise ProgramError("Only approved pure helpers can be called")
        if isinstance(node, ast.keyword) and node.arg is None:
            raise ProgramError("Keyword expansion is not allowed")
        if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
            raise ProgramError("Dictionary expansion is not allowed")
        if isinstance(node, ast.comprehension) and node.is_async:
            raise ProgramError("Async comprehensions are not allowed")
        if isinstance(node, ast.AugAssign) and not (
            isinstance(node.target, ast.Name)
            or isinstance(node.target, ast.Subscript)
            and isinstance(node.target.value, ast.Name)
            and isinstance(node.target.slice, (ast.Name, ast.Constant))
        ):
            raise ProgramError("Augmented assignment requires a local or simple slot")
        if isinstance(node, (ast.Break, ast.Continue)):
            ancestor = parents.get(node)
            while ancestor is not None and not isinstance(ancestor, (ast.For, ast.FunctionDef)):
                ancestor = parents.get(ancestor)
            if not isinstance(ancestor, ast.For):
                raise ProgramError("Loop control outside a loop")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return function


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class Interpreter:
    def __init__(self, limits):
        self.limits, self.steps, self.allocated = limits, 0, 0
        self.deadline = time.monotonic() + limits.timeout
        self.locals = {}
        self.line = 0
        self.container_sizes = {}

    def tick(self, cost=1):
        self.steps += cost
        if self.steps > self.limits.max_steps or time.monotonic() > self.deadline:
            raise ProgramError("Program operation/time limit exceeded")

    def measure(self, value, ancestors=None, depth=0):
        if depth > 32:
            raise ProgramError("Value nesting limit exceeded")
        if type(value) is str:
            size = len(value.encode("utf-8"))
        elif type(value) is int:
            if value.bit_length() > 1024:
                raise ProgramError("Integer size limit exceeded")
            size = 32
        elif type(value) is float:
            if not math.isfinite(value):
                raise ProgramError("Non-finite number")
            size = 16
        elif value is None or type(value) is bool:
            size = 8
        elif type(value) in {list, tuple, dict}:
            if len(value) > self.limits.max_items:
                raise ProgramError("Collection size limit exceeded")
            ancestors = set() if ancestors is None else ancestors
            if id(value) in ancestors:
                raise ProgramError("Cyclic values are not allowed")
            ancestors.add(id(value))
            size = 64 + len(value) * 16
            values = value
            if isinstance(value, dict):
                if not all(type(key) is str for key in value):
                    raise ProgramError("Dictionary keys must be strings")
                values = [*value.keys(), *value.values()]
            for item in values:
                size += self.measure(item, ancestors, depth + 1)
                if size > self.limits.max_bytes:
                    raise ProgramError("Value size limit exceeded")
            ancestors.remove(id(value))
        else:
            raise ProgramError("Only JSON-like values are available")
        if size > self.limits.max_bytes:
            raise ProgramError("Value size limit exceeded")
        return size

    def bound(self, value, *, allocate=True):
        size = self.measure(value)
        self.tick(1 + size // 1024)
        if allocate:
            self.allocated += size
            if self.allocated > self.limits.max_allocation:
                raise ProgramError("Program allocation limit exceeded")
        if type(value) in {list, dict} and (allocate or id(value) in self.container_sizes):
            self.container_sizes[id(value)] = (value, size)
        return value

    def iterable(self, value):
        if type(value) not in {list, tuple, dict, str} or len(value) > self.limits.max_items:
            raise ProgramError("Iteration requires a bounded collection")
        return value

    def mutate(self, container, value):
        # Forbid cycles before mutation; shared acyclic data remains permitted.
        def contains(candidate, seen):
            if candidate is container:
                return True
            if type(candidate) not in {dict, list, tuple} or id(candidate) in seen:
                return False
            seen.add(id(candidate))
            return any(
                contains(item, seen)
                for item in (candidate.values() if isinstance(candidate, dict) else candidate)
            )

        if contains(value, set()):
            raise ProgramError("Cyclic mutation is not allowed")
        size = self.measure(value) + 16
        previous = self.container_sizes.get(id(container))
        previous_size = previous[1] if previous is not None and previous[0] is container else None
        if previous_size is None:
            previous_size = self.measure(container)
        new_size = previous_size + size
        if new_size > self.limits.max_bytes or len(container) >= self.limits.max_items:
            raise ProgramError("Collection mutation limit exceeded")
        self.container_sizes[id(container)] = (container, new_size)
        self.allocated += size
        if self.allocated > self.limits.max_allocation:
            raise ProgramError("Program allocation limit exceeded")

    def assign(self, target, value):
        if isinstance(target, ast.Name):
            self.locals[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            if type(value) not in {tuple, list} or len(target.elts) != len(value):
                raise ProgramError("Unpacking shape mismatch")
            for key, item in zip(target.elts, value):
                self.assign(key, item)
        elif isinstance(target, ast.Subscript):
            collection, key = self.expr(target.value), self.expr(target.slice)
            if not (
                type(collection) is dict
                and type(key) is str
                or type(collection) is list
                and type(key) is int
            ):
                raise ProgramError("Unsupported assignment target")
            self.mutate(collection, value)
            collection[key] = value
        else:
            raise ProgramError("Unsupported assignment target")

    def statements(self, statements):
        for node in statements:
            self.line = getattr(node, "lineno", self.line)
            self.tick()
            if isinstance(node, ast.Return):
                raise _Return(self.expr(node.value) if node.value else None)
            if isinstance(node, ast.Assign):
                value = self.expr(node.value)
                for target in node.targets:
                    self.assign(target, value)
            elif isinstance(node, ast.AugAssign):
                if type(self.expr(node.target)) in {list, dict}:
                    raise ProgramError("Use append or explicit assignment for collections")
                self.assign(
                    node.target, self.binary(node.op, self.expr(node.target), self.expr(node.value))
                )
            elif isinstance(node, ast.Expr):
                self.expr(node.value)
            elif isinstance(node, ast.If):
                self.statements(node.body if self.expr(node.test) else node.orelse)
            elif isinstance(node, ast.For):
                # Snapshot iteration so appending to the source cannot grow the loop indefinitely.
                items = list(self.iterable(self.expr(node.iter)))
                for item in items:
                    self.tick()
                    self.assign(node.target, item)
                    try:
                        self.statements(node.body)
                    except _Continue:
                        continue
                    except _Break:
                        break
                else:
                    self.statements(node.orelse)
            elif isinstance(node, ast.Break):
                raise _Break()
            elif isinstance(node, ast.Continue):
                raise _Continue()

    def binary(self, op, left, right):
        if isinstance(op, ast.Mult):
            sequence, times = (left, right) if type(right) is int else (right, left)
            if type(sequence) in {str, list, tuple} and type(times) is int:
                if (
                    self.measure(sequence) * max(0, times) > self.limits.max_bytes
                    or type(sequence) is not str
                    and len(sequence) * max(0, times) > self.limits.max_items
                ):
                    raise ProgramError("Repetition limit exceeded")
            elif (
                type(left) is int
                and type(right) is int
                and left.bit_length() + right.bit_length() > 1024
            ):
                raise ProgramError("Integer size limit exceeded")
        if isinstance(op, ast.Add) and type(left) in {str, list, tuple}:
            if self.measure(left) + self.measure(right) > self.limits.max_bytes:
                raise ProgramError("Concatenation limit exceeded")
        if isinstance(op, ast.Mod) and isinstance(left, str):
            raise ProgramError("String format expressions are not supported")
        return self.bound(BINARY[type(op)](left, right))

    def comprehension(self, node):
        result = {} if isinstance(node, ast.DictComp) else []
        self.bound(result)
        saved = dict(self.locals)

        def visit(index):
            self.tick()
            if index == len(node.generators):
                key = self.expr(node.key) if isinstance(node, ast.DictComp) else None
                value = self.expr(node.value if isinstance(node, ast.DictComp) else node.elt)
                self.mutate(result, value)
                if isinstance(node, ast.DictComp):
                    if type(key) is not str:
                        raise ProgramError("Dictionary keys must be strings")
                    result[key] = value
                else:
                    result.append(value)
                return
            generator = node.generators[index]
            for item in list(self.iterable(self.expr(generator.iter))):
                self.assign(generator.target, item)
                if all(self.expr(test) for test in generator.ifs):
                    visit(index + 1)

        try:
            visit(0)
        finally:
            self.locals = saved
        return self.bound(result, allocate=False)

    def expr(self, node):
        self.line = getattr(node, "lineno", self.line)
        self.tick()
        if isinstance(node, ast.Constant):
            return self.bound(node.value, allocate=False)
        if isinstance(node, ast.Name):
            if node.id not in self.locals:
                raise ProgramError("Unknown local variable")
            return self.locals[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self.expr(item) for item in node.elts]
            return self.bound(tuple(items) if isinstance(node, ast.Tuple) else items)
        if isinstance(node, ast.Dict):
            return self.bound({self.expr(k): self.expr(v) for k, v in zip(node.keys, node.values)})
        if isinstance(node, ast.Subscript):
            value = self.expr(node.value)
            if type(value) not in {dict, list, tuple, str}:
                raise ProgramError("Unsupported subscript")
            return value[self.expr(node.slice)]
        if isinstance(node, ast.Slice):
            parts = [
                self.expr(part) if part else None for part in (node.lower, node.upper, node.step)
            ]
            if any(part is not None and type(part) is not int for part in parts):
                raise ProgramError("Slice indices must be integers")
            return slice(*parts)
        if isinstance(node, ast.BinOp):
            return self.binary(node.op, self.expr(node.left), self.expr(node.right))
        if isinstance(node, ast.UnaryOp):
            return self.bound(
                {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos}[
                    type(node.op)
                ](self.expr(node.operand))
            )
        if isinstance(node, ast.BoolOp):
            value = self.expr(node.values[0])
            for item in node.values[1:]:
                if (
                    isinstance(node.op, ast.And)
                    and not value
                    or isinstance(node.op, ast.Or)
                    and value
                ):
                    break
                value = self.expr(item)
            return value
        if isinstance(node, ast.Compare):
            left = self.expr(node.left)
            for operation, other in zip(node.ops, node.comparators):
                right = self.expr(other)
                self.bound([left, right], allocate=False)
                if not COMPARE[type(operation)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, (ast.ListComp, ast.DictComp)):
            return self.comprehension(node)
        if isinstance(node, ast.Call):
            args = [self.expr(arg) for arg in node.args]
            kwargs = {kw.arg: self.expr(kw.value) for kw in node.keywords}
            self.bound([args, kwargs], allocate=False)
            if isinstance(node.func, ast.Attribute):
                return self.method(self.expr(node.func.value), node.func.attr, args, kwargs)
            return self.helper(node.func.id, args, kwargs)
        raise ProgramError("Unsupported expression")

    def method(self, value, name, args, kwargs):
        if type(value) is list and name == "append" and len(args) == 1 and not kwargs:
            self.mutate(value, args[0])
            value.append(args[0])
            return None
        self.bound(value, allocate=False)
        if type(value) is dict and name in {"get", "keys", "values", "items"}:
            result = getattr(value, name)(*args, **kwargs)
            return self.bound(result, allocate=False) if name == "get" else self.bound(list(result))
        if type(value) is not str or name not in METHODS - {
            "append",
            "get",
            "keys",
            "values",
            "items",
        }:
            raise ProgramError("Method is not available for this value")
        if name == "replace":
            if kwargs or len(args) not in {2, 3} or len(args) == 3 and type(args[2]) is not int:
                raise ProgramError("replace supports positional strings and integer count")
            old, new = args[:2]
            if type(old) is not str or type(new) is not str:
                raise ProgramError("replace needs strings")
            count = (
                min(value.count(old), args[2])
                if len(args) == 3 and args[2] >= 0
                else value.count(old)
            )
            if (
                len(value.encode()) + count * max(0, len(new.encode()) - len(old.encode()))
                > self.limits.max_bytes
            ):
                raise ProgramError("Replacement size limit exceeded")
        elif name == "join":
            if (
                len(args) != 1
                or type(args[0]) not in {list, tuple}
                or not all(type(v) is str for v in args[0])
            ):
                raise ProgramError("join needs a list of strings")
            if (
                sum(len(v.encode()) for v in args[0])
                + len(value.encode()) * max(0, len(args[0]) - 1)
                > self.limits.max_bytes
            ):
                raise ProgramError("Join size limit exceeded")
        elif name == "split":
            if kwargs or len(args) > 2:
                raise ProgramError("split supports positional separator and maximum only")
            limit = args[1] if len(args) == 2 and args[1] >= 0 else self.limits.max_items
            return self.bound(
                value.split(args[0] if args else None, min(limit, self.limits.max_items))
            )
        elif (
            name == "splitlines"
            and sum(value.count(c) for c in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
            > self.limits.max_items
        ):
            raise ProgramError("Line count limit exceeded")
        return self.bound(getattr(value, name)(*args, **kwargs))

    def helper(self, name, args, kwargs):
        simple = {
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "abs": abs,
            "round": round,
            "sum": sum,
            "min": min,
            "max": max,
            "all": all,
            "any": any,
            "sorted": sorted,
        }
        if name in simple:
            return self.bound(simple[name](*args, **kwargs))
        if name in {"range", "enumerate", "zip", "list"}:
            if name == "range":
                value = range(*args, **kwargs)
                if len(value) > self.limits.max_items:
                    raise ProgramError("Range limit exceeded")
            else:
                for arg in args[:1] if name == "enumerate" else args:
                    self.iterable(arg)
                value = {"enumerate": enumerate, "zip": zip, "list": list}[name](*args, **kwargs)
            return self.bound(list(value))
        if name == "json_loads":
            if kwargs or len(args) != 1 or type(args[0]) is not str:
                raise ProgramError("json_loads accepts one string")
            return self.bound(load_json(args[0]))
        if name == "json_dumps":
            if (
                len(args) != 1
                or set(kwargs) - {"indent"}
                or (
                    "indent" in kwargs
                    and (type(kwargs["indent"]) is not int or not 0 <= kwargs["indent"] <= 8)
                )
            ):
                raise ProgramError("json_dumps accepts a value and optional indent 0..8")
            encoder = json.JSONEncoder(
                ensure_ascii=False, sort_keys=True, allow_nan=False, **kwargs
            )
            chunks, size = [], 0
            for chunk in encoder.iterencode(args[0]):
                self.tick()
                size += len(chunk.encode("utf-8"))
                if size > self.limits.max_bytes:
                    raise ProgramError("JSON output limit exceeded")
                chunks.append(chunk)
            return self.bound("".join(chunks))
        if name == "csv_read":
            if len(args) != 1 or type(args[0]) is not str or set(kwargs) - {"delimiter"}:
                raise ProgramError("csv_read accepts text and optional delimiter")
            reader = csv.DictReader(io.StringIO(*args), **kwargs)
            if (
                not reader.fieldnames
                or len(reader.fieldnames) > 1000
                or len(set(reader.fieldnames)) != len(reader.fieldnames)
            ):
                raise ProgramError("CSV requires unique column names")
            rows = []
            self.bound(rows)
            for row in reader:
                self.tick()
                if len(rows) >= self.limits.max_items or None in row:
                    raise ProgramError("CSV size or column mismatch")
                self.mutate(rows, row)
                rows.append(row)
            return self.bound(rows, allocate=False)
        if name == "csv_write":
            if len(args) != 2 or set(kwargs) - {"delimiter"}:
                raise ProgramError("csv_write accepts rows, field names and optional delimiter")
            rows, fields = args
            self.iterable(rows)
            self.iterable(fields)
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", **kwargs)
            writer.writeheader()
            for row in rows:
                self.tick()
                writer.writerow(row)
                if stream.tell() > self.limits.max_bytes:
                    raise ProgramError("CSV output limit exceeded")
            return self.bound(stream.getvalue())
        if name == "sort_rows":
            rows, field = args
            reverse, numeric = kwargs.pop("reverse", False), kwargs.pop("numeric", False)
            if kwargs or type(reverse) is not bool or type(numeric) is not bool:
                raise ProgramError("Invalid sort options")
            self.iterable(rows)
            return self.bound(
                sorted(
                    rows,
                    key=lambda row: float(row[field]) if numeric else row[field],
                    reverse=reverse,
                )
            )
        raise ProgramError("Unknown helper")


def run_program(source, text, arguments=None, *, limits=None):
    if type(text) is not str or arguments is not None and type(arguments) is not dict:
        raise ProgramError("Expected input text and an arguments object")
    function = validate_program(source)
    vm = Interpreter(limits or Limits())
    vm.locals = {
        "text": vm.bound(text),
        "params": vm.bound(json.loads(json.dumps(arguments or {}, allow_nan=False))),
    }
    try:
        vm.statements(function.body)
    except _Return as returned:
        if type(returned.value) is not str:
            raise ProgramError("transform must return text")
        return vm.bound(returned.value, allocate=False)
    except ProgramError as exc:
        raise ProgramError(f"{exc} (line {vm.line})") from exc
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
        RecursionError,
        ZeroDivisionError,
        csv.Error,
        _Break,
        _Continue,
    ) as exc:
        raise ProgramError(f"Transform failed: {type(exc).__name__} (line {vm.line})") from exc
    raise ProgramError("transform did not return text")


def validate_output(text, output_format, *, limits=None):
    limits = limits or Limits()
    try:
        if output_format == "json":
            Interpreter(limits).bound(load_json(text))
        elif output_format == "csv":
            rows = csv.reader(io.StringIO(text))
            header = next(rows, None)
            if not header or len(header) > 1000 or len(header) != len(set(header)):
                raise ProgramError("CSV output requires unique column names")
            for index, row in enumerate(rows, 1):
                if index > limits.max_items or len(row) != len(header):
                    raise ProgramError("CSV output row count or column shape mismatch")
        elif output_format != "txt":
            raise ProgramError("Unknown output format")
    except (ValueError, TypeError, RecursionError, csv.Error) as exc:
        raise ProgramError("Generated output does not match its format contract") from exc


def validate_arguments(arguments, schema):
    types = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "null": (type(None),),
    }
    if type(arguments) is not dict or type(schema) is not dict or set(arguments) != set(schema):
        raise ProgramError("Handler argument names do not match the contract")
    for key, expected in schema.items():
        if expected not in types or type(arguments[key]) not in types[expected]:
            raise ProgramError("Handler argument type mismatch")
        if type(arguments[key]) is float and not math.isfinite(arguments[key]):
            raise ProgramError("Handler arguments must be finite")
