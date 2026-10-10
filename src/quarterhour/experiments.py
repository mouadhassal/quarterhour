"""Every number in the README: python -m quarterhour.experiments  (about a minute)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .sim import ROOT, ZONES, Fleet, capped, dumb, hourly, load_prices, nights, schedule, sessions

OUT = ROOT / "results"


def per_car_year(ts, prices, f: Fleet, seed=0):
    """EUR per car per year for each zone and strategy (wholesale energy only)."""
    idx = nights(ts)
    rng = np.random.default_rng(seed)
    plug, leave, energy, power = sessions(f, rng, len(idx))
    out = {z: {"dumb": 0.0, "hourly": 0.0, "quarter": 0.0, "kwh": 0.0} for z in ZONES}
    for k, s in enumerate(idx):
        args = (plug[k], leave[k], energy[k], power[k])
        base = dumb(*args)
        for z in ZONES:
            p = prices[z][s:s + 96] / 1000                     # EUR/kWh
            h = schedule(hourly(p), *args)
            q = schedule(p, *args)
            out[z]["dumb"] += (base * p).sum()
            out[z]["hourly"] += (h * p).sum()
            out[z]["quarter"] += (q * p).sum()
            out[z]["kwh"] += base.sum()
    scale = 365 / len(idx) / f.n
    for z in ZONES:
        for k in out[z]:
            out[z][k] *= scale
        o = out[z]
        o["saving_hourly"] = o["dumb"] - o["hourly"]
        o["saving_quarter"] = o["dumb"] - o["quarter"]
        o["extra_from_quarter"] = o["hourly"] - o["quarter"]
        o["extra_share"] = o["extra_from_quarter"] / o["saving_quarter"] if o["saving_quarter"] else float("nan")
    return out


def herding(ts, prices, f: Fleet, zone="ee", jitters=(0.0, 0.5, 1.0, 2.0, 5.0), seed=1):
    """Fleet peak (kW) and cost when every car chases the same cheapest slots, and what a small
    per-car random offset (EUR/MWh) on the ranking price does to both."""
    idx = nights(ts)
    rng = np.random.default_rng(seed)
    plug, leave, energy, power = sessions(f, rng, len(idx))
    res = {"dumb": {"peak_kw": [], "cost": 0.0}}
    for j in jitters:
        res[f"jitter {j:g}"] = {"peak_kw": [], "cost": 0.0, "hourly_peak_kw": []}
    for k, s in enumerate(idx):
        p = prices[zone][s:s + 96] / 1000
        args = (plug[k], leave[k], energy[k], power[k])
        b = dumb(*args)
        res["dumb"]["peak_kw"].append(b.sum(0).max() * 4)
        res["dumb"]["cost"] += (b * p).sum()
        for j in jitters:
            jit = rng.uniform(-j, j, (f.n, 96)) / 1000 if j else None
            q = schedule(p, *args, jitter=jit)
            r = res[f"jitter {j:g}"]
            r["peak_kw"].append(q.sum(0).max() * 4)
            r["hourly_peak_kw"].append(q.sum(0).reshape(24, 4).mean(1).max() * 4)
            r["cost"] += (q * p).sum()
    scale = 365 / len(idx) / f.n
    summary = {}
    for name, r in res.items():
        summary[name] = {"mean_nightly_peak_kw": float(np.mean(r["peak_kw"])),
                         "p95_nightly_peak_kw": float(np.percentile(r["peak_kw"], 95)),
                         "eur_per_car_year": r["cost"] * scale}
        if "hourly_peak_kw" in r:
            summary[name]["mean_nightly_hourly_avg_peak_kw"] = float(np.mean(r["hourly_peak_kw"]))
    return summary


def fleet_cap(ts, prices, f: Fleet, zone="ee", caps_mw=(4.0, 3.0, 2.5), seed=1):
    """Fleet-aware scheduling: a hard cap on the fleet's load per quarter-hour."""
    idx = nights(ts)
    rng = np.random.default_rng(seed)
    plug, leave, energy, power = sessions(f, rng, len(idx))
    out = {}
    for cap in caps_mw:
        peaks, cost = [], 0.0
        for k, s in enumerate(idx):
            p = prices[zone][s:s + 96] / 1000
            q = capped(p, plug[k], leave[k], energy[k], power[k], cap * 1000, order=rng.permutation(f.n))
            peaks.append(q.sum(0).max() * 4)
            cost += (q * p).sum()
        out[f"cap {cap:g} MW"] = {"mean_nightly_peak_kw": float(np.mean(peaks)),
                                  "p95_nightly_peak_kw": float(np.percentile(peaks, 95)),
                                  "eur_per_car_year": cost * 365 / len(idx) / f.n}
        print(cap, out[f"cap {cap:g} MW"], flush=True)
    return out


def figure(single, herd):
    plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 200})
    fig, axes = plt.subplots(1, 2, figsize=(5.6, 2.2))
    x = np.arange(len(ZONES))
    axes[0].bar(x - 0.2, [single[z]["saving_hourly"] for z in ZONES], 0.4, color="#9ab", label="hourly plan")
    axes[0].bar(x + 0.2, [single[z]["saving_quarter"] for z in ZONES], 0.4, color="#1e8449", label="15-minute plan")
    axes[0].set_xticks(x, [z.upper() for z in ZONES])
    axes[0].set_ylabel("saving vs plug-and-charge\n(EUR per car per year)")
    axes[0].legend(frameon=False, fontsize=7, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    names = [n for n in herd if n.startswith("jitter")]
    caps = [n for n in herd if n.startswith("cap")]
    y = lambda n: herd[n]["p95_nightly_peak_kw"] / 1000
    axes[1].plot([herd[n]["eur_per_car_year"] for n in names], [y(n) for n in names], "o-", color="#2e86c1", ms=3,
                 label="random price jitter, ±0 to ±5 €/MWh")
    axes[1].plot([herd[n]["eur_per_car_year"] for n in caps], [y(n) for n in caps], "s-", color="#1e8449", ms=3,
                 label="fleet cap per quarter-hour")
    for n in caps:
        axes[1].annotate(n.replace("cap ", ""), (herd[n]["eur_per_car_year"], y(n)), fontsize=6.5,
                         xytext=(-4, 5), textcoords="offset points", color="#1e8449", ha="right")
    axes[1].axhline(y("dumb"), color="#c0392b", lw=0.8, ls="--")
    axes[1].text(92.2, y("dumb"), "plug-and-charge", fontsize=6.5, color="#c0392b", va="bottom")
    axes[1].set_xlabel("energy cost (EUR per car per year)")
    axes[1].set_ylabel("fleet peak on the worst 5%\nof nights, 1,000 cars (MW)")
    axes[1].legend(frameon=False, fontsize=6.5, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout()
    fig.savefig(OUT / "fig_quarterhour.png", bbox_inches="tight")
    plt.close(fig)


def main():
    OUT.mkdir(exist_ok=True)
    ts, prices = load_prices()
    f = Fleet()
    single = per_car_year(ts, prices, f)
    herd = herding(ts, prices, f)
    herd.update(fleet_cap(ts, prices, f))
    figure(single, herd)
    res = {"fleet": f.__dict__, "per_car_year": single, "herding_ee": herd,
           "price_window": [int(ts[0]), int(ts[-1])], "quarter_hours": int(len(ts))}
    (OUT / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
