"""Combine the original and eastern NOAA downloads into one compressed CSV."""
import argparse
import csv
import gzip
from pathlib import Path


def merge(sources, destination):
    if destination.exists():
        raise ValueError(f"Output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    row_count = 0
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            columns = None
            for source in sources:
                with gzip.open(source, "rt", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.reader(handle)
                    header = next(reader)
                    if columns is None:
                        columns = header
                        writer.writerow(columns)
                    if header != columns:
                        raise ValueError(f"Columns differ in {source}")
                    for row in reader:
                        if len(row) != len(columns):
                            raise ValueError(f"Malformed row in {source}")
                        writer.writerow(row)
                        row_count += 1
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f"Merged {row_count:,} station-day rows into {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if len({source.resolve() for source in args.sources}) != len(args.sources):
        parser.error("Input files must be distinct")
    merge(args.sources, args.output)
