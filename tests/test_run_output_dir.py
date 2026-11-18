from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_9ba_selection import _run_output_dir  # noqa: E402

BASE = "rolling_2025-04-01_2026-03-31"


@pytest.mark.parametrize(
    "unit_commitment, folder",
    [
        ("full", f"{BASE}_UC"),
        ("relaxed", f"{BASE}_relaxed"),
        ("none", f"{BASE}_LP"),
    ],
)
def test_run_folder_category_names_the_unit_commitment(tmp_path, unit_commitment, folder):
    assert _run_output_dir(tmp_path, BASE, unit_commitment, "demo") == tmp_path / folder / "demo"
