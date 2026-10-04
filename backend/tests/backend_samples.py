"""Rebuild committed sample workbooks before the suite so tests and files stay aligned."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))


def rebuild() -> None:
    from build_samples import main

    main()
