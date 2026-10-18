# ADR 0009: Secrets as settings of the run

Date: 2026-10-13 - Status: accepted

## Context

Downloading elevation data from OpenTopography needs a personal API key. Parameters end up in graph documents,
parameter sets (versioned, shared with others) and cache keys; none of them may hold the key.

## Decision

- The engine takes named secrets as a run setting (`Engine(secrets=...)`); a node asks `context.secret(name)` and
  gets a clear error naming the setting when it is missing. The graph framework knows only names.
- The CLI reads them from the table `[secrets]` of `local/settings.toml` (ignored by git), e.g.
  `opentopography_api_key`.
- Secrets are never parameters, so they are neither stored nor part of cache keys; a different key gives the same
  data, so the cache stays valid.

## Alternatives

- **Environment variables read by the node** - nodes must not read the environment; the setting would be invisible.
- **A parameter of the download node** - the key would land in project files and cache keys.

## Consequences

The GUI stores the key in the same local settings. Tests pass fake secrets to the context.
