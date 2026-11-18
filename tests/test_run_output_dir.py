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
    assert _run_output_dir(tmp_path, BASE, unit_commitment, "demo", resume=True) == (
        tmp_path / folder / "demo"
    )


@pytest.mark.parametrize(
    "unit_commitment, legacy_folder, new_folder",
    [
        ("full", BASE, f"{BASE}_UC"),
        ("relaxed", f"{BASE}_relaxed_uc", f"{BASE}_relaxed"),
        ("none", f"{BASE}_none_uc", f"{BASE}_LP"),
    ],
)
def test_resumed_run_keeps_its_old_folder(tmp_path, unit_commitment, legacy_folder, new_folder):
    legacy = tmp_path / legacy_folder / "old_run"
    legacy.mkdir(parents=True)

    assert _run_output_dir(tmp_path, BASE, unit_commitment, "old_run", resume=True) == legacy
    # Without resume, or for a new run name, the new category folder is used.
    assert _run_output_dir(tmp_path, BASE, unit_commitment, "old_run", resume=False) == (
        tmp_path / new_folder / "old_run"
    )
    assert _run_output_dir(tmp_path, BASE, unit_commitment, "new_run", resume=True) == (
        tmp_path / new_folder / "new_run"
    )


def test_existing_new_folder_wins_over_old_folder(tmp_path):
    (tmp_path / f"{BASE}_relaxed_uc" / "demo").mkdir(parents=True)
    current = tmp_path / f"{BASE}_relaxed" / "demo"
    current.mkdir(parents=True)

    assert _run_output_dir(tmp_path, BASE, "relaxed", "demo", resume=True) == current
