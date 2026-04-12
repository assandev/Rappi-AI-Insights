from __future__ import annotations

import argparse
import os
from pathlib import Path

_MPL_CONFIG_DIR = Path("analysis/.mplconfig")
_MPL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_CONFIG_DIR.resolve()))

import matplotlib.pyplot as plt
import pandas as pd

ZONE_ORDER = ["high_income", "corporate", "middle_class", "student", "tourist", "high_density"]
PLATFORM_ORDER = ["Rappi", "Uber Eats"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate presentation-ready platform comparison charts.")
    parser.add_argument("--input", default="data/processed/checkout_results_unified.csv")
    parser.add_argument("--output-dir", default="data/plots")
    parser.add_argument("--dpi", type=int, default=220)
    return parser.parse_args()


def load_and_aggregate(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    total_rows = len(df)
    df = df[df["status"].astype(str).str.lower() == "success"].copy()
    print(f"[INFO] Using success rows only: {len(df)}/{total_rows}")

    df["platform"] = (
        df["platform"]
        .astype(str)
        .str.strip()
        .str.lower()
        .map({"rappi": "Rappi", "ubereats": "Uber Eats"})
    )

    numeric_cols = [
        "total",
        "delivery_fee",
        "service_fee",
        "fee_share_pct",
        "total_per_minute",
        "eta_avg_minutes",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["platform", "zone_type"])
    if df.empty:
        return df

    agg = (
        df.groupby(["platform", "zone_type"], as_index=False, observed=False)[numeric_cols]
        .mean()
    )

    present_zones = [z for z in ZONE_ORDER if z in agg["zone_type"].astype(str).unique()]
    if not present_zones:
        present_zones = sorted(agg["zone_type"].astype(str).unique().tolist())
    agg["zone_type"] = pd.Categorical(agg["zone_type"], categories=present_zones, ordered=True)

    present_platforms = [p for p in PLATFORM_ORDER if p in agg["platform"].astype(str).unique()]
    if not present_platforms:
        present_platforms = sorted(agg["platform"].astype(str).unique().tolist())
    agg["platform"] = pd.Categorical(agg["platform"], categories=present_platforms, ordered=True)
    return agg


def _grouped_bar(
    pivot_df: pd.DataFrame,
    *,
    title: str,
    y_label: str,
    output_file: Path,
    dpi: int,
) -> None:
    zones = list(pivot_df.index)
    platforms = list(pivot_df.columns)
    x = list(range(len(zones)))

    bar_width = 0.8 / max(len(platforms), 1)
    fig, ax = plt.subplots(figsize=(11, 6))

    for i, platform in enumerate(platforms):
        values = pivot_df[platform]
        offsets = [v - 0.4 + (i + 0.5) * bar_width for v in x]
        bars = ax.bar(offsets, values.fillna(0), width=bar_width, label=platform)
        for idx, value in enumerate(values):
            if pd.isna(value):
                bars[idx].set_alpha(0.15)

    ax.set_title(title, fontsize=14)
    ax.set_xlabel("Zone", fontsize=11)
    ax.set_ylabel(y_label, fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([str(z).replace("_", " ").title() for z in zones], rotation=20, ha="right")
    ax.legend(frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(output_file, dpi=max(dpi, 200), bbox_inches="tight")
    plt.close(fig)


def plot_total_price(agg: pd.DataFrame, output_dir: Path, dpi: int) -> None:
    pivot_df = (
        agg.pivot(index="zone_type", columns="platform", values="total")
        .sort_index()
    )
    _grouped_bar(
        pivot_df,
        title="Average Order Cost by Zone",
        y_label="Average Total (MXN)",
        output_file=output_dir / "average_order_cost_by_zone.png",
        dpi=dpi,
    )


def plot_fee_share(agg: pd.DataFrame, output_dir: Path, dpi: int) -> None:
    pivot_df = (
        agg.pivot(index="zone_type", columns="platform", values="fee_share_pct")
        .sort_index()
    )
    _grouped_bar(
        pivot_df,
        title="Average Fee Share (% of Total)",
        y_label="Average Fee Share (%)",
        output_file=output_dir / "average_fee_share_pct.png",
        dpi=dpi,
    )


def plot_fee_breakdown_stacked(agg: pd.DataFrame, output_dir: Path, dpi: int) -> None:
    zones = list(agg["zone_type"].cat.categories)
    platforms = list(agg["platform"].cat.categories)
    x = list(range(len(zones)))
    bar_width = 0.8 / max(len(platforms), 1)

    fig, ax = plt.subplots(figsize=(12, 6))
    for i, platform in enumerate(platforms):
        sub = (
            agg[agg["platform"] == platform]
            .set_index("zone_type")
            .reindex(zones)
        )
        delivery = sub["delivery_fee"].fillna(0)
        service = sub["service_fee"].fillna(0)
        offsets = [v - 0.4 + (i + 0.5) * bar_width for v in x]

        ax.bar(offsets, delivery, width=bar_width, label=f"{platform} - Delivery")
        ax.bar(offsets, service, width=bar_width, bottom=delivery, label=f"{platform} - Service")

    ax.set_title("Fee Composition by Platform", fontsize=14)
    ax.set_xlabel("Zone", fontsize=11)
    ax.set_ylabel("Average Fee Amount (MXN)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([str(z).replace("_", " ").title() for z in zones], rotation=20, ha="right")
    ax.legend(frameon=False, fontsize=9, ncol=2)
    fig.tight_layout()
    fig.savefig(output_dir / "fee_composition_by_platform.png", dpi=max(dpi, 200), bbox_inches="tight")
    plt.close(fig)


def plot_cost_per_minute(agg: pd.DataFrame, output_dir: Path, dpi: int) -> None:
    pivot_df = (
        agg.pivot(index="zone_type", columns="platform", values="total_per_minute")
        .sort_index()
    )
    _grouped_bar(
        pivot_df,
        title="Cost per Minute (Price vs Delivery Time)",
        y_label="Average MXN per Minute",
        output_file=output_dir / "cost_per_minute_by_zone.png",
        dpi=dpi,
    )


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    agg = load_and_aggregate(input_path)
    if agg.empty:
        raise RuntimeError("No successful rows available after filtering.")

    plot_total_price(agg, output_dir, args.dpi)
    plot_fee_share(agg, output_dir, args.dpi)
    plot_fee_breakdown_stacked(agg, output_dir, args.dpi)
    plot_cost_per_minute(agg, output_dir, args.dpi)
    print(f"[INFO] Charts saved to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
