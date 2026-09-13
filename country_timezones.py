"""Country-labelled choices and validation backed by the same bundled tzdata."""
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo


@lru_cache(maxsize=1)
def zone_names():
    return {name.casefold(): name for name in files('tzdata').joinpath('zones').read_text().splitlines()}


def normalize_timezone(value):
    if not isinstance(value, str):
        raise ValueError('Choose a country and timezone from the list.')
    key = value.strip().casefold()
    aliases = {'asia/calcutta': 'Asia/Kolkata', 'asia/kolkatha': 'Asia/Kolkata',
               'asia/kolkata': 'Asia/Kolkata', 'utc': 'UTC'}
    result = aliases.get(key) or zone_names().get(key)
    if not result:
        raise ValueError('Choose a country and timezone from the list.')
    return result


@lru_cache(maxsize=600)
def bundled_zone(value):
    key = normalize_timezone(value)
    # Use the shipped database even when the host OS database is missing or older.
    with files('tzdata.zoneinfo').joinpath(*key.split('/')).open('rb') as source:
        return ZoneInfo.from_file(source, key=key)


@lru_cache(maxsize=1)
def country_zones():
    root = files('tzdata.zoneinfo')
    countries = dict(line.split('\t', 1) for line in root.joinpath('iso3166.tab').read_text().splitlines()
                     if line and not line.startswith('#'))
    result = []
    for line in root.joinpath('zone.tab').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        country, _, zone, *_ = line.split('\t')
        zone = normalize_timezone(zone)
        city = zone.split('/')[-1].replace('_', ' ')
        place = f'{countries[country]} — {city}'
        if zone == 'Asia/Kolkata':
            place = 'India — Kolkata (IST)'
        elif zone == 'America/Phoenix':
            place = 'United States — Arizona / Phoenix (no DST)'
        result.append((zone, place))
    return sorted(set(result), key=lambda item: item[1])


def timezone_options():
    now = datetime.now(timezone.utc)
    result = []
    for zone, place in country_zones():
        minutes = int(now.astimezone(bundled_zone(zone)).utcoffset().total_seconds() / 60)
        sign = '+' if minutes >= 0 else '-'
        hours, remainder = divmod(abs(minutes), 60)
        result.append({'value': zone, 'label': f'{place} (UTC{sign}{hours:02d}:{remainder:02d})'})
    return result + [{'value': 'UTC', 'label': 'Worldwide — UTC (UTC+00:00)'}]
