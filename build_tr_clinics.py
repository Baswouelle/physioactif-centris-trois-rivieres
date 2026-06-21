#!/usr/bin/env python3
"""Build the Trois-Rivières + Bécancour physio-clinic layer for the Centris map.

Source: OPPQ roster in the Physioactif MEGA DB (reference.oppq_practice_locations
joined to reference.oppq_professionals on oppq_id). One clinic = one
(clinic_name, rounded coordinate) group. For each clinic we record the number of
distinct physios and the MOST SENIOR physio (earliest graduation year).

Seniority rule (OPPQ permit numbers, format YYNNN, 5 digits, 2 first = grad year):
  YY <= 26 -> 20YY ; YY >= 73 -> 19YY (clean gap 26-72, verified on the data).
The "oldest" physio is the one with the EARLIEST graduation year. Ranking by raw
numeric permit is WRONG across eras: a 1989 grad (permit 89xxx) is more senior
than a 2006 grad (06xxx), yet 06xxx < 89xxx numerically. So we rank by grad_year
ascending (permit as tiebreak inside a year).

Outputs data/tr_clinics.json: a list of dicts with the CLINICS block fields
(id, name, address, city, postal_code, phone, website, num_physios, num_trps,
specialties, lat, lon, is_physioactif) PLUS oldest_name, oldest_permit,
oldest_grad_year, oldest_experience_years.

Run from the MEGA DB venv (has psql/psycopg2 + db access):
    source ~/.venvs/physioactif-megadb/bin/activate
    python build_tr_clinics.py
"""

import json
import os
import unicodedata
from datetime import datetime
from pathlib import Path

import psycopg2

SCRIPT_DIR = Path(__file__).parent
OUT_FILE = SCRIPT_DIR / "data" / "tr_clinics.json"

# City allowlist for the Trois-Rivières region (same zone as the locaux map:
# Trois-Rivières + Bécancour). OPPQ amalgamates the boroughs (Cap-de-la-Madeleine,
# Trois-Rivières-Ouest, Pointe-du-Lac, ...) under "Trois-Rivières", so they need
# no separate entries. Nicolet/Shawinigan are deliberately excluded.
REGION_CITIES = {"trois-rivieres", "becancour"}

CURRENT_YEAR = datetime.now().year


def _fold(s):
    """Accent/case-fold for tolerant matching."""
    s = (s or "").strip().lower()
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


REGION_CITIES_FOLDED = {_fold(c) for c in REGION_CITIES}


def permit_to_grad_year(permit):
    """OPPQ permit YYNNN -> 4-digit graduation year, or None if unusable.

    YY <= 26 -> 20YY ; YY >= 73 -> 19YY. The 27-72 range never occurs in the
    data (verified), so it returns None defensively.
    """
    if permit is None:
        return None
    s = str(permit).strip()
    if not s.isdigit() or len(s) < 4:
        return None
    yy = int(s[:2])
    if yy <= 26:
        return 2000 + yy
    if yy >= 73:
        return 1900 + yy
    return None


def _check_permit_to_grad_year():
    assert permit_to_grad_year("06336") == 2006, permit_to_grad_year("06336")
    assert permit_to_grad_year("89001") == 1989, permit_to_grad_year("89001")
    assert permit_to_grad_year("25010") == 2025, permit_to_grad_year("25010")
    assert permit_to_grad_year(None) is None
    assert permit_to_grad_year("abc") is None
    print("permit_to_grad_year asserts OK")


def fetch_rows():
    conn = psycopg2.connect(dbname="physioactif")
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.clinic_name, l.address, l.city, l.postal_code, l.phone,
               l.website, l.latitude, l.longitude, l.oppq_id,
               p.full_name, p.permit_number
        FROM reference.oppq_practice_locations l
        JOIN reference.oppq_professionals p ON p.oppq_id = l.oppq_id
        WHERE l.latitude IS NOT NULL AND l.longitude IS NOT NULL
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def aggregate(rows):
    """Group rows into clinics keyed by (folded clinic_name, rounded coord)."""
    clinics = {}
    for (name, address, city, postal, phone, website, lat, lon, oppq_id,
         full_name, permit) in rows:
        if _fold(city) not in REGION_CITIES_FOLDED:
            continue
        lat = float(lat)
        lon = float(lon)
        key = (_fold(name), round(lat, 4), round(lon, 4))
        c = clinics.get(key)
        if c is None:
            c = {
                "name": name or "",
                "address": address or "",
                "city": city or "",
                "postal_code": postal or "",
                "phone": phone or "",
                "website": website or "",
                "lat": lat,
                "lon": lon,
                "_oppq_ids": set(),
                "_physios": [],  # (grad_year, permit_int, permit_str, full_name)
            }
            clinics[key] = c
        c["_oppq_ids"].add(oppq_id)
        grad = permit_to_grad_year(permit)
        if grad is not None:
            c["_physios"].append((grad, int(permit), str(permit).strip(), full_name))

    out = []
    for i, c in enumerate(sorted(clinics.values(), key=lambda x: x["name"]), start=1):
        # Most senior = earliest graduation year; permit as tiebreak within a year.
        physios = sorted(c["_physios"], key=lambda t: (t[0], t[1]))
        if physios:
            grad_year, _permit_int, permit_str, oldest_name = physios[0]
            experience = CURRENT_YEAR - grad_year
        else:
            grad_year = permit_str = oldest_name = None
            experience = None
        out.append({
            "id": i,
            "name": c["name"],
            "address": c["address"],
            "city": c["city"],
            "postal_code": c["postal_code"],
            "phone": c["phone"],
            "website": c["website"],
            "num_physios": len(c["_oppq_ids"]),
            "num_trps": 0,
            "specialties": "",
            "lat": c["lat"],
            "lon": c["lon"],
            "is_physioactif": "physioactif" in (c["name"] or "").lower(),
            "oldest_name": oldest_name,
            "oldest_permit": permit_str,
            "oldest_grad_year": grad_year,
            "oldest_experience_years": experience,
        })
    return out


def main():
    _check_permit_to_grad_year()
    rows = fetch_rows()
    clinics = aggregate(rows)
    os.makedirs(OUT_FILE.parent, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(clinics, f, ensure_ascii=False, indent=2)
    with_oldest = sum(1 for c in clinics if c["oldest_name"])
    print(f"Wrote {OUT_FILE} ({len(clinics)} clinics, {with_oldest} with a known oldest physio)")


if __name__ == "__main__":
    main()
