# ADR 0006: Nodes are pure functions; everything else is object-oriented

Date: 2026-10-12 - Status: accepted

## Context

The legacy project required strict object orientation. In the new paradigm a node is "a small pure function" - the
contract node authors see should be as small as possible.

## Decision

- A node is a function with `@node`; its signature declares ports, parameters and outputs. Large algorithms inside a
  macro node may use classes (e.g. legacy classes taken over).
- The framework, data types, sources and applications stay strictly object-oriented (single responsibility,
  constructor injection, registries instead of conditionals, no global mutable state).

## Alternatives

- **Node classes** (`class Below(Node): inputs = ...; def run(...)`) - more ceremony per node, ports declared twice
  (attributes and run signature) or untyped.
- **Functions without decorator, registered in a central list** - a central list every plugin must change.

## Consequences

Writing a node needs no framework knowledge beyond the decorator; type checkers see real types. Registration happens
by scanning modules (built-in and entry-point plugins), without global registries.
