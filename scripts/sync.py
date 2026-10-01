#!/usr/bin/env python3
"""
sync.py — lit les 3 bases Notion du Plan Capital, récupère les cours,
calcule la valeur réelle du PEA et écrit docs/data.json pour la page.

Usage :
  python scripts/sync.py              # sync complet → docs/data.json
  python scripts/sync.py --check-order  # exit 1 si aucun ordre ce mois-ci (pour le rappel)

Variables d'environnement :
  NOTION_TOKEN        (obligatoire) token de l'intégration interne Notion
  NOTION_WRITE_BACK   (optionnel)  "true" → met à jour "PEA valeur titres" du mois courant dans Notion
  DATA_PASSPHRASE     (optionnel)  si défini, écrit docs/data.enc (AES-256-GCM) au lieu de docs/data.json
                                   → le dépôt peut être public, la page demande la phrase de passe
"""
import json
import os
import sys
from datetime import date, datetime, timezone

import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# IDs des bases (page Notion "Plan Capital")
DB_SUIVI = "1f90efd2cd34436eb1f23d15b8cb92dc"
DB_ORDRES = "ee18ae3e8acb41f9a25e3c48f33748f7"
DB_WATCHLIST = "b085c0914e4947bca54f58b4662bc987"

# Support Notion → ticker Yahoo Finance
TICKERS = {
    "WPEA": "WPEA.PA",
    "CW8": "CW8.PA",
    "ESE": "ESE.PA",
}

# Projection 20 → 30 ans (k€) — scénarios de la page Notion
PROJECTION = {
    "ages": list(range(21, 31)),
    "A": [10.1, 13.3, 16.6, 19.9, 23.3, 26.8, 30.3, 34.0, 37.6, 41.4],
    "B": [10.2, 13.6, 19.1, 24.8, 30.9, 37.2, 43.9, 50.9, 58.2, 65.9],
    "C": [10.2, 13.6, 23.9, 34.7, 46.0, 57.9, 70.4, 83.5, 97.3, 111.8],
    "birth_year": 2006,  # 20 ans en septembre 2026
}

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")
OUT_JSON = os.path.join(DOCS, "data.json")
OUT_ENC = os.path.join(DOCS, "data.enc")


def encrypt(plaintext: bytes, passphrase: str) -> dict:
    """AES-256-GCM, clé dérivée par PBKDF2-SHA256 (200 000 itérations). Déchiffrable par Web Crypto."""
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": 200_000, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


# ---------- Notion ----------

def notion_headers():
    if not NOTION_TOKEN:
        sys.exit("NOTION_TOKEN manquant")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def query_db(db_id):
    """Retourne toutes les pages d'une base (pagination gérée)."""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{API}/databases/{db_id}/query", headers=notion_headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows.extend(data["results"])
        if not data.get("has_more"):
            return rows
        cursor = data["next_cursor"]


def prop(page, name):
    """Extrait la valeur simple d'une propriété Notion, quel que soit son type."""
    p = page["properties"].get(name)
    if not p:
        return None
    t = p["type"]
    if t == "title":
        return "".join(x["plain_text"] for x in p["title"])
    if t == "rich_text":
        return "".join(x["plain_text"] for x in p["rich_text"])
    if t == "number":
        return p["number"]
    if t == "select":
        return p["select"]["name"] if p["select"] else None
    if t == "checkbox":
        return p["checkbox"]
    if t == "date":
        return p["date"]["start"] if p["date"] else None
    if t == "formula":
        f = p["formula"]
        return f.get(f["type"])
    return None


def is_template(title):
    return (title or "").upper().startswith("MODÈLE") or (title or "").upper().startswith("MODELE")


# ---------- Cours ----------

def fetch_prices(symbols):
    """Dernier cours via yfinance. Tolérant : un ticker en échec ne bloque pas les autres."""
    out = {}
    try:
        import yfinance as yf
    except ImportError:
        return out
    for key, ticker in symbols.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist):
                last = hist.iloc[-1]
                out[key] = {"price": round(float(last["Close"]), 3), "date": str(hist.index[-1].date())}
        except Exception as e:  # noqa: BLE001
            print(f"[warn] cours {ticker} indisponible : {e}")
    return out


# ---------- Calculs ----------

