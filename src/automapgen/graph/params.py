"""Node parameters: typed, immutable, validated models. Their JSON schema feeds the GUI forms and the graph editor.

    class BelowParams(NodeParams):
        limit: float = param(1000.0, unit="m")
        \"\"\"Upper limit; pixels below it are set.\"\"\"

The attribute docstring is the parameter's description.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeParams(BaseModel):
    """Base of all node parameter models: immutable, unknown names are errors, attribute docstrings = descriptions."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class NoParams(NodeParams):
    """Parameters of a node that has none."""


def param(default: Any, *, unit: str | None = None, ge: float | None = None, le: float | None = None,
          gt: float | None = None, lt: float | None = None) -> Any:
    """A parameter with a default value, an optional unit (shown in GUI/editor) and optional limits."""
    if unit:
        return Field(default, ge=ge, le=le, gt=gt, lt=lt, json_schema_extra={"unit": unit})
    return Field(default, ge=ge, le=le, gt=gt, lt=lt)
