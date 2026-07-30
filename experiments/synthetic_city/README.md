# GeoPilot synthetic-city experiment data

This generator creates deterministic ArcMap 10.2-compatible Shapefiles for four continuous business cases: flood response, facility siting, land-compliance inspection, and road-safety governance.

Run from the repository root:

```powershell
C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe experiments\synthetic_city\generate_dataset.py
```

The default output is `out/synthetic-city-v1/`. Generation fails when the target already exists so an earlier experiment dataset cannot be overwritten silently.

Output contents:

- `source/`: immutable input layers loaded before each experiment mode.
- `truth/`: reference result layers and `expected_ids.json` for automated scoring.
- `task_cases.json`: four cases with three dependent rounds per case.
- `load_order.json`: absolute ArcMap loading order.
- `data_dictionary.csv`: source fields and types.
- `validation.json`: geometry, CRS, field, and count checks.
- `manifest.json`: seed, counts, byte sizes, and SHA-256 checksums.

All source and truth layers use EPSG:32650, so distances and areas are evaluated in metres. Source layers are never edited during experiments; every G0-G3 run writes to a separate output folder and starts from the same source state.

## Formal ablation runner

`run_formal_experiments.py` is the sole formal-experiment entry point. It controls ArcMap only through the GeoPilot Gateway: before every mode/case/repetition, it clears the map, reloads exactly the 14 immutable source layers, and verifies the fresh Bridge context. It then executes the three dependent rounds, scores every generated vector against the truth IDs, checks required CSV/PNG artifacts, and continuously writes run records plus CSV summaries.

```powershell
python experiments\synthetic_city\run_formal_experiments.py `
  --output out\formal-experiments\run-001 `
  --repetitions 3
```

The output directory must not already exist. A failure stops the batch immediately and leaves `run_records.json` and the partial score tables for diagnosis; it never silently reuses a polluted ArcMap state or overwrites an earlier result set.