def build():
    today = date.today()

    # Ordres
    orders = []
    for pg in query_db(DB_ORDRES):
        title = prop(pg, "Ordre")
        if is_template(title):
            continue
        qty = prop(pg, "Quantité") or 0
        px = prop(pg, "Prix unitaire") or 0
        fees = prop(pg, "Frais") or 0
        sens = prop(pg, "Sens") or "Achat"
        sign = 1 if sens == "Achat" else -1
        orders.append({
            "titre": title,
            "date": prop(pg, "Date"),
            "sens": sens,
            "support": prop(pg, "ETF / Titre"),
            "isin": prop(pg, "ISIN"),
            "qty": qty * sign,
            "prix": px,
            "frais": fees,
            "montant": round(qty * px * sign + fees, 2),
        })
    orders.sort(key=lambda o: o["date"] or "")

    holdings = {}
    invested = 0.0
    for o in orders:
        holdings[o["support"]] = holdings.get(o["support"], 0) + o["qty"]
        invested += o["montant"]

    prices = fetch_prices({k: v for k, v in TICKERS.items() if holdings.get(k)} or {"WPEA": TICKERS["WPEA"]})
    pea_value = sum(q * prices.get(s, {}).get("price", 0) for s, q in holdings.items())
    pnl = pea_value - invested

    # Suivi mensuel
    months = []
    for pg in query_db(DB_SUIVI):
        title = prop(pg, "Mois")
        if is_template(title):
            continue
        months.append({
            "id": pg["id"],
            "mois": title,
            "date": prop(pg, "Date"),
            "salaire": prop(pg, "Salaire net") or 0,
            "livretA": prop(pg, "Livret A") or 0,
            "lep": prop(pg, "LEP") or 0,
            "peaCash": prop(pg, "PEA espèces") or 0,
            "peaTitres": prop(pg, "PEA valeur titres") or 0,
            "annexes": prop(pg, "Revenus annexes") or 0,
            "verse": prop(pg, "Versé ce mois") or 0,
            "capital": prop(pg, "Capital total") or 0,
            "taux": prop(pg, "Taux épargne %") or 0,
            "etape": prop(pg, "Étape"),
            "note": prop(pg, "Note"),
        })
    months.sort(key=lambda m: m["date"] or "")

    # Watchlist
    watch = []
    for pg in query_db(DB_WATCHLIST):
        title = prop(pg, "Nom")
        if is_template(title):
            continue
        watch.append({
            "nom": title,
            "type": prop(pg, "Type"),
            "isin": prop(pg, "ISIN"),
            "indice": prop(pg, "Indice / Marché"),
            "frais": prop(pg, "Frais %"),
            "pea": prop(pg, "Éligible PEA"),
            "role": prop(pg, "Rôle"),
            "seuil": prop(pg, "Seuil activation"),
            "verdict": prop(pg, "Verdict"),
            "detenu": prop(pg, "Détenu"),
        })
    role_order = {"Socle": 0, "Satellite": 1, "Apprentissage": 2, "Interdit": 3}
    watch.sort(key=lambda w: role_order.get(w["role"], 9))

    # Ordre du mois courant ?
    ym = today.strftime("%Y-%m")
    order_this_month = any((o["date"] or "").startswith(ym) and o["sens"] == "Achat" for o in orders)

    # Capital "live" = dernier mois saisi, mais PEA titres remplacé par la valeur réelle
    last = months[-1] if months else None
    live_capital = None
    if last:
        live_capital = round(last["livretA"] + last["lep"] + last["peaCash"] + pea_value, 2)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices": prices,
        "pea": {
            "holdings": holdings,
            "value": round(pea_value, 2),
            "invested": round(invested, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / invested * 100, 2) if invested else 0,
        },
        "live_capital": live_capital,
        "order_this_month": order_this_month,
        "months": months,
        "orders": orders,
        "watchlist": watch,
        "projection": PROJECTION,
    }
    return data, last


def write_back(last_month, pea_value):
    """Met à jour 'PEA valeur titres' de la dernière ligne du suivi (optionnel)."""
    if not last_month:
        return
    r = requests.patch(
        f"{API}/pages/{last_month['id']}",
        headers=notion_headers(),
        json={"properties": {"PEA valeur titres": {"number": round(pea_value, 2)}}},
        timeout=30,
    )
    r.raise_for_status()
    print(f"[ok] Notion mis à jour : {last_month['mois']} → PEA valeur titres = {pea_value:.2f} €")


def main():
    data, last = build()

    if "--check-order" in sys.argv:
        if data["order_this_month"]:
            print("[ok] ordre du mois déjà passé")
            sys.exit(0)
        print("[!] aucun ordre d'achat ce mois-ci")
        sys.exit(1)

    os.makedirs(DOCS, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    passphrase = os.environ.get("DATA_PASSPHRASE")
    if passphrase:
        with open(OUT_ENC, "w", encoding="utf-8") as f:
            json.dump(encrypt(payload.encode(), passphrase), f)
        if os.path.exists(OUT_JSON):
            os.remove(OUT_JSON)  # jamais de clair à côté du chiffré
        print(f"[ok] data.enc écrit (chiffré) — PEA {data['pea']['value']:.2f} € ({data['pea']['pnl_pct']:+.2f} %)")
    else:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"[ok] data.json écrit (clair) — PEA {data['pea']['value']:.2f} € ({data['pea']['pnl_pct']:+.2f} %)")

    if os.environ.get("NOTION_WRITE_BACK", "").lower() == "true" and data["pea"]["value"]:
        write_back(last, data["pea"]["value"])


if __name__ == "__main__":
    main()
