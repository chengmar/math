from __future__ import annotations

import sys
from pathlib import Path

trainer = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(trainer / "src"))

from cumcm_lab.cli import main

raise SystemExit(main())
