# ADR 0007: Cache keys from lineage, not from content

Date: 2026-10-12 - Status: accepted

## Context

Map runs take minutes; most changes touch a few parameters. Results of unaffected nodes shall be reused. Hashing
large rasters (hundreds of MB) on every run is slow.

## Decision

The key of a node output is the hash of its origin: node type, version, hash of the node function's source,
parameters and the keys of its inputs. Only graph inputs are identified by content (data type fingerprint; without
one, a fresh key per run). Keys are computed before anything runs, so the engine skips cached nodes and everything only
they need. Nodes with side effects are never cached.

## Alternatives

- **Content hashes of every result** - exact, but expensive for rasters and only known after computing.
- **Time stamps / manual step selection (legacy tick boxes)** - error-prone, the user must know the dependencies.

## Consequences

Changes in helpers called by a node function are not detected; raise the node version or clear the cache. Sources
reading external data are cached by their parameters; refreshing external data means clearing their cache entries.
