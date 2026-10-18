# ADR 0004: License Apache-2.0

Date: 2026-10-12 - Status: accepted

## Context

The tool shall spread as a standard for TF3 map making; others shall write node packages for it. It will be published
after the first release.

## Decision

Apache License 2.0 for the project. Original game files are explicitly not covered (NOTICE). Third-party data used in
generated maps keeps its license; attributions are added to the output.

## Alternatives

- **MIT** - equally permissive and shorter, but no explicit patent grant and no rules for contributions.
- **GPL-3.0** - changes by others stay open, but hinders adoption and forces plugins under the GPL.
- **MPL-2.0** - file-level copyleft, a middle way; less common in the Python ecosystem.

## Consequences

Permissive use, plugins under any license, explicit patent grant and contribution terms. All current dependencies
(numpy, scipy, pillow, rasterio, GDAL, osmium, pydantic) are BSD/MIT licensed and compatible.
