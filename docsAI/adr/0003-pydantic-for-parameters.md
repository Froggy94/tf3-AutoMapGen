# ADR 0003: pydantic v2 for parameter schemas

Date: 2026-10-12 - Status: accepted

## Context

Every node declares parameters. One declaration must give validation with clear messages, a JSON schema for GUI forms
and the graph editor, and documentation (units, limits, descriptions).

## Decision

Node parameters are pydantic v2 models (`NodeParams`: frozen, unknown names rejected, attribute docstrings become
descriptions, `param()` adds unit and limits). Graph documents are pydantic models as well. Data types (rasters,
vectors) stay plain frozen dataclasses - pydantic is limited to parameters and documents.

## Alternatives

| Option | For | Against |
|---|---|---|
| Own solution (legacy `binding.py` + dataclasses) | no dependency | JSON schema, unions, nesting and messages to maintain ourselves |
| attrs + cattrs | lean, fast | no JSON schema built in |
| msgspec | very fast, JSON schema | smaller ecosystem, fewer validation rules |
| marshmallow | mature | schema separate from the class -> duplicated declarations |

## Consequences

One dependency more, widely used and typed (mypy plugin enabled). GUI forms and editor property panels can be
generated from `model_json_schema()`.
