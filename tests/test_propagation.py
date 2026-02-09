import math

from app.services.propagation import MU_EARTH_KM3_S2, norm, propagate_two_body


def test_two_body_propagation_conserves_circular_orbit_over_one_period():
    r_km = 7000.0
    v_km_s = math.sqrt(MU_EARTH_KM3_S2 / r_km)
    state = [r_km, 0.0, 0.0, 0.0, v_km_s, 0.0]
    period_s = 2.0 * math.pi * math.sqrt((r_km**3) / MU_EARTH_KM3_S2)

    out = propagate_two_body(state, period_s)

    # Universal variables should return extremely close for the ideal circular case.
    assert abs(out[0] - state[0]) < 1e-6
    assert abs(out[1] - state[1]) < 1e-6
    assert abs(out[2] - state[2]) < 1e-6
    assert abs(norm(out[:3]) - r_km) < 1e-6
