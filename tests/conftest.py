from __future__ import annotations

import pytest

from automapgen.graph.catalog import NodeCatalog
from tests.toy import nodes


@pytest.fixture(scope="session")
def catalog() -> NodeCatalog:
    return NodeCatalog.from_modules([nodes])
