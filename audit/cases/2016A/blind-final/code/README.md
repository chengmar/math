# Code

`solve.py` is the deterministic Python 3.8+ standard-library solver. Run from
the revision workspace:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
python code/solve.py
```

The solver:

1. computes Q1 and the ball-volume identifiability scenarios;
2. expresses Q2 in submerged effective mass and maps to conditional solid-steel dry mass;
3. includes ball current drag at the ball node without passing it twice into the drum;
4. enumerates every integer link count for all five chain types in the expanded 12–40 m domain;
5. retains the full 25 kg feasible mass grid for every link and performs per-link continuous refinement;
6. builds the complete candidate and Pareto files, then applies the frozen fixed-scale score;
7. generates the environment, geometry/pressure/depth, legacy-design and sensitivity checks;
8. generates all CSV/JSON/TeX tables and SVG/PDF figures, including `results/README.md`;
9. writes a schema-2 manifest with separate `source_inputs` and `generated_outputs`.

The run is intentionally exhaustive and may take several minutes. It uses no
randomness.

Run the independent formula and ball-node test:

```powershell
python code/test_physics.py
```

Run paper/result semantic consistency after the paper files exist:

```powershell
python code/check_consistency.py
```

Model-internal `pass` does not establish real-sea validity. Ball geometry
outside the declared range, chain drag, waves, dynamics and anchor-soil
capacity remain `needs_review`.
