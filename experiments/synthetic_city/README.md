# GeoPilot synthetic-city experiment data

This generator creates deterministic ArcMap 10.2-compatible Shapefiles for four continuous business cases: flood response, facility siting, land-compliance inspection, and road-safety governance.

Run from the repository root:

```powershell
C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe experiments\synthetic_city\generate_dataset.py
```

The default output is `experiments/data/synthetic-city-v1/`. Generation fails when the target already exists so an earlier experiment dataset cannot be overwritten silently.

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

`experiments.supervisor` is the sole formal-experiment entry point. It controls ArcMap only through GeoPilotKernel, freezes paired G2/G3 provenance, and refuses any provider/model other than MiniMax-M3.

```powershell
python -m experiments.supervisor `
  --provider minimax --model MiniMax-M3 `
  --dataset experiments\data\synthetic-city-formal-20260910 `
  --output experiments\out\c3-gate-kernel-v1-minimax-<timestamp> `
  --seed 20260910 --repetition 1 `
  --case FLOOD_RESPONSE --case LAND_COMPLIANCE
```

The output directory must not already exist. The supervisor writes atomic campaign state and retains infrastructure or capability failures as evidence; it never reuses an earlier campaign or writes partial results into the paper's formal output.
