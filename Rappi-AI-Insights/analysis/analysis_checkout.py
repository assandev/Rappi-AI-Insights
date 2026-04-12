from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_INPUTS = [
    "data/screenshots/checkout_result.json",
    "data/ubereats/results/checkout_result_ubereats.json",
]
DEFAULT_OUTPUT = "data/processed/checkout_results_unified.csv"

BASE_COLUMNS = [
    "platform",
    "zone_type",
    "address",
    "product",
    "restaurant",
    "subtotal",
    "delivery_fee",
    "service_fee",
    "total",
    "eta_avg_minutes",
    "currency",
    "status",
]

DERIVED_COLUMNS = [
    "fee_total",
    "fee_share_pct",
    "delivery_share_pct",
    "service_share_pct",
    "total_per_minute",
]

ALL_COLUMNS = BASE_COLUMNS + DERIVED_COLUMNS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unify Rappi + Uber Eats checkout JSON into one analysis CSV.")
    parser.add_argument(
        "--inputs",
        nargs="+",
        default=DEFAULT_INPUTS,
        help="Input JSON files or directories. Defaults to both platform result files.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Unified CSV output path.",
    )
    return parser.parse_args()


def load_results(inputs: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in inputs:
        if path.is_file():
            rows.extend(_load_json_records(path))
            continue
        if path.is_dir():
            for json_path in sorted(path.rglob("*.json")):
                rows.extend(_load_json_records(json_path))
            continue
        print(f"[WARN] Skipping missing input: {path}")
    return rows


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict) and _looks_like_checkout_record(r)]
    if isinstance(parsed, dict) and _looks_like_checkout_record(parsed):
        return [parsed]
    return []


def _looks_like_checkout_record(obj: dict[str, Any]) -> bool:
    return ("platform" in obj or "status" in obj) and ("product" in obj)


def extract_zone_type(path_text: str) -> str:
    # Example run folder segment: a2_corporate_mcflurry-oreo
    m = re.search(r"a\d+_([a-z-]+)_[a-z0-9-]+", path_text.lower())
    if not m:
        return "unknown"
    return m.group(1).replace("-", "_")


def build_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=ALL_COLUMNS)

    df = pd.DataFrame(records)
    for col in BASE_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    zone_source = df.get("screenshot_path", pd.Series(index=df.index, dtype="object")).fillna(
        df.get("network_log_file", pd.Series(index=df.index, dtype="object"))
    )
    inferred_zone = zone_source.astype(str).map(extract_zone_type)
    df["zone_type"] = df["zone_type"].fillna(inferred_zone).fillna("unknown")

    for col in ["subtotal", "delivery_fee", "service_fee", "total", "eta_avg_minutes"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["platform"] = df["platform"].astype(str).str.strip().str.lower()
    df["status"] = df["status"].astype(str).str.strip().str.lower()

    df["fee_total"] = df["delivery_fee"].fillna(0) + df["service_fee"].fillna(0)
    df["fee_share_pct"] = (df["fee_total"] / df["total"]) * 100.0
    df["delivery_share_pct"] = (df["delivery_fee"] / df["total"]) * 100.0
    df["service_share_pct"] = (df["service_fee"] / df["total"]) * 100.0
    df["total_per_minute"] = df["total"] / df["eta_avg_minutes"]

    for col in ["fee_share_pct", "delivery_share_pct", "service_share_pct", "total_per_minute"]:
        df.loc[~df["total"].gt(0), col] = pd.NA
    df.loc[~df["eta_avg_minutes"].gt(0), "total_per_minute"] = pd.NA

    dedupe_cols = [c for c in ["platform", "address", "product", "status", "total"] if c in df.columns]
    if dedupe_cols:
        df = df.drop_duplicates(subset=dedupe_cols, keep="last")

    return df[ALL_COLUMNS].copy().sort_values(["platform", "zone_type", "product"], na_position="last").reset_index(drop=True)


def main() -> int:
    args = parse_args()
    input_paths = [Path(p) for p in args.inputs]
    output_path = Path(args.output)

    records = load_results(input_paths)
    if not records:
        raise RuntimeError("No checkout records found in the provided inputs.")

    df = build_dataframe(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    print(f"[INFO] Unified checkout CSV saved: {output_path}")
    print(f"[INFO] Rows: {len(df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
