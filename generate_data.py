"""Sinh dữ liệu bảo hành giả lập, có cài sẵn 4 bất thường để main.py đi tìm.

Chạy: uv run generate_data.py  ->  tạo data/warranty_claims.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA_PATH = Path(__file__).parent / "data" / "warranty_claims.csv"

DEALERS = [f"DL{i:02d}" for i in range(1, 21)]
PARTS = ["battery", "motor", "inverter", "brake", "infotainment", "charger"]
PART_PROBS = [0.15, 0.10, 0.10, 0.30, 0.20, 0.15]
SUPPLIERS = {
    "battery": ["SUP_A", "SUP_B"],
    "motor": ["SUP_C"],
    "inverter": ["SUP_D", "SUP_E"],
    "brake": ["SUP_F"],
    "infotainment": ["SUP_G"],
    "charger": ["SUP_H"],
}
BASE_COST = {"battery": 3000, "motor": 1500, "inverter": 1200, "brake": 200, "infotainment": 300, "charger": 400}


def generate(n: int = 20_000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # ---------- Dữ liệu "bình thường" ----------
    part = rng.choice(PARTS, size=n, p=PART_PROBS)
    supplier = [rng.choice(SUPPLIERS[p]) for p in part]
    date = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 365, n), unit="D")
    df = pd.DataFrame(
        {
            "claim_id": [f"C{i:06d}" for i in range(n)],
            "date": date,
            "dealer": rng.choice(DEALERS, size=n),
            "vin": [f"VIN{v:07d}" for v in rng.integers(0, 10_000_000, n)],
            "part": part,
            "supplier": supplier,
            "lot": [f"LOT{d.month:02d}" for d in date],
            "cost_usd": (pd.Series(part).map(BASE_COST) * rng.lognormal(0, 0.2, n)).round(2),
            "odometer_km": rng.integers(0, 60_000, n),
        }
    )

    # ---------- Bất thường 1: inverter SUP_E lô tháng 6-7 lỗi -> claim tăng vọt ----------
    extra = df[df.part == "inverter"].sample(600, random_state=1).copy()
    extra["supplier"] = "SUP_E"
    extra["date"] = pd.Timestamp("2025-06-01") + pd.to_timedelta(rng.integers(0, 60, 600), unit="D")
    extra["lot"] = extra["date"].dt.month.map(lambda m: f"LOT{m:02d}")
    extra["claim_id"] = [f"X{i:06d}" for i in range(600)]
    df = pd.concat([df, extra], ignore_index=True)

    # ---------- Bất thường 2: DL07 khai khống chi phí sửa phanh ----------
    mask = (df.dealer == "DL07") & (df.part == "brake")
    df.loc[mask, "cost_usd"] = (df.loc[mask, "cost_usd"] * 2.5).round(2)

    # ---------- Bất thường 3: DL13 gửi trùng claim ----------
    dup = df[df.dealer == "DL13"].sample(80, random_state=2).copy()
    dup["claim_id"] = dup["claim_id"] + "_D"
    df = pd.concat([df, dup], ignore_index=True)

    # ---------- Bất thường 4: odometer nhập sai ----------
    df.loc[df.sample(30, random_state=3).index, "odometer_km"] = -1

    return df.sample(frac=1, random_state=0).reset_index(drop=True)


if __name__ == "__main__":
    DATA_PATH.parent.mkdir(exist_ok=True)
    df = generate()
    df.to_csv(DATA_PATH, index=False)
    print(f"Đã tạo {len(df):,} claim -> {DATA_PATH}")
