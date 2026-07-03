# Vendored from ConceptPending/statespec statespec-v0.4.0 (src/statespec) — DO NOT EDIT.
# Only import statements' `statespec` prefix -> `kernel` is rewritten.
# Sync/verify with scripts/vendor_kernel.py.
"""Declarative guard/invariant expressions.

A condition is a small tree of pure data — comparisons over an entity context,
combined with and/or/not — so the same object evaluates at runtime, renders
into the generated doc, serialises to JSON, and diffs structurally. The
rendered policy *is* the enforced policy; a change to it shows up in the doc and
in a diff. See docs/statespec-expressions.md.

The grammar is deliberately closed at compare + boolean. Anything richer (live
cross-entity lookups, external calls) is pre-materialised into a context field
by the service, or expressed as an `Opaque` escape hatch (§11 of the design),
which is versioned and registered so its body cannot change unseen.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Mapping

# Type tags for the field schema and literals.
TYPE_TAGS = frozenset({"int", "decimal", "str", "bool", "uuid", "date", "null"})
_ORDERED = frozenset({"int", "decimal", "date"})  # legal for lt/le/gt/ge
_NUMERIC = frozenset({"int", "decimal"})


class ExpressionError(Exception):
    """A condition could not be evaluated — a missing context field, an
    incomparable pair, or a malformed operand. Indicates a service/spec
    contract bug (validate should prevent it), surfaced as HTTP 500."""


def _tag_of_value(v: object) -> str:
    # bool is a subclass of int — check it first.
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, Decimal):
        return "decimal"
    if isinstance(v, str):
        return "str"
    if isinstance(v, _uuid.UUID):
        return "uuid"
    if isinstance(v, _dt.date):
        return "date"
    if v is None:
        return "null"
    raise ExpressionError(f"unsupported literal type {type(v).__name__}")


# --- Operands ---------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    name: str

    def resolve(self, ctx: Mapping[str, object]) -> object:
        if self.name not in ctx:
            raise ExpressionError(f"field {self.name!r} not in context")
        return ctx[self.name]


@dataclass(frozen=True)
class Literal:
    value: object  # int | Decimal | str | bool | None | uuid | date | tuple

    def __post_init__(self):
        if isinstance(self.value, tuple):
            for el in self.value:
                _tag_of_value(el)  # validates each element type
        elif isinstance(self.value, float):
            raise ExpressionError("float literals are not allowed; use Decimal")
        else:
            _tag_of_value(self.value)  # validates type

    def resolve(self, ctx: Mapping[str, object]) -> object:
        return self.value


Operand = "Field | Literal"


# --- Conditions -------------------------------------------------------------

_OPS: dict[str, Callable[[object, object], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}
_OP_SYMBOL = {
    "eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥",
    "in": "∈", "not_in": "∉",
}


@dataclass(frozen=True)
class Compare:
    op: str
    left: "Field | Literal"
    right: "Field | Literal"

    def evaluate(self, ctx: Mapping[str, object]) -> bool:
        a = self.left.resolve(ctx)
        b = self.right.resolve(ctx)
        try:
            return bool(_OPS[self.op](a, b))
        except KeyError as exc:
            raise ExpressionError(f"unknown operator {self.op!r}") from exc
        except TypeError as exc:
            raise ExpressionError(
                f"cannot apply {self.op!r} to {type(a).__name__} and "
                f"{type(b).__name__}"
            ) from exc

    def fields(self) -> frozenset[str]:
        out = set()
        for o in (self.left, self.right):
            if isinstance(o, Field):
                out.add(o.name)
        return frozenset(out)


@dataclass(frozen=True)
class All:
    terms: tuple

    def evaluate(self, ctx: Mapping[str, object]) -> bool:
        return all(t.evaluate(ctx) for t in self.terms)

    def fields(self) -> frozenset[str]:
        return frozenset().union(*(t.fields() for t in self.terms)) if self.terms else frozenset()


@dataclass(frozen=True)
class Any:
    terms: tuple

    def evaluate(self, ctx: Mapping[str, object]) -> bool:
        return any(t.evaluate(ctx) for t in self.terms)

    def fields(self) -> frozenset[str]:
        return frozenset().union(*(t.fields() for t in self.terms)) if self.terms else frozenset()


@dataclass(frozen=True)
class Not:
    term: object

    def evaluate(self, ctx: Mapping[str, object]) -> bool:
        return not self.term.evaluate(ctx)

    def fields(self) -> frozenset[str]:
        return self.term.fields()


# --- Opaque escape hatch + its registry -------------------------------------
# (name, version) -> predicate. Registered at import (spec definition) time;
# treated as immutable thereafter. A re-register with a different identity is a
# duplicate error; a missing entry fails validation (startup), not first-eval.

_OPAQUE_REGISTRY: dict[tuple[str, int], Callable[[Mapping[str, object]], bool]] = {}


def register_opaque(
    name: str, version: int, fn: Callable[[Mapping[str, object]], bool]
) -> None:
    key = (name, version)
    if key in _OPAQUE_REGISTRY:
        raise ExpressionError(f"opaque {name!r} v{version} already registered")
    _OPAQUE_REGISTRY[key] = fn


def opaque_registered(name: str, version: int) -> bool:
    return (name, version) in _OPAQUE_REGISTRY


@dataclass(frozen=True)
class Opaque:
    name: str
    version: int
    label: str

    def evaluate(self, ctx: Mapping[str, object]) -> bool:
        fn = _OPAQUE_REGISTRY.get((self.name, self.version))
        if fn is None:
            raise ExpressionError(
                f"opaque {self.name!r} v{self.version} is not registered"
            )
        return bool(fn(ctx))

    def fields(self) -> frozenset[str]:
        return frozenset()  # body is code; fields it reads are not introspectable


Expr = "Compare | All | Any | Not | Opaque"


# --- Authoring DSL ----------------------------------------------------------


def literal(value: object) -> Literal:
    return Literal(value)


def _as_operand(x: object) -> "Field | Literal":
    if isinstance(x, (Field, Literal)):
        return x
    if isinstance(x, _FieldRef):
        return x._field
    return Literal(x)


class _FieldRef:
    """Fluent builder: field("amount").le(10000)."""

    def __init__(self, name: str):
        self._field = Field(name)

    def _cmp(self, op: str, other: object) -> Compare:
        return Compare(op, self._field, _as_operand(other))

    def eq(self, other): return self._cmp("eq", other)
    def ne(self, other): return self._cmp("ne", other)
    def lt(self, other): return self._cmp("lt", other)
    def le(self, other): return self._cmp("le", other)
    def gt(self, other): return self._cmp("gt", other)
    def ge(self, other): return self._cmp("ge", other)

    def is_in(self, collection) -> Compare:
        return Compare("in", self._field, Literal(tuple(collection)))

    def not_in(self, collection) -> Compare:
        return Compare("not_in", self._field, Literal(tuple(collection)))


def field(name: str) -> _FieldRef:
    return _FieldRef(name)


def all_(*terms) -> All:
    return All(tuple(terms))


def any_(*terms) -> Any:
    return Any(tuple(terms))


def not_(term) -> Not:
    return Not(term)


def opaque(name: str, version: int, label: str, fn=None) -> Opaque:
    """Build (and, if fn given, register) an opaque condition. Prefer
    materialising a fact into a context field over reaching for this."""
    if fn is not None:
        register_opaque(name, version, fn)
    return Opaque(name=name, version=version, label=label)


# --- Serialisation ----------------------------------------------------------


def _operand_to_dict(o: "Field | Literal") -> dict:
    if isinstance(o, Field):
        return {"kind": "field", "name": o.name}
    v = o.value
    if isinstance(v, tuple):
        return {"kind": "literal", "type": "list",
                "value": [_scalar_to_json(e) for e in v]}
    return {"kind": "literal", "type": _tag_of_value(v), "value": _scalar_to_json(v)}


def _scalar_to_json(v: object) -> object:
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")  # canonical, no exponent/trailing noise
    if isinstance(v, (_uuid.UUID, _dt.date)):
        return str(v)
    return v  # int, str, bool, None


def to_dict(expr) -> dict:
    if isinstance(expr, Compare):
        return {"kind": "compare", "op": expr.op,
                "left": _operand_to_dict(expr.left),
                "right": _operand_to_dict(expr.right)}
    if isinstance(expr, All):
        return {"kind": "all", "terms": [to_dict(t) for t in expr.terms]}
    if isinstance(expr, Any):
        return {"kind": "any", "terms": [to_dict(t) for t in expr.terms]}
    if isinstance(expr, Not):
        return {"kind": "not", "term": to_dict(expr.term)}
    if isinstance(expr, Opaque):
        return {"kind": "opaque", "name": expr.name, "version": expr.version,
                "label": expr.label, "review_required": True}
    raise ExpressionError(f"cannot serialise {type(expr).__name__}")


def _scalar_from_json(type_tag: str, value: object) -> object:
    if type_tag == "decimal":
        return Decimal(str(value))
    if type_tag == "uuid":
        return _uuid.UUID(str(value))
    if type_tag == "date":
        return _dt.date.fromisoformat(str(value))
    return value  # int, str, bool, null


def _operand_from_dict(d: dict) -> "Field | Literal":
    if d["kind"] == "field":
        return Field(d["name"])
    if d["type"] == "list":
        # v1 collections are homogeneous str/int (e.g. `status in (...)`), which
        # JSON round-trips losslessly. Typed collections (decimal/uuid/date)
        # would need per-element tags — not used yet.
        return Literal(tuple(d["value"]))
    return Literal(_scalar_from_json(d["type"], d["value"]))


def from_dict(d: dict):
    kind = d["kind"]
    if kind == "compare":
        return Compare(d["op"], _operand_from_dict(d["left"]),
                       _operand_from_dict(d["right"]))
    if kind == "all":
        return All(tuple(from_dict(t) for t in d["terms"]))
    if kind == "any":
        return Any(tuple(from_dict(t) for t in d["terms"]))
    if kind == "not":
        return Not(from_dict(d["term"]))
    if kind == "opaque":
        return Opaque(d["name"], d["version"], d["label"])
    raise ExpressionError(f"unknown node kind {kind!r}")


def to_canonical_bytes(expr) -> bytes:
    """Deterministic serialisation for the (deferred) identity/hash layer."""
    import json
    return json.dumps(to_dict(expr), sort_keys=True, separators=(",", ":")).encode()


# --- Rendering --------------------------------------------------------------


def _operand_render(o: "Field | Literal") -> str:
    if isinstance(o, Field):
        return o.name
    v = o.value
    if isinstance(v, tuple):
        return "{" + ", ".join(_operand_render(Literal(e)) for e in v) + "}"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    return str(v)


def render(expr) -> str:
    if isinstance(expr, Compare):
        return f"{_operand_render(expr.left)} {_OP_SYMBOL[expr.op]} {_operand_render(expr.right)}"
    if isinstance(expr, All):
        return "(" + " and ".join(render(t) for t in expr.terms) + ")" if expr.terms else "true"
    if isinstance(expr, Any):
        return "(" + " or ".join(render(t) for t in expr.terms) + ")" if expr.terms else "false"
    if isinstance(expr, Not):
        return f"not ({render(expr.term)})"
    if isinstance(expr, Opaque):
        return f"«{expr.name}:v{expr.version}» — custom code (requires technical review)"
    raise ExpressionError(f"cannot render {type(expr).__name__}")


# --- Static type-checking (called by core.validate) -------------------------


def _operand_tag(o: "Field | Literal", fields: Mapping[str, str]) -> tuple[str, list[str]]:
    """Return (type_tag, problems). For a collection literal, tag is 'list'."""
    if isinstance(o, Field):
        if o.name not in fields:
            return ("?", [f"field {o.name!r} is not in the spec's field schema"])
        return (fields[o.name], [])
    if isinstance(o.value, tuple):
        return ("list", [])
    return (_tag_of_value(o.value), [])


def typecheck(expr, fields: Mapping[str, str]) -> list[str]:
    """Return human-readable type problems for an expression. Empty == ok."""
    problems: list[str] = []
    if isinstance(expr, Compare):
        lt, lp = _operand_tag(expr.left, fields)
        problems += lp
        if expr.op in ("in", "not_in"):
            if not (isinstance(expr.right, Literal) and isinstance(expr.right.value, tuple)):
                problems.append(f"{expr.op!r} requires a collection literal on the right")
            else:
                el_tags = {_tag_of_value(e) for e in expr.right.value}
                if len(el_tags) > 1:
                    problems.append(f"{expr.op!r} collection is not homogeneous: {sorted(el_tags)}")
                elif el_tags and not _compatible(lt, next(iter(el_tags))):
                    problems.append(
                        f"{expr.op!r} compares {lt} field to {next(iter(el_tags))} elements")
        else:
            rt, rp = _operand_tag(expr.right, fields)
            problems += rp
            if "?" not in (lt, rt) and "list" not in (lt, rt):
                if expr.op in ("lt", "le", "gt", "ge"):
                    if lt not in _ORDERED or rt not in _ORDERED or not _comparable(lt, rt):
                        problems.append(f"{expr.op!r} needs ordered, comparable operands; got {lt}, {rt}")
                else:  # eq / ne
                    if not _compatible(lt, rt):
                        problems.append(f"{expr.op!r} compares incompatible types {lt}, {rt}")
    elif isinstance(expr, (All, Any)):
        if not expr.terms:
            problems.append(f"empty {type(expr).__name__} is not allowed in an authored spec")
        for t in expr.terms:
            problems += typecheck(t, fields)
    elif isinstance(expr, Not):
        problems += typecheck(expr.term, fields)
    elif isinstance(expr, Opaque):
        if not opaque_registered(expr.name, expr.version):
            problems.append(f"opaque {expr.name!r} v{expr.version} is not registered")
    return problems


def _compatible(a: str, b: str) -> bool:
    """eq/ne and in: same tag, or int<->decimal."""
    return a == b or {a, b} <= _NUMERIC


def _comparable(a: str, b: str) -> bool:
    """Ordering: both numeric, or both date."""
    return ({a, b} <= _NUMERIC) or (a == "date" and b == "date")


# --- Runtime context type-checking ------------------------------------------
# Static typecheck() proves the *expression* is well-typed against the field
# schema. This proves the *values* a service actually supplied match it — so a
# snapshot with "0" (str) where an int was declared fails loud rather than
# silently changing a decision. Cheap (dicts are tiny); run on every apply().

_RUNTIME_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "decimal": lambda v: isinstance(v, (int, float, Decimal)) and not isinstance(v, bool),
    "str": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "uuid": lambda v: isinstance(v, _uuid.UUID),
    "date": lambda v: isinstance(v, _dt.date),
    "null": lambda v: v is None,
}


def validate_context(fields: Mapping[str, str], ctx: Mapping[str, object]) -> None:
    """Raise ExpressionError if any present field's runtime value doesn't match
    its declared type tag. None is only allowed for the `null` tag — a None
    where a value is expected is a contract breach (and could silently flip a
    comparison), not a policy decision."""
    for name, tag in fields.items():
        if name not in ctx:
            continue  # presence is _require_fields' job
        check = _RUNTIME_CHECKS.get(tag)
        if check is not None and not check(ctx[name]):
            raise ExpressionError(
                f"context field {name!r} expected {tag}, got "
                f"{type(ctx[name]).__name__}"
            )
