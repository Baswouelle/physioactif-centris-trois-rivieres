#!/usr/bin/env python3
"""
Incremental Centris Commercial Listings Refresh

Designed for GitHub Actions. Runs the full GetMarkers + GetMarkerInfo pipeline
to discover current listings, but skips detail page fetches for listings already
in the cache. Only NEW listings get their detail pages fetched.

Savings: on a typical run with ~5 new listings out of ~660, this skips ~655
detail page fetches (each 0.5s + network), cutting runtime from ~12 min to ~6 min.

Usage:
    python refresh_centris.py              # Normal incremental refresh
    python refresh_centris.py --dry-run    # Show diff without fetching details
    python refresh_centris.py --full       # Force full detail refresh
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
CACHE_FILE = SCRIPT_DIR / 'commercial_latest.json'
INDEX_FILE = SCRIPT_DIR / 'index.html'
NEW_LISTINGS_FILE = SCRIPT_DIR / 'new_listings.json'

BASE_URL = 'https://www.centris.ca'
API_MARKERS = f'{BASE_URL}/api/property/map/GetMarkers'
API_MARKER_INFO = f'{BASE_URL}/property/GetMarkerInfo'

API_DELAY = 0.3
DETAIL_DELAY = 0.5

COMMERCIAL_CATEGORIES = {'batisse commerciale', 'local commercial', 'commerce'}

# Allowlist of Centris MUNICIPALITY URL SLUGS for the Trois-Rivières region.
# NOTE: the `city` field parsed from GetMarkerInfo is actually the ADMINISTRATIVE
# REGION ('Mauricie', 'Centre-du-Québec'), which is too coarse to separate
# Trois-Rivières from Shawinigan / Nicolet. The municipality is reliably encoded
# in the listing_url slug (…/local-commercial~a-louer~trois-rivieres/12345678),
# so we filter on that slug instead. Both shores are desired (Trois-Rivières +
# Bécancour and their borough slugs), so this is an INCLUSION filter, not a
# cross-river exclusion. Calibrated empirically from the dry-run slug distribution
# (set CENTRIS_DUMP_CITIES=1 to dump the pre-filter slug counts to /tmp/tr_slugs.json).
REGION_CITIES = {
    'trois-rivieres',
    'becancour',
}

HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Safari/537.36'
    ),
    'Origin': BASE_URL,
    'Referer': f'{BASE_URL}/fr/propriete-commerciale~a-louer',
}

# All areas used in the full search
AREAS = {
    'trois-rivieres': {
        'label': 'Trois-Rivières (ville amalgamée)',
        'bounds': {'NorthEast': {'Lat': 46.42, 'Lng': -72.45},
                   'SouthWest': {'Lat': 46.27, 'Lng': -72.72}},
    },
    'becancour': {
        'label': 'Bécancour',
        'bounds': {'NorthEast': {'Lat': 46.38, 'Lng': -72.30},
                   'SouthWest': {'Lat': 46.18, 'Lng': -72.58}},
    },
    'trois-rivieres-region': {
        'label': 'Région de Trois-Rivières (englobante)',
        'bounds': {'NorthEast': {'Lat': 46.45, 'Lng': -72.25},
                   'SouthWest': {'Lat': 46.15, 'Lng': -72.75}},
    },
}

# Areas to scan: Trois-Rivières + Bécancour
SEARCH_AREAS = [
    'trois-rivieres', 'becancour',
]
SELLING_TYPES = ['Rent', 'Sale']


def build_query(selling_type: str) -> Dict:
    """Build the Centris query object."""
    fields = [
        {"fieldId": "Category", "value": "Commercial",
         "fieldConditionId": "", "valueConditionId": ""},
        {"fieldId": "SellingType", "value": selling_type,
         "fieldConditionId": "", "valueConditionId": ""},
    ]
    price_field = "RentPrice" if selling_type == "Rent" else "SalePrice"
    condition = "ForRent" if selling_type == "Rent" else "ForSale"
    fields.extend([
        {"fieldId": price_field, "value": 0,
         "fieldConditionId": condition, "valueConditionId": ""},
        {"fieldId": price_field, "value": 999999999999,
         "fieldConditionId": condition, "valueConditionId": ""},
    ])
    return {
        "SearchName": "",
        "UseGeographyShapes": 0,
        "Filters": [],
        "FieldsValues": fields,
        "BrokerCode": None,
        "OfficeKey": None,
    }


def get_markers(session: requests.Session, bounds: Dict,
                query: Dict, zoom: int = 14) -> List[Dict]:
    """Fetch map markers in a bounding box."""
    payload = {
        "zoomLevel": zoom,
        "mapBounds": bounds,
        "mode": "Result",
        "sort": "None",
        "sortSeed": int(time.time()),
        "query": query,
        "region": "Quebec",
        "openListing": None,
    }
    resp = session.post(API_MARKERS, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('d', {}).get('Result', {}).get('Markers', [])


def _parse_marker_html(html: str, marker_data: Dict) -> Optional[Dict]:
    """Parse HTML from GetMarkerInfo into a structured listing dict."""
    if not html:
        return None

    mls_match = re.search(r'content="(\d{5,10})"\s*itemprop="sku"', html)
    if not mls_match:
        mls_match = re.search(r'itemprop="sku"\s*content="(\d{5,10})"', html)
    mls = mls_match.group(1) if mls_match else marker_data.get('NoMls')
    if not mls:
        return None

    name_match = re.search(r'content="([^"]+)"\s*itemprop="name"', html)
    if not name_match:
        name_match = re.search(r'itemprop="name"\s*content="([^"]+)"', html)
    title = unescape(name_match.group(1)) if name_match else ''
    title = re.sub(r'\s*-\s*Centris\.ca$', '', title)

    link_match = re.search(r'href="(/fr/[^"]+)"', html)
    listing_url = f'{BASE_URL}{link_match.group(1)}' if link_match else ''

    category = ''
    if link_match:
        url_path = link_match.group(1)
        cat_match = re.search(r'/fr/([^~]+)~', url_path)
        if cat_match:
            category = cat_match.group(1).replace('-', ' ').title()

    price_match = re.search(r'<span class="price">(.*?)</span>', html, re.DOTALL)
    price_display = unescape(price_match.group(1)).strip() if price_match else ''

    price_val_match = re.search(r'itemprop="price"\s*content="([^"]+)"', html)
    if not price_val_match:
        price_val_match = re.search(r'content="([^"]+)"\s*itemprop="price"', html)
    price_value = price_val_match.group(1) if price_val_match else None

    addr_parts = []
    if title:
        parts = title.split(',')
        if len(parts) >= 2:
            addr_parts = [p.strip() for p in parts[1:-1]]

    photo_match = re.search(r'>(\d+)<i class="fa[rl]? fa-camera"', html)
    photo_count = int(photo_match.group(1)) if photo_match else 0

    pos = marker_data.get('Position', {})

    return {
        'mls_number': str(mls),
        'title': title,
        'category': category,
        'address': ', '.join(addr_parts),
        'city': addr_parts[0] if addr_parts else '',
        'price_value': price_value,
        'price_display': price_display,
        'latitude': pos.get('Lat'),
        'longitude': pos.get('Lng'),
        'photo_count': photo_count,
        'listing_url': listing_url,
        'sqft': None,
        'description': None,
        'broker': None,
    }


def get_marker_info(session: requests.Session, marker: Dict,
                    bounds: Dict, query: Dict) -> Optional[Dict]:
    """Fetch listing preview from GetMarkerInfo."""
    pos = marker['Position']
    payload = {
        "pageIndex": 0,
        "zoomLevel": 18,
        "latitude": pos['Lat'],
        "longitude": pos['Lng'],
        "mapBounds": bounds,
        "geoHash": marker.get('GeoHash', ''),
        "sortSeed": int(time.time()),
        "sort": "None",
        "mode": "Result",
        "query": query,
        "region": "Quebec",
    }
    resp = session.post(API_MARKER_INFO, json=payload, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    result = data.get('d', {}).get('Result', {})
    return _parse_marker_html(result.get('Html', ''), result.get('Marker', {}))


def get_cluster_listings(session: requests.Session, marker: Dict,
                         bounds: Dict, query: Dict) -> List[Dict]:
    """Iterate through all listings in a cluster using pageIndex."""
    pos = marker['Position']
    count = marker.get('PointsCount', 1)
    listings = []

    for page_idx in range(count):
        payload = {
            "pageIndex": page_idx,
            "zoomLevel": 18,
            "latitude": pos['Lat'],
            "longitude": pos['Lng'],
            "mapBounds": bounds,
            "geoHash": marker.get('GeoHash', ''),
            "sortSeed": int(time.time()),
            "sort": "None",
            "mode": "Result",
            "query": query,
            "region": "Quebec",
        }
        try:
            resp = session.post(API_MARKER_INFO, json=payload,
                                headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            result = data.get('d', {}).get('Result', {})
            listing = _parse_marker_html(result.get('Html', ''), result.get('Marker', {}))
            if listing:
                listings.append(listing)
            time.sleep(API_DELAY)
        except Exception as e:
            logger.warning(f'  GetMarkerInfo page {page_idx} failed: {e}')
            break

    return listings


def fetch_listing_detail(session: requests.Session, listing: Dict) -> Dict:
    """Fetch detail page for sqft, description, broker."""
    url = listing.get('listing_url')
    if not url:
        return listing

    try:
        resp = session.get(url, headers={
            'User-Agent': HEADERS['User-Agent'],
            'Accept-Language': 'fr-CA,fr;q=0.9',
        }, timeout=15)
        resp.raise_for_status()
        text = resp.text

        price_match = re.search(r'itemprop="price"\s*content="([^"]+)"', text)
        if price_match and not listing.get('price_value'):
            listing['price_value'] = price_match.group(1)

        desc_match = re.search(
            r'<meta\s+(?:property="og:description"|name="description")'
            r'\s+content="([^"]+)"', text, re.IGNORECASE
        )
        if desc_match:
            listing['description'] = unescape(desc_match.group(1))[:300]

        for pattern in [
            r'Superficie commerciale disponible\s*</(?:td|div|span)>\s*'
            r'<(?:td|div|span)[^>]*>\s*([\d\s\xa0]+)\s*pc',
            r'Superficie du b.timent\s*</(?:td|div|span)>\s*'
            r'<(?:td|div|span)[^>]*>\s*([\d\s\xa0]+)\s*pc',
            r'([\d\s\xa0]+)\s*(?:pieds?\s*carr|pi2|pi\xb2|pc)',
        ]:
            sqft_match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if sqft_match:
                sqft_str = (sqft_match.group(1)
                            .replace(' ', '')
                            .replace('\xa0', '')
                            .strip())
                try:
                    val = int(sqft_str)
                    if 50 <= val <= 1_000_000:
                        listing['sqft'] = val
                        break
                except ValueError:
                    pass

        broker_match = re.search(r'itemprop="legalName"\s*content="([^"]+)"', text)
        if not broker_match:
            broker_match = re.search(r'content="([^"]+)"\s*itemprop="legalName"', text)
        if broker_match:
            listing['broker'] = unescape(broker_match.group(1))

    except Exception as e:
        logger.warning(f'  Detail fetch failed for MLS {listing["mls_number"]}: {e}')

    return listing


def search_area(session: requests.Session, area_key: str,
                selling_type: str, cache_lookup: Dict[str, Dict],
                fetch_all_details: bool = False) -> Tuple[List[Dict], int, int, int]:
    """
    Search all commercial listings in an area.
    Returns (listings, new_count, cached_count, api_calls).
    For listings found in cache_lookup, reuses cached data (skips detail fetch).
    """
    area = AREAS[area_key]
    bounds = area['bounds']
    query = build_query(selling_type)
    tx_label = 'location' if selling_type == 'Rent' else 'vente'

    logger.info(f'  {area["label"]} ({tx_label})...')

    markers = get_markers(session, bounds, query, zoom=14)
    api_calls = 1
    total_points = sum(m.get('PointsCount', 0) for m in markers)
    logger.info(f'    {len(markers)} markers, ~{total_points} listings')
    time.sleep(API_DELAY)

    if not markers:
        return [], 0, 0, api_calls

    all_listings = []
    seen_mls = set()
    new_count = 0
    cached_count = 0

    for marker in markers:
        count = marker.get('PointsCount', 0)
        mls = marker.get('NoMls')

        if count == 0:
            continue

        if count == 1 or mls:
            listing = get_marker_info(session, marker, bounds, query)
            api_calls += 1
            if listing and listing.get('category', '').lower() not in COMMERCIAL_CATEGORIES:
                time.sleep(API_DELAY)
                continue
            if listing and listing['mls_number'] not in seen_mls:
                seen_mls.add(listing['mls_number'])

                # Check cache
                if listing['mls_number'] in cache_lookup and not fetch_all_details:
                    cached = cache_lookup[listing['mls_number']]
                    # Update position from fresh marker data (in case it moved)
                    cached['latitude'] = listing.get('latitude') or cached.get('latitude')
                    cached['longitude'] = listing.get('longitude') or cached.get('longitude')
                    # Update price from fresh marker data
                    if listing.get('price_value'):
                        cached['price_value'] = listing['price_value']
                    if listing.get('price_display'):
                        cached['price_display'] = listing['price_display']
                    if not cached.get('first_seen'):
                        cached['first_seen'] = datetime.now().date().isoformat()
                    all_listings.append(cached)
                    cached_count += 1
                else:
                    listing['area'] = area_key
                    listing['area_label'] = area['label']
                    listing['transaction_type'] = 'lease' if selling_type == 'Rent' else 'sale'
                    listing['first_seen'] = datetime.now().date().isoformat()
                    fetch_listing_detail(session, listing)
                    api_calls += 1
                    time.sleep(DETAIL_DELAY)
                    all_listings.append(listing)
                    new_count += 1

            time.sleep(API_DELAY)
        else:
            cluster_listings = get_cluster_listings(session, marker, bounds, query)
            api_calls += count  # one call per page

            for listing in cluster_listings:
                if listing and listing.get('category', '').lower() not in COMMERCIAL_CATEGORIES:
                    continue
                if listing and listing['mls_number'] not in seen_mls:
                    seen_mls.add(listing['mls_number'])

                    if listing['mls_number'] in cache_lookup and not fetch_all_details:
                        cached = cache_lookup[listing['mls_number']]
                        cached['latitude'] = listing.get('latitude') or cached.get('latitude')
                        cached['longitude'] = listing.get('longitude') or cached.get('longitude')
                        if listing.get('price_value'):
                            cached['price_value'] = listing['price_value']
                        if listing.get('price_display'):
                            cached['price_display'] = listing['price_display']
                        if not cached.get('first_seen'):
                            cached['first_seen'] = datetime.now().date().isoformat()
                        all_listings.append(cached)
                        cached_count += 1
                    else:
                        listing['area'] = area_key
                        listing['area_label'] = area['label']
                        listing['transaction_type'] = 'lease' if selling_type == 'Rent' else 'sale'
                        listing['first_seen'] = datetime.now().date().isoformat()
                        fetch_listing_detail(session, listing)
                        api_calls += 1
                        time.sleep(DETAIL_DELAY)
                        all_listings.append(listing)
                        new_count += 1

    logger.info(f'    {len(all_listings)} listings ({new_count} new, {cached_count} cached)')
    return all_listings, new_count, cached_count, api_calls


def building_key(address: Optional[str]) -> Optional[str]:
    """Normalize an address down to its building (street, civic number), dropping
    the suite/local. Returns None for placeholder/empty addresses, which must never
    be treated as 'already seen' (e.g. 'Rue Non Disponible-Unavailable' would
    collapse unrelated listings into one fake building).
    """
    a = (address or '').strip()
    low = a.lower()
    if not a or 'non disponible' in low or 'unavailable' in low:
        return None
    key = re.sub(r',?\s*local\s+[\w/.\-]+\s*$', '', a, flags=re.IGNORECASE)
    key = re.sub(r'[\s\xa0]+', ' ', key).strip().lower()
    return key or None


def _norm_slug(s: str) -> str:
    """Accent/case-fold a municipality slug for tolerant allowlist matching."""
    s = (s or '').strip().lower()
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')


REGION_CITIES_NORM = {_norm_slug(c) for c in REGION_CITIES}


def municipality_slug(listing: Dict) -> str:
    """Extract the Centris municipality slug from a listing's URL.

    URL shape: https://www.centris.ca/fr/<category>~<transaction>~<municipality>/<mls>
    e.g. .../local-commercial~a-louer~trois-rivieres/12345678 -> 'trois-rivieres'.
    """
    url = listing.get('listing_url') or ''
    m = re.search(r'~([^~/]+)/\d+', url)
    return _norm_slug(m.group(1)) if m else ''


def is_out_of_region(listing: Dict) -> bool:
    """Drop listings whose municipality slug is not in the region allowlist.

    The bounding boxes overlap surrounding Mauricie/Centre-du-Québec towns; this
    keeps only listings whose municipality slug (from listing_url) is in
    REGION_CITIES. Both shores (Trois-Rivières + Bécancour and their borough
    slugs) are desired, so this is an inclusion filter.
    """
    slug = municipality_slug(listing)
    if not slug:
        return False  # can't determine municipality -> keep (don't silently drop)
    return slug not in REGION_CITIES_NORM


def update_index_html(listings: List[Dict]) -> bool:
    """Update index.html by replacing only the DATA variable and date."""
    if not INDEX_FILE.exists():
        logger.error(f'index.html not found at {INDEX_FILE}')
        return False

    html = INDEX_FILE.read_text(encoding='utf-8')

    new_data = json.dumps({
        'search_date': datetime.now().isoformat(),
        'total_listings': len(listings),
        'source': 'centris.ca (API)',
        'listings': listings,
    }, ensure_ascii=False)

    # Replace const DATA = {...}; using string slicing (not re.sub,
    # which interprets \n in replacement strings as literal newlines)
    match = re.search(r'const DATA = \{.*?\};', html, flags=re.DOTALL)
    if not match:
        logger.error('Could not find const DATA = {...}; in index.html')
        return False
    new_html = html[:match.start()] + f'const DATA = {new_data};' + html[match.end():]

    # Replace date display
    new_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    new_html = re.sub(
        r'Mise a jour: [^<]+',
        f'Mise a jour: {new_date}',
        new_html,
        count=1,
    )

    INDEX_FILE.write_text(new_html, encoding='utf-8')
    logger.info(f'Updated index.html ({len(listings)} listings, date: {new_date})')
    return True


def main():
    parser = argparse.ArgumentParser(description='Incremental Centris refresh')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would change without fetching details')
    parser.add_argument('--full', action='store_true',
                        help='Force full detail refresh (ignore cache)')
    args = parser.parse_args()

    session = requests.Session()

    # Load cache
    cache_lookup = {}
    if CACHE_FILE.exists() and not args.full:
        with open(CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
        for listing in cache.get('listings', []):
            cache_lookup[listing['mls_number']] = listing
        logger.info(f'Loaded cache: {len(cache_lookup)} listings from {cache.get("search_date", "?")}')
    else:
        logger.info('No cache or --full mode: all listings will get detail pages fetched')

    if args.dry_run:
        logger.info('[DRY RUN] Would scan all areas and show diff. Exiting.')
        logger.info(f'Cache has {len(cache_lookup)} listings. Run without --dry-run to refresh.')
        return

    # Search all areas
    logger.info(f'\nSearching {len(SEARCH_AREAS)} areas x {len(SELLING_TYPES)} transaction types...')
    all_listings = []
    seen_mls = set()
    total_new = 0
    total_cached = 0
    total_api_calls = 0

    for area_key in SEARCH_AREAS:
        for selling_type in SELLING_TYPES:
            listings, new_count, cached_count, calls = search_area(
                session, area_key, selling_type, cache_lookup,
                fetch_all_details=args.full,
            )
            for lst in listings:
                if lst['mls_number'] not in seen_mls:
                    seen_mls.add(lst['mls_number'])
                    all_listings.append(lst)
            total_new += new_count
            total_cached += cached_count
            total_api_calls += calls

    # Calibration aid: dump the pre-filter municipality-slug distribution so the
    # REGION_CITIES allowlist can be set empirically (slugs are unknown until we
    # see what Centris returns for the amalgamated region).
    if os.environ.get('CENTRIS_DUMP_CITIES'):
        import collections
        slug_counts = collections.Counter(municipality_slug(l) for l in all_listings)
        region_counts = collections.Counter((l.get('city') or '') for l in all_listings)
        Path('/tmp/tr_slugs.json').write_text(json.dumps({
            'by_slug': slug_counts.most_common(),
            'by_region_field': region_counts.most_common(),
            'total_prefilter': len(all_listings),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        logger.info(f'[CALIBRATION] dumped {len(slug_counts)} slugs to /tmp/tr_slugs.json')

    # Drop out-of-region listings (municipality slug not in REGION_CITIES)
    before_filter = len(all_listings)
    all_listings = [l for l in all_listings if not is_out_of_region(l)]
    out_of_region_dropped = before_filter - len(all_listings)
    if out_of_region_dropped:
        logger.info(f'Filtered {out_of_region_dropped} out-of-region listings (not in REGION_CITIES)')

    # Identify delisted
    cached_mls = set(cache_lookup.keys())
    current_mls = set(lst['mls_number'] for lst in all_listings)
    delisted = cached_mls - current_mls

    logger.info(f'\nDedup: {len(all_listings)} unique listings')
    if delisted:
        logger.info(f'Delisted ({len(delisted)}): {sorted(delisted)[:10]}{"..." if len(delisted) > 10 else ""}')

    # Save cache
    output = {
        'search_date': datetime.now().isoformat(),
        'total_listings': len(all_listings),
        'source': 'centris.ca (API)',
        'listings': all_listings,
    }
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f'Saved: {CACHE_FILE}')

    # Write new_listings.json for email step (only listings not in prior cache).
    # Tag each with building_seen_before: was this building's street address already
    # in the prior cache? Brokers re-list (new MLS, often a different suite) in
    # buildings we already track; the email demotes those to a "deja suivi" section
    # instead of headlining them as brand-new (see send_email.build_html).
    prior_buildings = set()
    for lst in cache_lookup.values():
        bk = building_key(lst.get('address'))
        if bk:
            prior_buildings.add(bk)

    new_listings = []
    for lst in all_listings:
        if lst['mls_number'] in cache_lookup:
            continue
        bk = building_key(lst.get('address'))
        entry = dict(lst)  # copy so the cache/index.html stay free of this flag
        entry['building_seen_before'] = bool(bk and bk in prior_buildings)
        new_listings.append(entry)

    fresh_n = sum(1 for l in new_listings if not l['building_seen_before'])
    known_n = len(new_listings) - fresh_n
    if new_listings:
        with open(NEW_LISTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'search_date': datetime.now().isoformat(),
                'count': len(new_listings),
                'fresh_buildings': fresh_n,
                'seen_buildings': known_n,
                'listings': new_listings,
            }, f, ensure_ascii=False, indent=2)
        logger.info(f'Saved: {NEW_LISTINGS_FILE} '
                    f'({len(new_listings)} new: {fresh_n} nouveaux immeubles, '
                    f'{known_n} locaux dans immeubles deja suivis)')
    elif NEW_LISTINGS_FILE.exists():
        NEW_LISTINGS_FILE.unlink()
        logger.info(f'Removed stale {NEW_LISTINGS_FILE} (no new listings)')

    # Update index.html
    update_index_html(all_listings)

    # Summary
    logger.info(f'\n{"="*60}')
    logger.info(f'SUMMARY')
    logger.info(f'{"="*60}')
    logger.info(f'  New listings:       {total_new}')
    logger.info(f'  Cached (reused):    {total_cached}')
    logger.info(f'  Delisted:           {len(delisted)}')
    logger.info(f'  Total listings:     {len(all_listings)}')
    logger.info(f'  Total API calls:    {total_api_calls}')
    logger.info(f'  Detail pages saved: ~{total_cached} (skipped for cached listings)')
    logger.info(f'{"="*60}')


if __name__ == '__main__':
    main()
