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
        ("relaxed", f"{BASE}_relaxed_uc"),
        ("none", f"{BASE}_none_uc"),
    ],
)
def test_run_folder_suffix_names_the_unit_commitment(tmp_path, unit_commitment, folder):
    assert _run_output_dir(tmp_path, BASE, unit_commitment, "demo", resume=True) == (
        tmp_path / folder / "demo"
    )


def test_resumed_full_uc_run_keeps_its_unsuffixed_folder(tmp_path):
    legacy = tmp_path / BASE / "full_uc_profiled"
    legacy.mkdir(parents=True)

    assert _run_output_dir(tmp_path, BASE, "full", "full_uc_profiled", resume=True) == legacy
    # Without resume, or for a new run name, the _UC folder is used.
    assert _run_output_dir(tmp_path, BASE, "full", "full_uc_profiled", resume=False) == (
        tmp_path / f"{BASE}_UC" / "full_uc_profiled"
    )
    assert _run_output_dir(tmp_path, BASE, "full", "new_run", resume=True) == (
        tmp_path / f"{BASE}_UC" / "new_run"
    )


def test_existing_uc_folder_wins_over_legacy_folder(tmp_path):
    (tmp_path / BASE / "demo").mkdir(parents=True)
    current = tmp_path / f"{BASE}_UC" / "demo"
    current.mkdir(parents=True)

    assert _run_output_dir(tmp_path, BASE, "full", "demo", resume=True) == current
