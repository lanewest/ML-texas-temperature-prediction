#!/usr/bin/env python3
"""Download a bounded GHCN daily subset using NOAA's documented Data Service API.

Python 3.9+, standard library only. Defaults reproduce the supplied search URL.
"""
import argparse
import calendar
import csv
import datetime as dt
import gzip
import json
import math
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = ('https://www.ncei.noaa.gov/access/search/datasets/daily-summaries/'
               '?dataTypes=TMIN&dataTypes=TMAX&bbox=33.079,-99.273,29.048,-95.963'
               '&startDate=1950-01-01&endDate=2025-12-31T23:59:59')
API = 'https://www.ncei.noaa.gov/access/services/data/v1'
INVENTORY = 'https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt'
FIELDS = ['STATION', 'NAME', 'LATITUDE', 'LONGITUDE', 'ELEVATION', 'DATE',
          'TMIN', 'TMAX', 'TMIN_ATTRIBUTES', 'TMAX_ATTRIBUTES']


def selection(url, start=None, end=None):
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname != 'www.ncei.noaa.gov' or '/daily-summaries/' not in parsed.path:
        raise ValueError('Use a NOAA daily-summaries search URL.')
    q = urllib.parse.parse_qs(parsed.query)
    # Fail rather than silently ignore filters this downloader does not implement.
    extra = set(q) - {'dataTypes', 'bbox', 'startDate', 'endDate', 'p'}
    if extra:
        raise ValueError('Unsupported search filters: ' + ', '.join(sorted(extra)))
    bbox = q.get('bbox', [''])[0]
    north, west, south, east = map(float, bbox.split(','))
    if not (-90 <= south <= north <= 90 and -180 <= west <= east <= 180):
        raise ValueError('Invalid bounding box; expected north,west,south,east.')
    types = sorted({t for value in q.get('dataTypes', ['TMIN,TMAX'])
                    for t in value.split(',')})
    if types != ['TMAX', 'TMIN']:
        raise ValueError('This project downloader requires exactly TMIN and TMAX.')
    first = dt.date.fromisoformat((start or q['startDate'][0])[:10])
    last = dt.date.fromisoformat((end or q['endDate'][0])[:10])
    if first > last:
        raise ValueError('Start date must not follow end date.')
    return bbox, first, last


def months(first, last):
    while first <= last:
        stop = min(last, dt.date(first.year, first.month,
                                calendar.monthrange(first.year, first.month)[1]))
        yield first, stop
        first = stop + dt.timedelta(days=1)


def discover(bbox, first, last, output, timeout):
    """Select stations using NOAA's element/period inventory; no external join."""
    cache = output / 'ghcnd-inventory.txt'
    if not cache.exists():
        temp = cache.with_suffix('.part')
        try:
            with urllib.request.urlopen(INVENTORY, timeout=timeout) as response, temp.open('wb') as out:
                shutil.copyfileobj(response, out)
            temp.replace(cache)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    north, west, south, east = map(float, bbox.split(','))
    stations = set()
    with cache.open() as handle:
        for line in handle:
            sid, lat, lon, element, start, end = line.split()
            if (element in ('TMIN', 'TMAX') and south <= float(lat) <= north
                    and west <= float(lon) <= east
                    and int(start) <= last.year and int(end) >= first.year):
                stations.add(sid)
    if not stations:
        raise ValueError('No overlapping temperature stations found in NOAA inventory.')
    return sorted(stations)


def request_url(stations, first, last):
    return API + '?' + urllib.parse.urlencode({
        'dataset': 'daily-summaries', 'stations': ','.join(stations),
        'startDate': first.isoformat(), 'endDate': last.isoformat(),
        'dataTypes': 'TMIN,TMAX', 'format': 'csv', 'units': 'metric',
        'includeAttributes': 'true', 'includeStationName': 'true',
        'includeStationLocation': 'true',
    })


def validate(path, first, last):
    """Reject HTML/errors, out-of-range records, and duplicate station-days."""
    seen = set()
    with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not {'STATION', 'DATE'}.issubset(reader.fieldnames or []):
            raise ValueError('Response is not the expected station/date CSV.')
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('Malformed CSV row.')
            date = dt.date.fromisoformat(row['DATE'][:10])
            if not row['STATION'] or not first <= date <= last:
                raise ValueError('Unexpected station or date in response.')
            key = row['STATION'], date
            if key in seen:
                raise ValueError('Duplicate station-date in response: ' + str(key))
            seen.add(key)
    return len(seen)


def fetch(url, dest, first, last, timeout=180, attempts=5):
    temp = dest.with_suffix(dest.suffix + '.part')
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'GHCNd-coursework-downloader/1.0', 'Accept': 'text/csv'})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 204:
                    with gzip.open(temp, 'wt', newline='') as out:
                        csv.writer(out).writerow(FIELDS)
                else:
                    with gzip.open(temp, 'wb') as out:
                        shutil.copyfileobj(response, out, length=1024 * 1024)
            count = validate(temp, first, last)
            temp.replace(dest)
            return count
        except (OSError, ValueError, EOFError, csv.Error) as exc:
            temp.unlink(missing_ok=True)
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt + 1 == attempts:
                raise
            delay = min(60, 2 ** (attempt + 1))
            print(f'  Retry in {delay}s: {exc}', flush=True)
            time.sleep(delay)


