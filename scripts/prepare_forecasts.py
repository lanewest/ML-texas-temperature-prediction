"""Build station-day forecasting examples and score persistence on validation years."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FOLDS = [(1999, 2004), (2004, 2009), (2009, 2015)]
TEMPERATURES = ["TMIN", "TMAX"]
COLUMNS = ["STATION", "DATE", "LATITUDE", "LONGITUDE", "ELEVATION", *TEMPERATURES]


def prepare(source, output):
    daily = pd.read_csv(
        source, usecols=COLUMNS, parse_dates=["DATE"],
        dtype={column: "float32" for column in COLUMNS[2:]},
    )
    if daily[["STATION", "DATE"]].isna().any().any() or not pd.api.types.is_datetime64_any_dtype(daily["DATE"]):
        raise ValueError("Missing station ID or invalid observation date")
    duplicates = int(daily.duplicated(["STATION", "DATE"]).sum())
    if duplicates:
        raise ValueError(f"Found {duplicates:,} duplicate station-date rows")

    daily[TEMPERATURES] = daily[TEMPERATURES].where(
        np.isfinite(daily[TEMPERATURES]) & daily[TEMPERATURES].ne(-9999)
    )
    annual = daily.groupby(daily["DATE"].dt.year).agg(
        station_days=("STATION", "size"), stations=("STATION", "nunique"),
        valid_tmin=("TMIN", "count"), valid_tmax=("TMAX", "count"),
    )
    annual.index.name = "year"
    audit = {
        "source_rows": len(daily),
        "stations": int(daily["STATION"].nunique()),
        "duplicate_station_dates": duplicates,
        "valid_temperature_values": int(daily[TEMPERATURES].count().sum()),
        "missing_temperature_values": daily[TEMPERATURES].isna().sum().to_dict(),
        "station_days_with_tmin_above_tmax": int(daily["TMIN"].gt(daily["TMAX"]).sum()),
    }

    daily.sort_values(["STATION", "DATE"], inplace=True, ignore_index=True)
    stations = daily.groupby("STATION", sort=False)
    daily["target_date"] = daily["DATE"] + pd.Timedelta(days=1)
    tomorrow = stations["DATE"].shift(-1).eq(daily["target_date"])
    daily["target_tmax"] = stations["TMAX"].shift(-1).where(tomorrow)
    # Lookup exact calendar dates so gaps never shift the meaning of a lag.
    temperature_history = daily.set_index(["STATION", "DATE"])[TEMPERATURES]
    for lag in (1, 2, 3):
        lookup_dates = daily["DATE"] - pd.Timedelta(days=lag)
        lookup_keys = pd.MultiIndex.from_arrays([daily["STATION"], lookup_dates])
        history = temperature_history.reindex(lookup_keys)
        for column in TEMPERATURES:
            daily[f"{column.lower()}_lag_{lag}d"] = history[column].to_numpy()
    phase = 2 * np.pi * (daily["target_date"].dt.dayofyear - 1) / 365.2425
    daily["season_sin"] = np.sin(phase).astype("float32")
    daily["season_cos"] = np.cos(phase).astype("float32")
    examples = daily.loc[daily["target_tmax"].notna()].copy()
    audit["forecast_examples"] = len(examples)
    audit["examples_with_persistence_input"] = int(examples["TMAX"].count())

    scores = []
    for training_end, validation_end in FOLDS:
        start = pd.Timestamp(training_end + 1, 1, 1)
        stop = pd.Timestamp(validation_end + 1, 1, 1)
        validation = examples.loc[examples["target_date"].between(start, stop, inclusive="left")]
        if validation.empty:
            raise ValueError(f"No examples for validation starting {start.date()}")
        # Archive availability timestamps are unknown. Exclude targets dated on
        # the first forecast's issue day from training as a boundary buffer.
        first_issue = validation["DATE"].min()
        train = examples.loc[examples["target_date"].lt(first_issue), "target_date"]
        scored = validation.dropna(subset=["TMAX"])
        if train.empty or scored.empty:
            raise ValueError(f"Insufficient training or scoring rows for {start.year}")
        assert train.max() < first_issue < validation["target_date"].min()
        error = scored["target_tmax"].astype("float64") - scored["TMAX"]
        scores.append({
            "validation_years": f"{start.year}–{validation_end}",
            "latest_training_target": str(train.max().date()),
            "first_validation_issue_date": str(first_issue.date()),
            "eligible_validation_rows": len(validation),
            "scored_rows": len(scored),
            "persistence_mae_c": round(float(error.abs().mean()), 5),
        })

    output.mkdir(parents=True, exist_ok=True)
    examples.to_parquet(output / "forecast_examples.parquet", index=False)
    report = {"audit": audit, "annual_coverage": annual.reset_index().to_dict("records"),
              "validation": scores}
    (output / "baseline_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"audit": audit, "validation": scores}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Merged NOAA daily CSV or CSV.gz")
    parser.add_argument("--output", type=Path, default=Path("forecasting"))
    args = parser.parse_args()
    prepare(args.source, args.output)
