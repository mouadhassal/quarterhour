"""Overnight EV charging against Baltic and Finnish day-ahead prices, at hourly vs
quarter-hourly planning resolution, for single cars and for a fleet.

Strategies for each plugged-in night:
- plug-and-charge: full power from plug-in until the battery has what it needs;
- hourly plan: picks the cheapest *hours* (mean of their four quarter-hour prices);
- quarter-hour plan: picks the cheapest 15-minute slots.
All are paid at the real 15-minute price.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Europe/Tallinn")   # EE, LV, LT and FI share this offset all year
ZONES = ["ee", "fi", "lv", "lt"]


def load_prices(path=ROOT / "data" / "prices.csv"):
    raw = np.genfromtxt(path, delimiter=",", names=True)
    ts = raw["timestamp_utc"].astype(np.int64)
    return ts, {z: raw[z] for z in ZONES}


@dataclass(frozen=True)
class Fleet:
    n: int = 1000
    plug_mean_h: float = 18.5      # local hour of plug-in
    plug_sd_h: float = 1.2
    leave_mean_h: float = 7.25     # local hour of departure (next morning)
    leave_sd_h: float = 0.5
    plug_prob: float = 0.6         # share of nights a car is plugged in
    energy_kwh: tuple = (5.0, 18.0)
    power_kw: tuple = (7.4, 11.0)
    power_share: tuple = (0.6, 0.4)


def nights(ts: np.ndarray):
    """Index of the first quarter-hour at 12:00 local time on each day; a 'night' runs 12:00 -> 12:00."""
    from datetime import datetime
    local = [datetime.fromtimestamp(int(t), TZ) for t in ts]
    starts = [i for i, d in enumerate(local) if d.hour == 12 and d.minute == 0]
    return [s for s in starts if s + 96 <= len(ts)]


def sessions(f: Fleet, rng, n_nights: int):
    """Per car and night: plug-in slot, departure slot (from 12:00), energy need (kWh), power (kW)."""
    shape = (n_nights, f.n)
    plug = np.clip(np.round((rng.normal(f.plug_mean_h, f.plug_sd_h, shape) - 12) * 4), 16, 44).astype(int)
    leave = np.clip(np.round((rng.normal(f.leave_mean_h, f.leave_sd_h, shape) + 12) * 4), 70, 84).astype(int)
    power = np.where(rng.random(shape) < f.power_share[0], f.power_kw[0], f.power_kw[1])
    energy = np.minimum(rng.uniform(*f.energy_kwh, shape), power * (leave - plug) / 4)
    energy = np.where(rng.random(shape) < f.plug_prob, energy, 0.0)
    return plug, leave, energy, power


def schedule(rank_price: np.ndarray, plug: np.ndarray, leave: np.ndarray, energy: np.ndarray,
             power: np.ndarray, jitter: np.ndarray | None = None) -> np.ndarray:
    """kWh per quarter-hour (cars x 96): fill the cheapest available slots by `rank_price`.
    `rank_price` is (96,) for one night; ties broken by time. Optional per-car `jitter` (cars x 96)."""
    n = len(plug)
    slot = np.arange(96)[None, :]
    avail = (slot >= plug[:, None]) & (slot < leave[:, None])
    key = np.where(avail, rank_price[None, :] + (0 if jitter is None else jitter), np.inf)
    order = np.argsort(key, axis=1, kind="stable")
    per_slot = power[:, None] / 4
    k_full = np.floor(energy / per_slot[:, 0]).astype(int)
    rest = energy - k_full * per_slot[:, 0]
    kwh = np.zeros((n, 96))
    rank = np.empty_like(order)
    rank[np.arange(n)[:, None], order] = slot
    kwh[rank < k_full[:, None]] = np.broadcast_to(per_slot, (n, 96))[rank < k_full[:, None]]
    last = rank == k_full[:, None]
    kwh[last] = np.broadcast_to(rest[:, None], (n, 96))[last]
    return kwh


def dumb(plug, leave, energy, power) -> np.ndarray:
    """Charge at full power from plug-in."""
    return schedule(np.arange(96, dtype=float), plug, leave, energy, power)


def hourly(price: np.ndarray) -> np.ndarray:
    return np.repeat(price.reshape(24, 4).mean(1), 4)


def capped(price: np.ndarray, plug, leave, energy, power, cap_kw: float, order=None) -> np.ndarray:
    """Fleet-aware schedule: cars, one after another, take their cheapest slots that still have
    room under a per-quarter-hour fleet cap (kW). A car that cannot fit spills to the cheapest
    remaining slots of its window regardless of the cap (it must leave charged)."""
    n = len(plug)
    room = np.full(96, cap_kw / 4)          # kWh left per slot
    kwh = np.zeros((n, 96))
    order = np.arange(n) if order is None else order
    rank = np.argsort(price, kind="stable")
    for i in order:
        need = energy[i]
        if need <= 0:
            continue
        step = power[i] / 4
        for s_ in rank:
            if need <= 1e-9:
                break
            if not (plug[i] <= s_ < leave[i]):
                continue
            take = min(step, need, room[s_])
            if take > 0:
                kwh[i, s_] += take
                room[s_] -= take
                need -= take
        if need > 1e-9:                     # spill: ignore the cap
            for s_ in rank:
                if need <= 1e-9:
                    break
                if plug[i] <= s_ < leave[i]:
                    take = min(step - kwh[i, s_], need)
                    if take > 0:
                        kwh[i, s_] += take
                        need -= take
    return kwh
