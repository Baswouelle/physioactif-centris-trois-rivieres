#!/usr/bin/env python3
"""Create a Google Sheet of TR-region physio clinics with the most senior physio.

Reads data/tr_clinics.json (produced by build_tr_clinics.py) and creates a new
Google Sheet via the MEGA DB SheetsAPI: one header row + one row per clinic,
sorted by years of experience (descending). Prints the spreadsheetId and URL.

Run from the MEGA DB venv (has google-api-client + the Sheets token):
    source ~/.venvs/physioactif-megadb/bin/activate
    python build_tr_clinics_sheet.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_FILE = SCRIPT_DIR / "data" / "tr_clinics.json"

# The SheetsAPI class + the Sheets OAuth token live in the MEGA DB project.
# Its credentials path is resolved relative to sheets_api.py's own location, so
# importing it from there works regardless of this script's cwd.
MEGADB_DIR = Path(os.environ.get(
    "MEGADB_DIR",
    "/Users/ariel/odrive/Dropbox Cloé/CLOÉ PERSO/À Ariel/Claude code/Physioactif/MEGA DB",
))
sys.path.insert(0, str(MEGADB_DIR))

from scripts.utils.sheets_api import SheetsAPI  # noqa: E402

HEADER = [
    "Clinique", "Adresse", "Ville", "Nb physios",
    "Plus vieux physio", "Permis", "Année graduation", "Années d'expérience",
]


def load_clinics():
    clinics = json.load(open(DATA_FILE, encoding="utf-8"))
    # Sort by experience desc; clinics with no known oldest physio go last.
    clinics.sort(
        key=lambda c: (c["oldest_experience_years"] is not None,
                       c["oldest_experience_years"] or 0),
        reverse=True,
    )
    return clinics


def to_rows(clinics):
    rows = [HEADER]
    for c in clinics:
        rows.append([
            c["name"],
            c["address"],
            c["city"],
            c["num_physios"],
            c["oldest_name"] or "",
            c["oldest_permit"] or "",
            c["oldest_grad_year"] if c["oldest_grad_year"] is not None else "",
            c["oldest_experience_years"] if c["oldest_experience_years"] is not None else "",
        ])
    return rows


def format_sheet(api, ssid, n_rows, n_cols):
    """Brand the sheet: forest-green bold header, frozen header, banded rows,
    auto-sized columns, and a filter so columns are sortable."""
    sheet_id = api.get_sheet_id_by_name(ssid, "Sheet1") or 0
    forest = {"red": 0.141, "green": 0.208, "blue": 0.133}
    mint = {"red": 0.910, "green": 0.937, "blue": 0.925}
    requests = [
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": forest,
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1},
                               "bold": True, "fontSize": 11},
                "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        {"addBanding": {"bandedRange": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": n_cols},
            "rowProperties": {"firstBandColor": {"red": 1, "green": 1, "blue": 1},
                              "secondBandColor": mint}}}},
        {"setBasicFilter": {"filter": {"range": {
            "sheetId": sheet_id, "startRowIndex": 0,
            "startColumnIndex": 0, "endColumnIndex": n_cols}}}},
        {"autoResizeDimensions": {"dimensions": {
            "sheetId": sheet_id, "dimension": "COLUMNS",
            "startIndex": 0, "endIndex": n_cols}}},
    ]
    api._exec(api.sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=ssid, body={"requests": requests}))


def main():
    clinics = load_clinics()
    rows = to_rows(clinics)

    api = SheetsAPI()
    title = f"Cliniques physio Trois-Rivières, {datetime.now():%Y-%m-%d}"
    ssid = api.create_spreadsheet(title)
    api.write_values(ssid, "Sheet1!A1", rows)
    format_sheet(api, ssid, len(rows), len(HEADER))
    url = f"https://docs.google.com/spreadsheets/d/{ssid}/edit"

    # Read-back verification: row count and a sample clinic's oldest physio.
    read_back = api.read_sheet(ssid, "Sheet1!A:H", value_render_option="FORMATTED_VALUE")
    assert len(read_back) == len(clinics) + 1, (
        f"row count {len(read_back)} != {len(clinics) + 1}"
    )
    print(f"spreadsheetId: {ssid}")
    print(f"URL: {url}")
    print(f"Rows: {len(read_back)} ({len(clinics)} clinics + header)")
    # Show a sample for cross-check against tr_clinics.json
    sample = next((c for c in clinics if "CBI Excellence" in c["name"]), clinics[0])
    print(f"Sample: {sample['name']} -> {sample['oldest_name']} "
          f"(permis {sample['oldest_permit']}, {sample['oldest_experience_years']} ans)")


if __name__ == "__main__":
    main()
