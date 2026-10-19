"""Linopy compatibility settings for the PyPSA 1.2.x runners."""

from __future__ import annotations

import warnings

import linopy
from linopy.config import LinopySemanticsWarning


def configure_for_pypsa_124() -> None:
    """Keep PyPSA 1.2.4's required legacy missing-value behavior."""
    # PyPSA 1.2.4 builds first-snapshot ramping and energy-balance terms with
    # intentionally absent slots.  Under v1 those slots can drop constraints.
    linopy.options["semantics"] = "legacy"
    warnings.filterwarnings(
        "ignore",
        category=LinopySemanticsWarning,
        module=r"pypsa\.optimization\.constraints",
    )