def clean_value(row, field):
    """Metric API values are already Celsius; exclude NOAA flagged measurements."""
    flags = (row.get(field + '_ATTRIBUTES') or '').split(',')
    flagged = len(flags) > 1 and bool(flags[1].strip())
    value = (row.get(field) or '').strip()
    try:
        number = float(value)
    except ValueError:
        return '', flagged
    if flagged or not math.isfinite(number) or number == -9999:
        return '', flagged
    return value, False


def combine(parts, output):
    """Stream monthly parts into one normalized CSV; never load full data into RAM."""
    dest = output / 'daily_temperatures.csv.gz'
    temp = dest.with_suffix(dest.suffix + '.part')
    rows = complete = valid = flagged = 0
    stations = set()
    try:
        with gzip.open(temp, 'wt', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for part in parts:
                with gzip.open(part, 'rt', encoding='utf-8-sig', newline='') as source:
                    for row in csv.DictReader(source):
                        record = {key: row.get(key, '') for key in FIELDS}
                        for field in ('TMIN', 'TMAX'):
                            record[field], bad = clean_value(row, field)
                            flagged += int(bad)
                            valid += int(record[field] != '')
                        if not record['TMIN'] and not record['TMAX']:
                            continue
                        complete += int(bool(record['TMIN']) and bool(record['TMAX']))
                        rows += 1
                        stations.add(record['STATION'])
                        writer.writerow(record)
        temp.replace(dest)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return {'station_days_with_temperature': rows, 'stations': len(stations),
            'days_with_both_temperatures': complete,
            'valid_temperature_values': valid, 'quality_flagged_values_removed': flagged,
            'exceeds_10_million_temperature_values': valid > 10_000_000,
            'combined_compressed_bytes': dest.stat().st_size,
            'units': 'degrees Celsius',
            'note': 'Counts exclude IDs, dates, metadata, missing and quality-flagged values. '
                    'Modeling exclusions and train/test splits can reduce usable size further.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search-url', default=DEFAULT_URL)
    parser.add_argument('--start', help='Optional YYYY-MM-DD override, e.g. for a pilot')
    parser.add_argument('--end', help='Optional YYYY-MM-DD override')
    parser.add_argument('--output', type=Path, default=Path('noaa_daily_data'))
    parser.add_argument('--dry-run', action='store_true', help='Show plan without network or writes')
    parser.add_argument('--download', action='store_true', help='Explicitly start downloading')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    try:
        bbox, first, last = selection(args.search_url, args.start, args.end)
        windows = list(months(first, last))
        print(f'NOAA GHCN daily | TMIN/TMAX | {first} through {last}')
        print(f'Bounding box (north,west,south,east): {bbox}')
        print(f'{len(windows)} monthly requests; compressed CSV output: {args.output.resolve()}')
        if args.dry_run or not args.download:
            print('Station discovery inventory: ' + INVENTORY)
            print('No downloads started. Add --download to proceed.')
            return 0
        config = {'dataset': 'daily-summaries', 'bbox': bbox, 'start': str(first),
                  'end': str(last), 'dataTypes': ['TMIN', 'TMAX'], 'units': 'metric',
                  'includeAttributes': True, 'includeStationName': True,
                  'includeStationLocation': True, 'api': API, 'schema_version': 1}
        args.output.mkdir(parents=True, exist_ok=True)
        manifest = args.output / 'selection.json'
        if manifest.exists() and json.loads(manifest.read_text()) != config:
            raise ValueError('Output folder contains a different selection. Use another --output folder.')
        manifest.write_text(json.dumps(config, indent=2) + '\n')
        raw = args.output / 'monthly'
        raw.mkdir(exist_ok=True)
        station_file = args.output / 'stations.json'
        if station_file.exists():
            stations = json.loads(station_file.read_text())
        else:
            print('Finding stations in NOAA inventory...', flush=True)
            stations = discover(bbox, first, last, args.output, args.timeout)
            station_file.write_text(json.dumps(stations, indent=2) + '\n')
        batches = [stations[i:i + 50] for i in range(0, len(stations), 50)]
        jobs = [(start, end, index, batch) for start, end in windows
                for index, batch in enumerate(batches)]
        print(f'{len(stations)} stations; {len(jobs)} monthly station-batch requests.', flush=True)
        parts = []
        for i, (start, end, index, batch) in enumerate(jobs, 1):
            path = raw / f'{start}_{end}_batch{index:03d}.csv.gz'
            parts.append(path)
            if path.exists():
                try:
                    count = validate(path, start, end)
                    print(f'[{i}/{len(jobs)}] Cached {path.name}: {count:,} rows', flush=True)
                    continue
                except (OSError, ValueError, EOFError, csv.Error):
                    print(f'  Replacing invalid cached part: {path.name}')
            count = fetch(request_url(batch, start, end), path, start, end, args.timeout)
            print(f'[{i}/{len(jobs)}] Downloaded {path.name}: {count:,} rows; '
                  f'{path.stat().st_size / 1_000_000:.2f} MB compressed', flush=True)
            time.sleep(1)
        summary = combine(parts, args.output)
        summary['selection'] = config
        summary['completed_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
        if not summary['exceeds_10_million_temperature_values']:
            print('NOTICE: This selection does NOT exceed 10 million valid temperature values.')
        return 0
    except KeyboardInterrupt:
        print('\nInterrupted. Rerun the same command to resume.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'ERROR: {exc}\nCompleted monthly files remain available for resume.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
