# NumPy 2 type promotion and values that pass through files

Found 2026-10-12 (stage N5c): the same graph gave a heightmap differing by one grey value in 146 pixels depending on
whether an intermediate result came from memory or from the disk cache.

- NumPy 2 (NEP 50): NumPy scalars are strongly typed, Python numbers weakly. `float32_array - np.float64(x)` is
  computed in float64, `float32_array - x` (Python float) in float32.
- A town's position computed from NumPy indices is a `np.float64`; written to JSON and read back it is a Python
  float. The settling arithmetic then ran in float64 (fresh) or float32 (cached / legacy file) - the last bit of the
  heights differed and the 16-bit quantisation flipped some pixels.
- The legacy project passes intermediate results through files, so it always computed with Python floats.

Rule ([node_guide.md](../../docsHuman/node_guide.md), section 5): a value flowing between nodes must be the same as what its codec gives back. JSON
documents normalise their data on creation (`JsonDocument.__post_init__`). Rasters are typed arrays (float32, bool,
uint8, int32) whose codec keeps the dtype.

How it was found: run once without cache, once with a cache filled by that run, compare every node output key by key
(the first differing node was `settle.terrain`, its inputs the town and industry lists).
