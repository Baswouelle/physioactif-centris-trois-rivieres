# Centris Trois-Rivières - Veille des locaux commerciaux

Pipeline de veille des locaux commerciaux Centris ciblé sur **Trois-Rivières et ses alentours proches**. Clone du pipeline Rive-Sud (`baswouelle/physioactif-centris`), région et filtre adaptés. Le projet Rive-Sud reste intact et séparé.

- Repo: **`baswouelle/physioactif-centris-trois-rivieres`** (public, GitHub Pages)
- Carte live: <https://baswouelle.github.io/physioactif-centris-trois-rivieres/>
- Working dir d'itération (hors odrive): `~/repos/physioactif-centris-trois-rivieres`

## API Centris.ca (reverse-engineered)

**Aucune authentification requise.** Appels JSON directs.

| Endpoint | Méthode | Usage |
|----------|---------|-------|
| `/api/property/map/GetMarkers` | POST | Découvrir tous les listings dans une bounding box |
| `/property/GetMarkerInfo` | POST | Preview d'un listing (prix, adresse, MLS, type) |
| Page détail + Schema.org | GET | Détails complets (sqft, description, courtier) |

Rate limiting: `API_DELAY = 0.3s` (markers), `DETAIL_DELAY = 0.5s` (pages détail), 4 threads max. Pas de ban observé.

## Région couverte

Ville amalgamée de Trois-Rivières (inclut Cap-de-la-Madeleine, Trois-Rivières-Ouest, Pointe-du-Lac, St-Louis-de-France) + **Bécancour** sur la rive sud du St-Laurent. Commercial **à vendre ET à louer** (`SELLING_TYPES = ['Rent', 'Sale']`).

### Zones de scan (`AREAS` dans `refresh_centris.py`)

- `trois-rivieres` : NE `{46.42, -72.45}`, SW `{46.27, -72.72}`
- `becancour` : NE `{46.38, -72.30}`, SW `{46.18, -72.58}`
- `trois-rivieres-region` : bbox englobante NE `{46.45, -72.25}`, SW `{46.15, -72.75}`

`SEARCH_AREAS = ['trois-rivieres', 'becancour']`.

### Filtre de région (allowlist, pas exclusion)

Les bounding boxes débordent sur la Mauricie / Centre-du-Québec (Shawinigan, Nicolet, Yamachiche, St-Étienne-des-Grès...). Contrairement à la Rive-Sud, **les deux rives sont désirées**, donc le filtre est une **allowlist d'inclusion** et non une exclusion inter-rive.

- `refresh_centris.py::is_out_of_region(listing)` : drop un listing si sa ville (`city`) n'est pas dans `REGION_CITIES` (comparaison normalisée accents/casse via `_norm_city`, match par préfixe pour couvrir les variantes de quartier comme `Trois-Rivières (Cap-de-la-Madeleine)`).
- `REGION_CITIES` de départ : `{'Trois-Rivières', 'Bécancour'}`. **Calibré empiriquement** sur la distribution des villes au premier scan ; ajouter les variantes de quartier manquantes si elles apparaissent.

## Structure des fichiers (repo)

```
refresh_centris.py                # Scan API + cache incrémental + first_seen + génération carte
send_email.py                     # Gmail API, alerte HTML branding Physioactif (ariel@ + sylvain@)
index.html                        # Carte interactive Leaflet (DATA embarqué, auto-suffisant)
.github/workflows/refresh-centris.yml  # Cron quotidien 0 14 * * * (10h ET)
commercial_latest.json            # Cache d'état (committé à chaque run)
new_listings.json                 # Nouveaux du jour (lu par send_email.py)
```

`index.html` est auto-suffisant : le JSON est embarqué comme variable JS (`const DATA = {...}`), pas de fetch, fonctionne en `file://`.

## Cycle quotidien

1. Cron lance `refresh_centris.py` -> scan API Centris des 2 zones x 2 types.
2. Filtre `is_out_of_region` retire les villes hors région.
3. Listings nouveaux (pas dans le cache) écrits dans `new_listings.json` avec `first_seen = today`.
4. Listings existants gardent leur `first_seen` (badge NOUVEAU 7 jours).
5. Commit `commercial_latest.json + index.html + new_listings.json` sur main.
6. `send_email.py` lit `new_listings.json`, envoie le courriel si > 0 nouveaux. Sujet et en-tête tagués "Trois-Rivières" pour distinguer du pipeline Rive-Sud (mêmes destinataires).

## Amorçage du cache (seed silencieux)

Le cache démarre **vide**. Sans précaution, le premier scan taguerait tous les listings `first_seen = today` -> courriel géant + badges NOUVEAU partout (piège documenté dans `feedback_first_seen_backfill.md`). Procédure de seed : antédater `first_seen = '2020-01-01'` sur tout l'inventaire de départ, supprimer `new_listings.json`, et **ne pas** lancer `send_email.py`. Le cron n'alerte ensuite que sur les vrais nouveaux.

## Secrets GitHub (Gmail API)

`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` (extraits du pickle local `token_gmail.pickle`, scope `gmail.send`). Même compte OAuth que le pipeline Rive-Sud. Pattern réutilisable : mémoire globale `reference_github_actions_gmail_pickle.md`.

## Watchdog

`MEGA DB/automation/watchdog_sync_health.sh` interroge l'API GitHub Actions : seuil 30h depuis le dernier run OU conclusion != success -> alerte ariel@. Endpoint :
`https://api.github.com/repos/baswouelle/physioactif-centris-trois-rivieres/actions/workflows/refresh-centris.yml/runs?per_page=1`

## Deep-link carte

`index.html::openFromUrl()` lit `?mls=<NoMls>` au chargement, centre la carte (zoom 16) et ouvre le popup. Les liens "Voir" du courriel pointent vers `MAP_URL?mls=<NoMls>` (notre carte), pas Centris.

---
*Cloné de `baswouelle/physioactif-centris`. Dernière mise à jour : 2026-06-20.*
