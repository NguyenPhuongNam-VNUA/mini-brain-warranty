"""Mini Brain - Level 1 & 2: tự động tìm bất thường trong dữ liệu bảo hành.

Luồng xử lý:
    1. Data quality   : luật cứng (claim trùng, odometer phi lý)
    2. Cost anomaly   : đại lý nào khai chi phí cao bất thường? (robust z-score)
    3. Volume spike   : linh kiện/nhà cung cấp nào bị tăng claim đột biến theo tháng?
    4. Root cause     : drill-down xem phần tăng đến từ lot / dealer nào
    5. ML (Level 2)   : Isolation Forest trên dữ liệu tổng hợp, so với kết quả thống kê

Chạy: uv run main.py
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # chỉ lưu file ảnh, không mở cửa sổ
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyod.models.iforest import IForest

from generate_data import DATA_PATH, generate

OUT_DIR = Path(__file__).parent / "outputs"
Z_THRESHOLD = 3.5  # ngưỡng phổ biến cho robust z-score (Iglewicz & Hoaglin)


# =============================================================================
# Công cụ thống kê
# =============================================================================
def robust_zscore(x: pd.Series) -> pd.Series:
    """z-score dùng median & MAD thay cho mean & std.

    Mean/std bị chính điểm bất thường kéo lệch (outlier làm std phình to -> outlier
    trông "bớt lạ"). Median/MAD gần như không bị ảnh hưởng nên bắt outlier tốt hơn.
    """
    median = x.median()
    mad = (x - median).abs().median()
    if mad == 0:
        return pd.Series(0.0, index=x.index)
    return 0.6745 * (x - median) / mad  # 0.6745 để quy đổi về thang của z-score thường


def finding(kind: str, scope: dict, metric: str, expected, actual, evidence: str) -> dict:
    """Mọi bất thường được trả về cùng 1 format -> sau này LLM (Level 3) đọc dễ."""
    return {
        "type": kind,
        "scope": scope,
        "metric": metric,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
    }


# =============================================================================
# Bước 1: Data quality - luật cứng
# =============================================================================
def check_data_quality(df: pd.DataFrame) -> list[dict]:
    findings = []

    # Claim trùng: cùng xe, cùng linh kiện, cùng ngày, cùng đại lý, cùng số tiền
    # nhưng khác claim_id -> nghi gửi 2 lần để nhận tiền 2 lần.
    key = ["vin", "part", "date", "dealer", "cost_usd"]
    
    # để tìm các dòng có cùng: chiếc xe (vin), linh kiện thay thế (part), ngày sửa (date), đại lý tiếp nhận (dealer), và chi phí (cost_usd).
    # keep="first" giữ lại bản ghi đầu tiên và đánh dấu các bản ghi xuất hiện sau đó là trùng lặp.
    dups = df[df.duplicated(key, keep="first")]
    
    # Trong Pd khi khi bạn lặp qua một đối tượng DataFrameGroupBy,
    # mỗi vòng lặp sẽ trả về một tuple gồm 2 phần tử: 
    # - dealer: Giá trị dùng để gom nhóm
    # - g: Toàn bộ dữ liệu của nhóm đó dưới dạng DataFrame.
    
    for dealer, g in dups.groupby("dealer"):
        findings.append(
            finding(
                "duplicate_claim",
                {"dealer": dealer},
                "n_duplicate_claims",
                0,
                len(g),
                f"{len(g)} claim trùng, tổng {g.cost_usd.sum():,.0f} USD bị trả thừa. "
                f"Ví dụ: {', '.join(g.claim_id.head(3))}",
            )
        )

    # Odometer âm là không thể xảy ra -> lỗi nhập liệu
    bad_odo = df[df.odometer_km < 0]
    if len(bad_odo):
        findings.append(
            finding(
                "invalid_value",
                {"column": "odometer_km"},
                "n_rows_odometer_negative",
                0,
                len(bad_odo),
                f"{len(bad_odo)} dòng có odometer < 0. Ví dụ: {', '.join(bad_odo.claim_id.head(3))}",
            )
        )
    return findings


# =============================================================================
# Bước 2: Chi phí bất thường theo đại lý
# =============================================================================
def check_cost_by_dealer(df: pd.DataFrame) -> list[dict]:
    """Với mỗi linh kiện, so chi phí trung bình của từng đại lý với các đại lý khác.

    Phải so trong cùng 1 linh kiện: pin 3000$ là bình thường, phanh 3000$ thì không.
    """
    findings = []

    # =Gom nhóm tính chi phí trung bình của từng đại lý theo từng linh kiện:
    # Gom nhóm theo cặp ["part", "dealer"]
    # Với mỗi nhóm, tính:
    # - mean_cost: Chi phí trung bình khi sửa linh kiện đó tại đại lý đó.
    # - n: Số lượng claim (số lượt sửa) mà đại lý đã thực hiện.
    stats = df.groupby(["part", "dealer"]).agg(mean_cost=("cost_usd", "mean"), n=("claim_id", "count")).reset_index()
    
    stats["z"] = stats.groupby("part")["mean_cost"].transform(robust_zscore)

    for _, r in stats[stats.z > Z_THRESHOLD].iterrows():
        
        # các đại lý khác cùng linh kiện (peers) và tính median làm "mức kỳ vọng"
        peers = stats[(stats.part == r.part) & (stats.dealer != r.dealer)]
        expected = peers.mean_cost.median()
        findings.append(
            finding(
                "cost_anomaly",
                {"dealer": r.dealer, "part": r.part},
                "mean_cost_usd",
                round(expected, 1),
                round(r.mean_cost, 1),
                f"Chi phí TB sửa {r.part} ở {r.dealer} = {r.mean_cost:,.0f}$, "
                f"gấp {r.mean_cost / expected:.1f} lần median các đại lý khác "
                f"({expected:,.0f}$), dựa trên {r.n} claim. z={r.z:.1f}",
            )
        )
    return findings


# =============================================================================
# Bước 3 + 4: Số claim tăng đột biến theo tháng + drill-down nguyên nhân
# =============================================================================
# Tổng hợp số claim theo từng tháng
def monthly_counts(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.assign(month=df.date.dt.to_period("M"))
        .groupby(["part", "supplier", "month"])
        .size()
        .rename("n_claims")
        .reset_index()
    )


def check_volume_spike(df: pd.DataFrame) -> list[dict]:
    """So số claim mỗi tháng của (part, supplier) với chính lịch sử của nó."""
    findings = []
    monthly = monthly_counts(df)
    monthly["z"] = monthly.groupby(["part", "supplier"])["n_claims"].transform(robust_zscore)

    for _, r in monthly[monthly.z > Z_THRESHOLD].iterrows():
        history = monthly[(monthly.part == r.part) & (monthly.supplier == r.supplier)]
        expected = history.n_claims.median()
        root_cause = drill_down(df, r.part, r.supplier, r.month)
        findings.append(
            finding(
                "volume_spike",
                {"part": r.part, "supplier": r.supplier, "month": str(r.month)},
                "n_claims",
                int(expected),
                int(r.n_claims),
                f"{r.part}/{r.supplier} tháng {r.month}: {r.n_claims} claim, "
                f"gấp {r.n_claims / expected:.1f} lần mức thường ({expected:.0f}). z={r.z:.1f}. "
                f"Nguyên nhân: {root_cause}",
            )
        )
    return findings


def drill_down(df: pd.DataFrame, part: str, supplier: str, month: pd.Period, top_share: float = 0.5) -> str:
    """Phần claim tăng thêm tập trung ở đâu? (contribution analysis đơn giản)

    Với mỗi chiều (lot, dealer): tính số claim "dư" của từng giá trị so với mức
    thường, rồi xem giá trị lớn nhất chiếm bao nhiêu % tổng phần dư.
    - Chiếm phần lớn -> nguyên nhân tập trung ở đó (vd: 1 lô sản xuất lỗi).
    - Rải đều       -> không phải do chiều đó (vd: không phải lỗi của 1 đại lý).
    """
    sub = df[(df.part == part) & (df.supplier == supplier)].assign(month=lambda d: d.date.dt.to_period("M"))
    cur = sub[sub.month == month]
    n_months = sub.month.nunique()

    notes = []
    for dim in ["lot", "dealer"]:
        actual = cur[dim].value_counts()
        baseline = sub[sub.month != month][dim].value_counts() / (n_months - 1)
        excess = (actual - baseline.reindex(actual.index).fillna(0)).clip(lower=0)
        if excess.sum() == 0:
            continue
        top_val, share = excess.idxmax(), excess.max() / excess.sum()
        if share >= top_share:
            notes.append(f"tập trung ở {dim}={top_val} ({share:.0%} phần tăng)")
        else:
            notes.append(f"rải đều theo {dim} (lớn nhất {top_val} chỉ {share:.0%})")
    return "; ".join(notes)


# =============================================================================
# Bước 5 (Level 2): Isolation Forest
# =============================================================================
def check_isolation_forest(df: pd.DataFrame, top_k: int = 10) -> pd.DataFrame:
    """ML không giám sát: không cần định nghĩa luật, mô hình tự tìm điểm "khác đám đông".

    Mỗi điểm dữ liệu = 1 (dealer, part, tháng). Feature được chuẩn hoá theo linh kiện
    để pin và phanh so sánh được với nhau.
    """
    agg = (
        df.assign(month=df.date.dt.to_period("M"))
        .groupby(["dealer", "part", "month"])
        .agg(n_claims=("claim_id", "size"), mean_cost=("cost_usd", "mean"))
        .reset_index()
    )
    agg["cost_ratio"] = agg.mean_cost / agg.groupby("part").mean_cost.transform("median")
    agg["count_ratio"] = agg.n_claims / agg.groupby("part").n_claims.transform("median")

    model = IForest(contamination=0.02, random_state=0)
    model.fit(agg[["cost_ratio", "count_ratio"]].to_numpy())
    agg["anomaly_score"] = model.decision_scores_
    agg["is_anomaly"] = model.labels_ == 1
    return agg.sort_values("anomaly_score", ascending=False).head(top_k)


# =============================================================================
# Biểu đồ
# =============================================================================
def plot_monthly(df: pd.DataFrame, path: Path) -> None:
    monthly = monthly_counts(df)
    monthly["series"] = monthly.part + "/" + monthly.supplier
    pivot = monthly.pivot(index="month", columns="series", values="n_claims")

    fig, ax = plt.subplots(figsize=(11, 5))
    for col in pivot.columns:
        highlight = col == "inverter/SUP_E"
        ax.plot(pivot.index.astype(str), pivot[col], lw=2.5 if highlight else 1,
                alpha=1 if highlight else 0.4, label=col)
    ax.set_title("Số claim bảo hành theo tháng (part/supplier)")
    ax.set_ylabel("Số claim")
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# =============================================================================
def main():

    # === Nạp data ===
    if not DATA_PATH.exists():
        DATA_PATH.parent.mkdir(exist_ok=True)
        generate().to_csv(DATA_PATH, index=False)
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    OUT_DIR.mkdir(exist_ok=True)
    print(f"Đã nạp {len(df):,} claim, {df.dealer.nunique()} đại lý, {df.part.nunique()} linh kiện\n")
    findings = check_data_quality(df) + check_cost_by_dealer(df) + check_volume_spike(df)

    print("=" * 80)
    print(f"LEVEL 1 - THỐNG KÊ & LUẬT: tìm thấy {len(findings)} bất thường")
    print("=" * 80)
    for i, f in enumerate(findings, 1):
        print(f"\n[{i}] {f['type']}  {f['scope']}")
        print(f"    {f['evidence']}")

    print("\n" + "=" * 80)
    print("LEVEL 2 - ISOLATION FOREST: top (dealer, part, tháng) bất thường nhất")
    print("=" * 80)
    top = check_isolation_forest(df)
    print(top[["dealer", "part", "month", "n_claims", "cost_ratio", "count_ratio", "anomaly_score"]]
          .to_string(index=False, float_format="%.2f"))

    (OUT_DIR / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2, default=str))
    plot_monthly(df, OUT_DIR / "claims_by_month.png")
    print(f"\nĐã lưu: {OUT_DIR / 'findings.json'} và {OUT_DIR / 'claims_by_month.png'}")


if __name__ == "__main__":
    main()
