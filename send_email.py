#!/usr/bin/env python3
"""
Email alert pour les nouveaux listings Centris.

Lu par refresh_centris.py: si new_listings.json existe, envoie un email
HTML aux destinataires via Gmail API (OAuth refresh token).

Variables d'environnement requises (GitHub Secrets):
    GMAIL_CLIENT_ID
    GMAIL_CLIENT_SECRET
    GMAIL_REFRESH_TOKEN

Exit 0 silencieux si new_listings.json absent ou vide.
"""

import base64
import json
import logging
import os
import re
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
NEW_LISTINGS_FILE = SCRIPT_DIR / 'new_listings.json'

SENDER = 'ariel@physioactif.com'
RECIPIENTS = ['ariel@physioactif.com', 'sylvain@physioactif.com']
MAP_URL = 'https://baswouelle.github.io/physioactif-centris-trois-rivieres/'

TOKEN_URI = 'https://oauth2.googleapis.com/token'
GMAIL_SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'


def get_access_token() -> str:
    client_id = os.environ['GMAIL_CLIENT_ID']
    client_secret = os.environ['GMAIL_CLIENT_SECRET']
    refresh_token = os.environ['GMAIL_REFRESH_TOKEN']
    resp = requests.post(TOKEN_URI, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()['access_token']


def fmt_price(listing: dict) -> str:
    display = listing.get('price_display')
    if display:
        return display
    pv = listing.get('price_value')
    if pv:
        try:
            return f"{float(pv):,.0f} $".replace(',', ' ')
        except (TypeError, ValueError):
            return str(pv)
    return '-'


def fmt_sqft(listing: dict) -> str:
    sqft = listing.get('sqft')
    if sqft:
        return f"{sqft:,} pi²".replace(',', ' ')
    return '-'


def map_link(listing: dict) -> str:
    """Deep-link to the listing's fiche on our own map (not Centris).
    index.html reads ?mls=<NoMls> and opens that marker's popup."""
    mls = listing.get('mls_number') or ''
    return f"{MAP_URL}?mls={mls}" if mls else MAP_URL


def split_addr(addr: str) -> tuple:
    """Split a Centris address into (building_label, suite_label).

    Address shape: 'Montérégie, 365, Rue Saint-Jean, local 102'
      -> building '365, Rue Saint-Jean', suite 'local 102'
    Drops the leading region/city token and the trailing 'local ...' suffix.
    """
    parts = [p.strip() for p in (addr or '').split(',') if p.strip()]
    suite = ''
    if parts and re.match(r'(?i)^local\b', parts[-1]):
        suite = parts[-1]
        parts = parts[:-1]
    # Drop a leading non-numeric token (region/city like 'Montérégie')
    if len(parts) > 1 and not re.search(r'\d', parts[0]):
        parts = parts[1:]
    building = ', '.join(parts) if parts else (addr or '?')
    return building, suite


def fresh_row(l: dict) -> str:
    addr = l.get('address') or l.get('title') or '?'
    city = l.get('area_label') or l.get('city') or ''
    cat = l.get('category') or 'Commercial'
    tx = 'Location' if l.get('transaction_type') == 'lease' else 'Vente'
    url = map_link(l)
    return f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #e8efec;font-family:monospace;font-size:12px;">{l.get('mls_number', '')}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e8efec;">
                <div style="font-weight:600;color:#243522;">{addr}</div>
                <div style="font-size:11px;color:#6e6724;">{city}</div>
              </td>
              <td style="padding:8px 12px;border-bottom:1px solid #e8efec;font-size:12px;">
                <div>{cat}</div>
                <div style="font-size:11px;color:#9ba491;">{tx}</div>
              </td>
              <td style="padding:8px 12px;border-bottom:1px solid #e8efec;font-weight:600;color:#243522;">{fmt_price(l)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e8efec;color:#6e6724;">{fmt_sqft(l)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e8efec;"><a href="{url}" style="color:#243522;text-decoration:underline;">Carte &rarr;</a></td>
            </tr>
        """


def build_known_section(known: list) -> str:
    """Compact, grouped-by-building section for new suites in buildings we already
    track. Demoted below the genuinely new buildings."""
    if not known:
        return ''

    groups: dict = {}
    order: list = []
    for l in known:
        building, suite = split_addr(l.get('address'))
        key = building.lower()
        if key not in groups:
            groups[key] = {'building': building,
                           'city': l.get('area_label') or l.get('city') or '',
                           'items': []}
            order.append(key)
        groups[key]['items'].append((suite, l))

    blocks = []
    for key in order:
        g = groups[key]
        suites = []
        for suite, l in g['items']:
            tx = 'Location' if l.get('transaction_type') == 'lease' else 'Vente'
            url = map_link(l)
            label = (suite[:1].upper() + suite[1:]) if suite else f"MLS {l.get('mls_number', '')}"
            bits = [b for b in (fmt_sqft(l) if l.get('sqft') else '',
                                fmt_price(l) if (l.get('price_display') or l.get('price_value')) else '',
                                tx) if b]
            suites.append(
                f'<li style="margin:2px 0;font-size:12px;color:#3a4a38;">'
                f'<strong style="color:#243522;">{label}</strong>'
                f'<span style="color:#6e6724;"> &middot; {" &middot; ".join(bits)}</span>'
                f' &middot; <a href="{url}" style="color:#243522;">Voir sur la carte &rarr;</a></li>'
            )
        blocks.append(
            f'<div style="margin:0 0 12px 0;padding:10px 12px;background:#f4f7f5;border-radius:8px;">'
            f'<div style="font-weight:600;color:#243522;font-size:13px;">{g["building"]}'
            f'<span style="font-weight:400;color:#9ba491;font-size:11px;"> &middot; {g["city"]}</span></div>'
            f'<ul style="margin:6px 0 0 0;padding-left:18px;">{"".join(suites)}</ul>'
            f'</div>'
        )

    n_loc = len(known)
    n_imm = len(order)
    return f"""
      <div style="margin-top:28px;border-top:2px solid #e8efec;padding-top:18px;">
        <div style="font-size:14px;font-weight:600;color:#6e6724;margin:0 0 4px 0;">
          Nouveaux locaux dans des immeubles d&eacute;j&agrave; suivis
        </div>
        <p style="font-size:12px;color:#9ba491;margin:0 0 14px 0;">
          {n_loc} {'locaux' if n_loc > 1 else 'local'} dans {n_imm} immeuble{'s' if n_imm > 1 else ''} d&eacute;j&agrave; pr&eacute;sent{'s' if n_imm > 1 else ''} dans la liste (souvent une autre suite, ou une refonte de fiche par le courtier).
        </p>
        {''.join(blocks)}
      </div>"""


def immeuble_phrase(n: int) -> str:
    """French-correct: 'nouvel immeuble' (sing.), 'nouveaux immeubles' (plur.)."""
    return f"{n} nouvel immeuble" if n <= 1 else f"{n} nouveaux immeubles"


def build_html(new_listings: list, search_date: str) -> str:
    date_str = search_date[:10] if search_date else datetime.now().date().isoformat()

    fresh = [l for l in new_listings if not l.get('building_seen_before')]
    known = [l for l in new_listings if l.get('building_seen_before')]

    nf = len(fresh)
    plural = 's' if nf > 1 else ''

    if fresh:
        rows_html = ''.join(fresh_row(l) for l in fresh)
        fresh_block = f"""
      <p style="font-size:14px;color:#243522;margin:0 0 16px 0;">
        {immeuble_phrase(nf)} apparu{plural} sur Centris depuis le dernier scan&nbsp;:
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:13px;color:#243522;">
        <thead>
          <tr style="background:#ddc96a;color:#243522;">
            <th style="padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;">MLS</th>
            <th style="padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;">Adresse</th>
            <th style="padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;">Cat&eacute;gorie</th>
            <th style="padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;">Prix</th>
            <th style="padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;">Superficie</th>
            <th style="padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase;">Lien</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>"""
    else:
        fresh_block = """
      <p style="font-size:14px;color:#243522;margin:0 0 16px 0;">
        Aucun nouvel immeuble jamais vu aujourd&apos;hui. Seulement de nouveaux locaux dans des immeubles d&eacute;j&agrave; suivis&nbsp;:
      </p>"""

    known_block = build_known_section(known)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#e8efec;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:760px;margin:0 auto;background:#fff;">
    <div style="background:#243522;color:#ddc96a;padding:20px 24px;">
      <div style="font-size:20px;font-weight:600;">Physioactif &ndash; Veille Centris Trois-Rivières</div>
      <div style="font-size:13px;color:#9ba491;margin-top:4px;">{date_str} &middot; {immeuble_phrase(nf)}{f' &middot; {len(known)} {"locaux" if len(known) > 1 else "local"} d&eacute;j&agrave; suivi{"s" if len(known) > 1 else ""}' if known else ''}</div>
    </div>
    <div style="padding:20px 24px;">
      {fresh_block}
      {known_block}
      <p style="margin:20px 0 0 0;font-size:13px;">
        <a href="{MAP_URL}" style="display:inline-block;background:#ddc96a;color:#243522;padding:10px 20px;border-radius:50px;text-decoration:none;font-weight:600;">Ouvrir la carte interactive &rarr;</a>
      </p>
    </div>
    <div style="background:#e8efec;color:#6e6724;padding:12px 24px;font-size:11px;text-align:center;">
      Alerte g&eacute;n&eacute;r&eacute;e automatiquement par refresh-centris (GitHub Actions).
    </div>
  </div>
</body>
</html>"""


def send_via_gmail(access_token: str, html_body: str, subject: str) -> None:
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SENDER
    msg['To'] = ', '.join(RECIPIENTS)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
    resp = requests.post(
        GMAIL_SEND_URL,
        headers={'Authorization': f'Bearer {access_token}',
                 'Content-Type': 'application/json'},
        json={'raw': raw},
        timeout=30,
    )
    resp.raise_for_status()
    logger.info(f"Sent: id={resp.json().get('id')} to={msg['To']}")


def main() -> int:
    if not NEW_LISTINGS_FILE.exists():
        logger.info('No new_listings.json - nothing to send')
        return 0

    with open(NEW_LISTINGS_FILE, encoding='utf-8') as f:
        data = json.load(f)

    new_listings = data.get('listings', [])
    if not new_listings:
        logger.info('new_listings.json is empty - nothing to send')
        return 0

    nf = sum(1 for l in new_listings if not l.get('building_seen_before'))
    nk = len(new_listings) - nf
    date_str = (data.get('search_date') or datetime.now().isoformat())[:10]
    head = f"{nf} nouvel immeuble" if nf <= 1 else f"{nf} nouveaux immeubles"
    if nk:
        head += f" (+{nk} {'locaux' if nk > 1 else 'local'} déjà suivi{'s' if nk > 1 else ''})"
    subject = f"Centris Trois-Rivières: {head} - {date_str}"

    access_token = get_access_token()
    html_body = build_html(new_listings, data.get('search_date', ''))
    send_via_gmail(access_token, html_body, subject)
    return 0


if __name__ == '__main__':
    sys.exit(main())
