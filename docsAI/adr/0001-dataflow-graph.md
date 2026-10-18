# ADR 0001: Declarative typed dataflow graph with plugin nodes

Date: 2026-10-12 - Status: accepted

## Context

The legacy project grew from scripts into a clean object-oriented pipeline of coarse steps. Changing behaviour still
meant changing classes or adding strategies, deep in the code and with possible side effects. The tool shall become a
published standard for TF3 maps, extended by others, so it needs a paradigm in which the logic stays constant and maps
differ in data, parameters and composition.

## Decision

Build a new project on a declarative, typed dataflow graph: small pure nodes with typed ports, wired in graph
documents (data, not code), parameters in parameter sets, executed by a domain-free engine. Separate five concerns:
data, parameters, processing, composition, execution. Large algorithms (routing, town placement) are macro nodes,
split where variation is wanted. The legacy project stays unchanged as functional template.

## Alternatives

- **Keep the step pipeline, add more strategies** - cheapest, but every behavioural change remains a code change.
- **Introduce a node engine into the legacy repository step by step** - less risk (always runnable), but carries the
  old structure along; the new project reuses legacy algorithms as macro nodes, which removes most of the rewrite cost.
- **Fine-grained nodes everywhere** (also for planning) - maximum flexibility, but graphs become unreadable and slow;
  rejected in favour of macro nodes at variation points.

## Consequences

- New behaviour by rewiring; new themes (base climates, OSM road network, town districts) are new node packages or
  subgraphs, without touching existing code.
- Per-node cache, previews and checks before running come with the engine.
- Costs: building the framework and taking over all functions of the legacy project; a second "language" (graph documents)
  that needs tooling (checks, diagrams, later an editor).
