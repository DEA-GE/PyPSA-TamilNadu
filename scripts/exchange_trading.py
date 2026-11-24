"""Price-responsive power-exchange trading of Tamil Nadu.

The interstate schedule built into the network is fixed: loads at the
external gateway buses deliver Tamil Nadu's metered net import every hour,
and that import includes the state's power-exchange trades (day-ahead, green
day-ahead, high-price day-ahead and real-time). With this module enabled the
exchange part becomes a decision of the model, taken against the exchange
price, while the contracted part (long-term, GNA, T-GNA, REMC) stays fixed:

- an ``IEX`` market bus is connected without losses or charges to every
  gateway bus that carries interstate loads, so traded power reaches Tamil
  Nadu over the existing interstate links, with their capacity, 3% loss and
  transmission charge;
- a fixed load at the market bus equal to the observed net exchange import
  takes the observed trades out of the fixed schedule (with no trading the
  state's net import falls by exactly the observed exchange trades);
- import steps (generators at the market bus) sell to Tamil Nadu at the
  hourly exchange price plus a step adder, and export steps (generators with
  negative output) buy from it at the price minus the adder. With increasing
  adders the steps form a convex merit-order curve, so the problem stays a
  linear programme (plus the coal commitment binaries in full unit
  commitment).

Two trade limits are available (``limit``): ``headroom`` leaves the volume to
the steps and to the interconnector capacity not used by the fixed flows;
``observed`` caps each hour's imports and exports at the observed exchange
imports and exports, filled step by step.

The network must use the metered interchange basis (the default build): the
transaction-only basis does not contain the exchange trades.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parents[1]
MARKET_BUS = "IEX"
REVERSAL_LOAD = "interstate_exchange_observed_trades"
IMPORT_CARRIER = "exchange_import"
EXPORT_CARRIER = "exchange_export"
LINK_CARRIER = "exchange_access"
LIMIT_CHOICES = ("headroom", "observed")
SECTION_KEYS = {"enabled", "limit", "price", "trades", "import_steps", "export_steps"}


class ExchangeTradingError(ValueError):
    """Invalid exchange-trading settings or inputs."""


@dataclass
class ExchangeTradingResult:
    gateways: pd.DataFrame
    steps: pd.DataFrame
    notes: list[str]


def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def _validate_steps(name: str, steps: Any) -> list[tuple[float, float]]:
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ExchangeTradingError(f"exchange_trading.{name} must be a non-empty list of [MW, INR/MWh] pairs")
    parsed = []
    for step in steps:
        if not isinstance(step, (list, tuple)) or len(step) != 2:
            raise ExchangeTradingError(f"exchange_trading.{name}: each step is [MW, INR/MWh adder], got {step!r}")
        size, adder = (float(value) for value in step)
        if not size > 0:
            raise ExchangeTradingError(f"exchange_trading.{name}: step sizes must be positive")
        if adder < 0:
            raise ExchangeTradingError(f"exchange_trading.{name}: adders must be non-negative")
        parsed.append((size, adder))
    adders = [adder for _, adder in parsed]
    if any(later < earlier for earlier, later in zip(adders, adders[1:])):
        raise ExchangeTradingError(
            f"exchange_trading.{name}: adders must not decrease (a convex merit order keeps the problem linear)"
        )
    return parsed


def validate_section(section: Mapping[str, Any]) -> dict[str, Any]:
    """Check and normalise the ``exchange_trading`` config section."""
    unknown = set(section) - SECTION_KEYS
    if unknown:
        raise ExchangeTradingError(f"exchange_trading: unknown keys {sorted(unknown)}")
    settings = dict(section)
    if not isinstance(settings.get("enabled"), bool):
        raise ExchangeTradingError("exchange_trading.enabled must be true or false")
    limit = str(settings.get("limit")).strip().lower()
    if limit not in LIMIT_CHOICES:
        raise ExchangeTradingError(f"exchange_trading.limit must be one of {list(LIMIT_CHOICES)}, got {settings.get('limit')!r}")
    settings["limit"] = limit
    for key in ("price", "trades"):
        if not settings.get(key):
            raise ExchangeTradingError(f"exchange_trading.{key} must name a CSV file")
        settings[key] = str(settings[key])
    settings["import_steps"] = _validate_steps("import_steps", settings.get("import_steps"))
    settings["export_steps"] = _validate_steps("export_steps", settings.get("export_steps"))
    return settings


def read_price(path: str | Path, snapshots: pd.DatetimeIndex) -> pd.Series:
    """Hourly exchange price (INR/MWh) on the network snapshots."""
    frame = pd.read_csv(_resolve(path), parse_dates=["timestamp"], index_col="timestamp")
    if "price_inr_per_mwh" not in frame:
        raise ExchangeTradingError(f"{path}: no price_inr_per_mwh column")
    price = frame["price_inr_per_mwh"].reindex(snapshots)
    if price.isna().any():
        raise ExchangeTradingError(f"{path}: no price for {int(price.isna().sum())} snapshot(s)")
    return price.astype(float)


def read_trades(path: str | Path, snapshots: pd.DatetimeIndex) -> pd.DataFrame:
    """Observed hourly exchange imports and exports (MW) on the network snapshots."""
    frame = pd.read_csv(_resolve(path), parse_dates=["snapshot"], index_col="snapshot")
    columns = ["exchange_import_mw", "exchange_export_mw", "exchange_net_import_mw"]
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ExchangeTradingError(f"{path}: missing columns {sorted(missing)}")
    trades = frame[columns].reindex(snapshots)
    if trades.isna().any().any():
        raise ExchangeTradingError(f"{path}: no trades for {int(trades.isna().any(axis=1).sum())} snapshot(s)")
    return trades.astype(float)


def gateway_buses(network: pypsa.Network) -> list[str]:
    """External buses that carry the fixed interstate loads."""
    loads = network.loads
    interstate = loads.index[loads.index.str.startswith("interstate_") & (loads.index != REVERSAL_LOAD)]
    if not interstate.str.startswith("interstate_metered_correction_").any():
        raise ExchangeTradingError(
            "exchange_trading needs the metered interchange basis (interstate_metered_correction_* loads); "
            "the transaction-only schedule does not contain the exchange trades"
        )
    return sorted(loads.loc[interstate, "bus"].unique())


def step_availability(cap: pd.Series, sizes: list[float]) -> tuple[pd.DataFrame, list[float]]:
    """Per-unit availability of each step when the hourly volume is capped at ``cap``.

    Steps fill in order; the last step is enlarged if needed to hold the
    largest hourly cap.
    """
    sizes = list(sizes)
    before = float(sum(sizes[:-1]))
    sizes[-1] = max(sizes[-1], float(cap.max()) - before)
    columns = {}
    start = 0.0
    for k, size in enumerate(sizes):
        columns[k] = ((cap - start).clip(lower=0.0, upper=size) / size).astype(float)
        start += size
    return pd.DataFrame(columns, index=cap.index), sizes


def apply_exchange_trading(
    network: pypsa.Network, settings: Mapping[str, Any] | None
) -> ExchangeTradingResult | None:
    """Add the market bus, trading steps and observed-trade reversal in place."""
    if not settings or not settings.get("enabled"):
        return None
    if MARKET_BUS in network.buses.index:
        raise ExchangeTradingError(f"The network already has a {MARKET_BUS} bus")
    snapshots = pd.DatetimeIndex(network.snapshots)
    price = read_price(settings["price"], snapshots)
    trades = read_trades(settings["trades"], snapshots)
    gateways = gateway_buses(network)

    links = network.links
    capacity = {
        bus: float((links.loc[links.bus0.eq(bus), "p_nom"] * links.loc[links.bus0.eq(bus), "p_max_pu"]).sum())
        for bus in gateways
    }
    if not all(value > 0 for value in capacity.values()):
        raise ExchangeTradingError(f"Gateway without interstate link capacity: {capacity}")

    for carrier, nice in ((IMPORT_CARRIER, "exchange import"), (EXPORT_CARRIER, "exchange export"),
                          (LINK_CARRIER, "exchange access")):
        if carrier not in network.carriers.index:
            network.add("Carrier", carrier, nice_name=nice)
    xy = network.buses.loc[gateways, ["x", "y"]].mean()
    extra = {"kind": "power exchange"} if "kind" in network.buses else {}
    network.add("Bus", MARKET_BUS, carrier="AC", x=float(xy.x), y=float(xy.y), **extra)
    for bus in gateways:
        # Lossless and free: a cost on a bidirectional link would become a
        # revenue for reverse flow. Physical limits stay on the interstate links.
        network.add("Link", f"{MARKET_BUS} - {bus}", bus0=MARKET_BUS, bus1=bus, carrier=LINK_CARRIER,
                    p_nom=capacity[bus], p_min_pu=-1.0, p_max_pu=1.0, efficiency=1.0, marginal_cost=0.0)
    network.add("Load", REVERSAL_LOAD, bus=MARKET_BUS, carrier="electricity",
                p_set=trades["exchange_net_import_mw"])

    observed = settings["limit"] == "observed"
    rows = []
    for direction, steps, carrier, cap in (
        ("import", settings["import_steps"], IMPORT_CARRIER, trades["exchange_import_mw"]),
        ("export", settings["export_steps"], EXPORT_CARRIER, trades["exchange_export_mw"]),
    ):
        sizes = [size for size, _ in steps]
        availability = None
        if observed:
            availability, sizes = step_availability(cap, sizes)
        sign = 1.0 if direction == "import" else -1.0
        for k, ((_, adder), size) in enumerate(zip(steps, sizes)):
            name = f"{carrier}_{k + 1}"
            pu = availability[k] if availability is not None else pd.Series(1.0, index=snapshots)
            kwargs = dict(p_max_pu=pu, p_min_pu=0.0) if direction == "import" else dict(p_max_pu=0.0, p_min_pu=-pu)
            network.add("Generator", name, bus=MARKET_BUS, carrier=carrier, p_nom=size, committable=False,
                        marginal_cost=price + sign * adder, **kwargs)
            rows.append({"generator": name, "direction": direction, "step": k + 1, "size_mw": size,
                         "adder_inr_per_mwh": adder})
    gateway_table = pd.DataFrame({"bus": gateways, "interstate_capacity_mw": [capacity[b] for b in gateways]})
    notes = [
        f"{settings['limit']} limit;",
        f"observed exchange trades taken out of the fixed schedule: {trades.exchange_import_mw.sum() / 1e6:.2f} TWh "
        f"imports, {trades.exchange_export_mw.sum() / 1e6:.2f} TWh exports over the network's snapshots.",
    ]
    return ExchangeTradingResult(gateways=gateway_table, steps=pd.DataFrame(rows), notes=notes)
