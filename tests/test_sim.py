import numpy as np

from quarterhour.sim import Fleet, capped, dumb, hourly, schedule, sessions


def test_energy_delivered_and_cap_respected():
    rng = np.random.default_rng(0)
    f = Fleet(n=50)
    plug, leave, energy, power = sessions(f, rng, 1)
    price = rng.random(96)
    for kwh in (dumb(plug[0], leave[0], energy[0], power[0]),
                schedule(price, plug[0], leave[0], energy[0], power[0]),
                schedule(hourly(price), plug[0], leave[0], energy[0], power[0])):
        assert np.allclose(kwh.sum(1), energy[0])
        assert (kwh <= power[0][:, None] / 4 + 1e-9).all()
    c = capped(price, plug[0], leave[0], energy[0], power[0], cap_kw=60.0)
    assert np.allclose(c.sum(1), energy[0])


def test_quarter_plan_never_costs_more_than_hourly():
    rng = np.random.default_rng(1)
    f = Fleet(n=30)
    plug, leave, energy, power = sessions(f, rng, 1)
    for _ in range(20):
        p = rng.random(96)
        q = (schedule(p, plug[0], leave[0], energy[0], power[0]) * p).sum()
        h = (schedule(hourly(p), plug[0], leave[0], energy[0], power[0]) * p).sum()
        assert q <= h + 1e-9


def test_charging_only_while_plugged():
    rng = np.random.default_rng(2)
    f = Fleet(n=20)
    plug, leave, energy, power = sessions(f, rng, 1)
    kwh = schedule(rng.random(96), plug[0], leave[0], energy[0], power[0])
    slot = np.arange(96)[None, :]
    assert (kwh[(slot < plug[0][:, None]) | (slot >= leave[0][:, None])] == 0).all()
