# Graph `map_climate`

Generated with `automapgen show map_climate` - do not edit by hand.

flowchart LR
  n_inputs[/"inputs<br/>land_min_m: number<br/>map_format: map_format"/]
  n_reference["reference<br/><small>climate.reference</small>"]
  n_climate_mod[["climate_mod<br/><small>graph:climate_mod</small>"]]
  n_climate_mod_folder["climate_mod_folder<br/><small>file.mod</small>"]
  n_waterfall_preview_png["waterfall_preview_png<br/><small>file.png</small>"]
  n_inputs -- "land_min_m" --> n_reference
  n_reference -- "reference_m" --> n_climate_mod
  n_inputs -- "map_format" --> n_climate_mod
  n_climate_mod -- "mod<br/>mod_id -> name" --> n_climate_mod_folder
  n_climate_mod -- "waterfall_preview -> image" --> n_waterfall_preview_png
```
