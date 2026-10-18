"""Form fields from the JSON schemas of node parameters: how the GUI's advanced settings show and edit a value.

`SchemaForms` resolves references (`$ref`) and optional values (`X | None`) and asks its field kinds in order; the
first that accepts the schema makes the field. A new kind of value is one more `FieldKind` here and a renderer of the
same name in static/advanced.js (`FIELD_KINDS`). Objects get one field per key, so every single value inside a
preset (`climate.heights.tree_line`) can be changed on its own; what no kind accepts is edited as JSON text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

type Schema = Mapping[str, Any]

_LIMITS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
_SCALARS = {"boolean": "bool", "integer": "integer", "number": "number", "string": "text"}


@dataclass(frozen=True)
class FormField:
    """One value of a form. `kind` names the renderer; objects (`object`) hold their `fields` by key."""

    kind: str
    description: str = ""
    unit: str = ""
    nullable: bool = False
    limits: Mapping[str, float] | None = None
    choices: tuple[Any, ...] = ()
    item: str = ""
    """list: kind of the items."""
    length: int | None = None
    """list: fixed number of items."""
    fields: tuple[tuple[str, FormField], ...] = ()

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        optional = {"description": self.description, "unit": self.unit, "nullable": self.nullable,
                    "limits": dict(self.limits or {}), "choices": list(self.choices), "item": self.item,
                    "length": self.length}
        out.update({k: v for k, v in optional.items() if v not in ("", False, None, {}, [])})
        if self.kind == "object":
            out["fields"] = [{"key": key, **f.as_json()} for key, f in self.fields]
        return out


class FieldKind(Protocol):
    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        """The field for a resolved schema and the value it shows, or None if this kind does not fit."""


class ChoiceKind:
    """A fixed set of values (enum, Literal)."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        if "enum" in schema:
            return FormField("choice", choices=tuple(schema["enum"]))
        if "const" in schema:
            return FormField("choice", choices=(schema["const"],))
        return None


class ScalarKind:
    """Yes/no, whole numbers, numbers (with limits) and texts."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        kind = _SCALARS.get(schema.get("type", ""))
        if kind is None:
            return None
        limits = {k: schema[k] for k in _LIMITS if k in schema}
        return FormField(kind, limits=limits or None)


class ListKind:
    """A list of plain values, also of fixed length (tuple[float, float])."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        if schema.get("type") != "array":
            return None
        if "prefixItems" in schema:
            kinds = {_SCALARS.get(forms.resolve(item).get("type", "")) for item in schema["prefixItems"]}
            item, length = (kinds.pop() if len(kinds) == 1 else None), len(schema["prefixItems"])
        else:
            item, length = _SCALARS.get(forms.resolve(schema.get("items", {})).get("type", "")), None
        return None if item in (None, "bool") else FormField("list", item=str(item), length=length)


class ObjectKind:
    """A table with known keys (a parameter model): one field per key."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        properties = schema.get("properties")
        if properties is None or not isinstance(value, Mapping):
            return None
        return FormField("object", fields=tuple((key, forms.field(sub, value.get(key, sub.get("default"))))
                                                for key, sub in properties.items()))


class MapKind:
    """A table with free keys (dict[str, X]): one field per key of the value shown."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        items = schema.get("additionalProperties")
        if schema.get("type") != "object" or not isinstance(items, Mapping) or not isinstance(value, Mapping):
            return None
        return FormField("object", fields=tuple((key, forms.field(items, v)) for key, v in value.items()))


class VariantKind:
    """One of several tables told apart by a key (a discriminated union): the form of the variant shown."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        tag = schema.get("discriminator", {})
        if not tag or not isinstance(value, Mapping):
            return None
        ref = tag.get("mapping", {}).get(value.get(tag.get("propertyName")))
        return None if ref is None else forms.field({"$ref": ref}, value)


class JsonKind:
    """Everything else (lists of tables, mixed lists, a switched-off part): the whole value as JSON text."""

    def field(self, schema: Schema, value: Any, forms: SchemaForms) -> FormField | None:
        return FormField("json")


DEFAULT_KINDS: tuple[FieldKind, ...] = (ChoiceKind(), ScalarKind(), ListKind(), VariantKind(), ObjectKind(),
                                        MapKind(), JsonKind())


class SchemaForms:
    """Fields for the schema of one parameter model (`model_json_schema()`, references in its `$defs`)."""

    def __init__(self, defs: Mapping[str, Schema], kinds: Sequence[FieldKind] = DEFAULT_KINDS) -> None:
        self._defs = defs
        self._kinds = kinds

    def field(self, schema: Schema, value: Any) -> FormField:
        resolved = self.resolve(schema)
        options = [o for o in resolved.get("anyOf", ()) if self.resolve(o).get("type") != "null"]
        nullable = len(options) < len(resolved.get("anyOf", ()))
        if len(options) == 1:                  # X | None
            inner = self.resolve(options[0])
            resolved = {**inner, "description": resolved.get("description", inner.get("description", ""))}
        description = str(resolved.get("description", ""))
        unit = str(schema.get("unit", resolved.get("unit", "")))
        for kind in self._kinds:
            made = kind.field(resolved, value, self)
            if made is not None:
                return replace(made, description=description, unit=unit, nullable=nullable)
        raise ValueError(f"no field kind for {dict(schema)}")   # JsonKind accepts everything

    def resolve(self, schema: Schema) -> Schema:
        """The schema with its reference replaced by the referenced one (own keys like `description` win)."""
        while "$ref" in schema or (len(schema.get("allOf", ())) == 1):
            ref = schema["$ref"] if "$ref" in schema else schema["allOf"][0].get("$ref")
            own = {k: v for k, v in schema.items() if k not in ("$ref", "allOf")}
            target = self._defs[ref.rsplit("/", 1)[-1]] if ref else schema["allOf"][0]
            schema = {**target, **own}
        return schema
