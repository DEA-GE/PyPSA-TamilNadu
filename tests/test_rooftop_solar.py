from pathlib import Path
import sys

import pandas as pd
import pypsa
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rooftop_solar import apply_rooftop_solar, rooftop_generators, validate_section  # noqa: E402
from run_config import ConfigError, load_run_config, run_selection_kwargs  # noqa: E402

ROOFTOP = "tamil_nadu_n_a_rooftop_grid_connected_1635_8__aggregate_01__trichy"


def _network() -> pypsa.Network:
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2025-04-01", periods=3, freq="h"))
    network.add("Bus", "Trichy")
    network.add("Generator", ROOFTOP, bus="Trichy", carrier="solar", p_nom=226.0,
                p_max_pu=[0.0, 0.3, 0.6])
    network.add("Generator", "ariyalur_solar_park", bus="Trichy", carrier="solar", p_nom=100.0,
                p_max_pu=[0.0, 0.4, 0.7])
    network.add("Generator", "rooftop_named_wind", bus="Trichy", carrier="wind", p_nom=50.0)
    return network


def test_included_by_default_and_network_unchanged():
    network = _network()
    removed = apply_rooftop_solar(network, None)
    assert removed.empty
    assert ROOFTOP in network.generators.index
    assert apply_rooftop_solar(network, {"include": True}).empty


def test_excluded_removes_only_rooftop_solar_and_its_profile():
    network = _network()
    removed = apply_rooftop_solar(network, {"include": False})
    assert removed.to_dict() == {ROOFTOP: 226.0}
    assert ROOFTOP not in network.generators.index
    assert ROOFTOP not in network.generators_t.p_max_pu.columns
    # Utility solar and other carriers stay, whatever their names.
    assert {"ariyalur_solar_park", "rooftop_named_wind"} <= set(network.generators.index)


def test_excluding_without_rooftop_generators_fails():
    network = _network()
    network.remove("Generator", [ROOFTOP])
    with pytest.raises(ValueError, match="no solar generator"):
        apply_rooftop_solar(network, {"include": False})


@pytest.mark.parametrize("section, message", [
    ({"include": "no"}, "must be true or false"),
    ({"include": False, "share": 0.5}, "unknown key"),
])
def test_invalid_settings_are_rejected(section, message):
    with pytest.raises(ValueError, match=message):
        validate_section(section)


def test_config_default_and_override():
    assert run_selection_kwargs(load_run_config(None, ["run.name=demo"]))["rooftop_solar"] == {"include": True}
    config = load_run_config(None, ["run.name=demo", "rooftop_solar.include=false"])
    assert run_selection_kwargs(config)["rooftop_solar"] == {"include": False}
    with pytest.raises(ConfigError, match="rooftop_solar.include"):
        load_run_config(None, ["run.name=demo", "rooftop_solar.include=maybe"])


def test_built_model_has_the_rooftop_record_in_every_area():
    generators = pd.read_csv(ROOT / "model" / "generators.csv", index_col="name")
    network = pypsa.Network()
    network.add("Bus", generators.bus.unique())
    network.add("Generator", generators.index, bus=generators.bus, carrier=generators.carrier,
                p_nom=generators.p_nom)
    units = rooftop_generators(network)
    assert len(units) == 9
    assert generators.loc[units, "bus"].nunique() == 9
    assert generators.loc[units, "p_nom"].sum() == pytest.approx(1635.8)
