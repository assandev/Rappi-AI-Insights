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


def load_and_aggregate(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
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
        return df, df

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
    return agg, df


def _add_bar_labels(
    ax: plt.Axes,
    bars,
    raw_values: pd.Series | list[float],
    *,
    suffix: str = "",
) -> None:
    for idx, bar in enumerate(bars):
        value = raw_values.iloc[idx] if hasattr(raw_values, "iloc") else raw_values[idx]
        if pd.isna(value):
            continue
        label = f"{float(value):.1f}{suffix}"
        ax.text(
            bar.get_x() + (bar.get_width() / 2.0),
            bar.get_height(),
            label,
            ha="center",
            va="bottom",
            fontsize=8,
        )


def _grouped_bar(
    pivot_df: pd.DataFrame,
    *,
    title: str,
    y_label: str,
    output_file: Path,
    dpi: int,
    label_suffix: str = "",
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
        _add_bar_labels(ax, bars, values, suffix=label_suffix)

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
        title="Average Total Order Cost by Zone",
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
        title="Average Fee Share by Zone",
        y_label="Average Fee Share (%)",
        output_file=output_dir / "average_fee_share_pct.png",
        dpi=dpi,
        label_suffix="%",
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
        delivery_raw = sub["delivery_fee"]
        service_raw = sub["service_fee"]
        delivery = delivery_raw.fillna(0)
        service = service_raw.fillna(0)
        offsets = [v - 0.4 + (i + 0.5) * bar_width for v in x]

        bars_delivery = ax.bar(offsets, delivery, width=bar_width, label=f"{platform} - Delivery")
        bars_service = ax.bar(offsets, service, width=bar_width, bottom=delivery, label=f"{platform} - Service")

        for idx in range(len(zones)):
            if pd.isna(delivery_raw.iloc[idx]) and pd.isna(service_raw.iloc[idx]):
                bars_delivery[idx].set_alpha(0.15)
                bars_service[idx].set_alpha(0.15)

    ax.set_title("Fee Composition by Zone and Platform", fontsize=14)
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
        title="Average Cost per Minute by Zone",
        y_label="Average MXN per Minute",
        output_file=output_dir / "cost_per_minute_by_zone.png",
        dpi=dpi,
    )


def plot_uber_premium_vs_rappi(agg: pd.DataFrame, output_dir: Path, dpi: int) -> None:
    pivot_total = (
        agg.pivot(index="zone_type", columns="platform", values="total")
        .sort_index()
    )
    if "Rappi" not in pivot_total.columns or "Uber Eats" not in pivot_total.columns:
        print("[WARN] Skipping premium plot: both Rappi and Uber Eats totals are required.")
        return

    premium = ((pivot_total["Uber Eats"] - pivot_total["Rappi"]) / pivot_total["Rappi"]) * 100.0
    premium = premium.replace([float("inf"), float("-inf")], pd.NA)

    zones = list(premium.index)
    x = list(range(len(zones)))
    fig, ax = plt.subplots(figsize=(11, 6))
    bars = ax.bar(x, premium.fillna(0), width=0.6, label="Uber Eats Premium")
    for idx, value in enumerate(premium):
        if pd.isna(value):
            bars[idx].set_alpha(0.15)
    _add_bar_labels(ax, bars, premium, suffix="%")

    ax.set_title("Uber Eats Premium vs Rappi by Zone", fontsize=14)
    ax.set_xlabel("Zone", fontsize=11)
    ax.set_ylabel("Premium vs Rappi (%)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([str(z).replace("_", " ").title() for z in zones], rotation=20, ha="right")
    ax.legend(frameon=False, fontsize=10)
    fig.tight_layout()
    fig.savefig(output_dir / "uber_premium_vs_rappi_pct.png", dpi=max(dpi, 200), bbox_inches="tight")
    plt.close(fig)


def build_platform_summary(df: pd.DataFrame, output_dir: Path) -> None:
    summary = (
        df.groupby("platform", as_index=False, observed=False)
        .agg(
            average_total=("total", "mean"),
            average_delivery_fee=("delivery_fee", "mean"),
            average_service_fee=("service_fee", "mean"),
            average_fee_share_pct=("fee_share_pct", "mean"),
            average_total_per_minute=("total_per_minute", "mean"),
            average_eta_avg_minutes=("eta_avg_minutes", "mean"),
            observation_count=("platform", "size"),
        )
    )
    summary["platform"] = pd.Categorical(summary["platform"], categories=PLATFORM_ORDER, ordered=True)
    summary = summary.sort_values("platform").reset_index(drop=True)
    summary.to_csv(output_dir / "platform_summary_metrics.csv", index=False, encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    agg, df_success = load_and_aggregate(input_path)
    if agg.empty:
        raise RuntimeError("No successful rows available after filtering.")

    build_platform_summary(df_success, output_dir)
    plot_total_price(agg, output_dir, args.dpi)
    plot_fee_share(agg, output_dir, args.dpi)
    plot_fee_breakdown_stacked(agg, output_dir, args.dpi)
    plot_cost_per_minute(agg, output_dir, args.dpi)
    plot_uber_premium_vs_rappi(agg, output_dir, args.dpi)
    print(f"[INFO] Charts saved to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
