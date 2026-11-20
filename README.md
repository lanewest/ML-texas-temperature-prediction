# ML-texas-temperature-prediction

Predict next-day maximum temperature using NOAA GHCN daily observations from
Central and eastern Texas into Louisiana, covering 1950–2025.

This CS 4347 project compares persistence and ridge regression with
histogram-based gradient boosting. Feature selection and PCA experiments will
assess accuracy and computational cost.

## Current progress

- Downloaded and audited 5,410,145 station-day rows from 507 stations.
- Verified 10,740,949 valid TMIN/TMAX measurements.
- Constructed 5,340,890 next-day forecasting examples.
- Evaluated persistence on three chronological validation folds.
- Ridge regression, gradient boosting, and dimensionality experiments are planned.

| Validation years | Persistence MAE, °C |
|---|---:|
| 2000–2004 | 2.59111 |
| 2005–2009 | 2.71761 |
| 2010–2015 | 2.70664 |

The recorded results are in [reports/baseline_report.json](reports/baseline_report.json).
The proposal describes the planned experiments in [ProjectProposal.md](ProjectProposal.md).

## Setup

Use Python 3.11 or newer. The dependency versions below match the environment
used for the existing preprocessing results.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Use the downloaded dataset

Run commands from the repository root. If the merged NOAA file is already on
your computer, provide its path directly. For the existing project location, run

```bash
python scripts/prepare_forecasts.py \
  data/raw/daily_temperatures_combined.csv.gz \
  --output data/processed
```

This produces `forecast_examples.parquet` and `baseline_report.json` in
`data/processed`. Downloaded data and generated forecasting tables are ignored
by Git. Small result summaries and source code are tracked.

## Reproduce the download

These two nonoverlapping selections cover the study region. Each downloader
command resumes completed monthly batches when rerun with the same output path.
NOAA access can take hours across the full period.

```bash
python scripts/download_noaa.py --download --timeout 60 \
  --output data/raw/original

python scripts/download_noaa.py --download --timeout 60 \
  --output data/raw/east \
  --search-url 'https://www.ncei.noaa.gov/access/search/datasets/daily-summaries/?dataTypes=TMIN&dataTypes=TMAX&bbox=33.079,-95.962999,29.048,-92.000&startDate=1950-01-01&endDate=2025-12-31'

python scripts/merge_downloads.py \
  data/raw/original/daily_temperatures.csv.gz \
  data/raw/east/daily_temperatures.csv.gz \
  --output data/raw/daily_temperatures_combined.csv.gz

python scripts/prepare_forecasts.py \
  data/raw/daily_temperatures_combined.csv.gz \
  --output data/processed
```

NOAA may revise historical observations. Rerunning downloads later can produce
different counts from the recorded snapshot. Temperatures requested through the
API are in degrees Celsius. Quality flags are retained and flagged measurements
are excluded by the downloader.

## Temporal evaluation

Examples match each station's current day to its observed TMAX on exactly the
next calendar day. Predictors include current TMIN/TMAX, recent lags, cyclic
seasonal features, latitude, longitude, and elevation.

| Fold | Training target years | Validation target years |
|---|---|---|
| 1 | 1950–1999 | 2000–2004 |
| 2 | 1950–2004 | 2005–2009 |
| 3 | 1950–2009 | 2010–2015 |

The preprocessing script excludes training targets on the first validation
issue day as a conservative boundary buffer. The baseline report records exact
cutoffs. Model preprocessing will be fitted independently inside each training
fold. Model confirmation uses 2016–2020, followed by final testing on 2021–2025.
The existing baseline evaluates only the three validation folds.

Daily summaries are assumed available before issuing the forecast. Actual
reporting times vary by station and require further analysis. Historical lags use exact station/date lookups to handle gaps. The proposal includes later experiments with longer
histories, rolling features, station/season error analysis, and reporting times.

## Data source

- [NOAA GHCN daily](https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily)
- [NOAA data format and quality flags](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt)
- [NOAA Access Data Service API](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation)

## Next work

- Compare ridge regression and gradient boosting on shared evaluation rows.
- Fit all imputers and transformations inside chronological training folds.
- Examine seasonal, location, and reporting-time effects.
- Compare feature selection and PCA on expanded historical predictors.
- Finalize model settings, evaluate 2021–2025, and prepare the final report.
