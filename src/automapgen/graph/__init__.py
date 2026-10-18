"""The graph framework: data types, parameters, nodes, composition and execution - without any domain knowledge.

Public API for node authors (stable within a major version):

    from automapgen.graph import NodeContext, NodeParams, data_type, node, param

`merged_tables` merges values the way parameter sets layer them (for values made of a base and its differences).

Everything else in this package is used by the application (compiler, engine, files) and may change.
"""

from automapgen.graph.data import Codec, DataType, Preview, data_type
from automapgen.graph.node import NodeContext, node
from automapgen.graph.params import NodeParams, NoParams, param
from automapgen.graph.paramsets import merged_tables

__all__ = ["Codec", "DataType", "NoParams", "NodeContext", "NodeParams", "Preview", "data_type", "merged_tables",
           "node", "param"]
