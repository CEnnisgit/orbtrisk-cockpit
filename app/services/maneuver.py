from datetime import datetime, timedelta
from typing import List

from app.models import ConjunctionEvent


def generate_options(event: ConjunctionEvent, risk_score: float) -> List[dict]:
    base_time = event.tca
    windows = [
        (base_time - timedelta(hours=2), base_time - timedelta(hours=1)),
        (base_time - timedelta(hours=1), base_time),
        (base_time, base_time + timedelta(hours=1)),
    ]
    options = []
    for idx, window in enumerate(windows):
        delta_v = 0.05 + idx * 0.02
        risk_after = max(0.0, risk_score - (0.15 + idx * 0.05))
        fuel_cost = delta_v * 10
        options.append(
            {
                "delta_v": delta_v,
                "time_window_start": window[0],
                "time_window_end": window[1],
                "risk_after": risk_after,
                "fuel_cost": fuel_cost,
                "is_recommended": idx == 0,
            }
        )
    return options
