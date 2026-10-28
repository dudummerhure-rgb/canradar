#!/usr/bin/env python3
"""Liest aktuelle Monster-/Red-Bull-Angebote von kaufDA und schreibt deals.json.

Komplett kostenlos: nur Python-Standardbibliothek, kein API-Key. Läuft über GitHub Actions.

Ablauf:
  1. Preise: kaufDA-Stadtseite je Marke (z. B. /Koeln/Angebote/Red-Bull) -> alle Läden der Stadt.
  2. Gültigkeit: kaufDA-Händlerseite (z. B. /Edeka/Sortiment/Red-Bull) -> Prospekt-Zeitraum.
     Angebote, deren Prospekt erst nächste Woche startet, werden übersprungen.
Sicherheitsnetze: Fehler bei einer Seite lassen die alten, noch gültigen Angebote stehen;
findet das Skript gar nichts Gültiges, wird die Datei NICHT überschrieben.
"""
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

# >>> Hier die Stadt ändern, falls nötig (Schreibweise wie in der kaufDA-Adresse, z. B. Koeln, Bonn, Berlin).
CITY = "Koeln"

OUT = Path(__file__).resolve().parent / "deals.json"
CITY_URL = "https://www.kaufda.de/{city}/Angebote/{brand}"
NATIONAL_URL = "https://www.kaufda.de/Angebote/{brand}"
STORE_URL = "https://www.kaufda.de/{slug}/Sortiment/{brand}"
UA = "Mozilla/5.0 (compatible; CanRadarBot/1.0; privates Hobbyprojekt)"

# (App-Laden, kaufDA-Händlerseite, Namensteile des Ladens in den Angebotstiteln)
STORE_PAGES = [
    ("lidl", "Lidl", ("lidl",)), ("netto", "Netto-Marken-Discount", ("marken",)),
    ("netto", "Netto-Supermarkt", ("scottie", "supermarkt")),
    ("aldi", "Aldi-Sued", ("süd", "sued")), ("aldi", "Aldi-Nord", ("nord",)),
    ("edeka", "Edeka", ("edeka",)), ("kaufland", "Kaufland", ("kaufland",)),
    ("penny", "Penny-Markt", ("penny",)), ("rewe", "REWE", ("rewe",)),
]
BRAND_PAGES = {"monster": "Monster-Energy", "redbull": "Red-Bull"}
BRAND_WORDS = {"monster": ("monster",), "redbull": ("red bull", "redbull")}
STORES = {s for s, _, _ in STORE_PAGES}
SIZES = {"monster": [500, 355, 473, 440, 330], "redbull": [250, 330, 355, 473]}
ALL_SIZES = [250, 330, 355, 440, 473, 500]
# Normalpreis pro Liter, wenn die Seite keinen Streichpreis nennt (Dose 0,5 l = 1,59 / 0,25 l = 1,49)
REF_PER_L = {"monster": 3.18, "redbull": 5.96}
SKIP_WORDS = ("cola", "kalender", "tray", "box", "geschenk")

TAG_IMG = re.compile(r"<img\b[^>]*>", re.I | re.S)
ALT_OFFER = re.compile(r"Angebote? von (.+?) bei (.+?) für (?:ab )?(\d+,\d{2})\s*€", re.S)
OFFER_TITLE = re.compile(r"^(.*?)\s+für\s+(?:ab\s+)?(\d+,\d{2})\s*€\s+bei\s+(.+?)\s+im Angebot\s*$", re.S)
PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")
PERL = [
    re.compile(r"1\s*[lL]\s*=\s*(?:€\s*)?(\d+[.,]\d+)(?:\s*-\s*(?:€\s*)?(\d+[.,]\d+))?"),
    re.compile(r"€\s*(\d+[.,]\d+)\s*/\s*1\s*[lL]()"),
    re.compile(r"(\d+[.,]\d+)\s*/\s*[lL]\b()"),
]
RANGE = re.compile(r"gültig von (\d{2})\.(\d{2})\.(\d{4}) bis (\d{2})\.(\d{2})\.(\d{4})")
RANGE_TXT = re.compile(r"vom (\d{2})\.(\d{2})\.(\d{4}) bis (\d{2})\.(\d{2})\.(\d{4}) gültig")
RANGE_SHORT = re.compile(r"\b\w{2}\. (\d{1,2})\.(\d{1,2})\.\s*-\s*\w{2}\. (\d{1,2})\.(\d{1,2})\.(\d{4})")
PACK = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(ml|l)\b", re.I)


def num(s: str) -> float:
    s = s.strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def d_(day, mon, year) -> dt.date:
    return dt.date(int(year), int(mon), int(day))


def attr(tag: str, name: str) -> str:
    m = re.search(r"""\b%s\s*=\s*(?:"([^"]*)"|'([^']*)')""" % name, tag, re.I | re.S)
    return html.unescape((m.group(1) or m.group(2) or "") if m else "")


def fetch(url: str) -> str:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]*$", " ", s)  # abgeschnittenes Tag am Ende
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def validity(page: str, today: dt.date, subs=()):
    """(start, ende) des aktuell gültigen Prospekts dieses Ladens oder None."""
    ranges, viewer = set(), False
    for tag in TAG_IMG.findall(page):
        if "BROCHURE-BOX-VIEWER" in tag:
            viewer = True
            title = attr(tag, "title")
            r = RANGE.search(title)
            if r and (not subs or any(x in title.lower() for x in subs)):
                ranges.add((d_(*r.group(1, 2, 3)), d_(*r.group(4, 5, 6))))
    if not ranges and not viewer:
        r = RANGE_TXT.search(strip_tags(page))
        if r:
            ranges.add((d_(*r.group(1, 2, 3)), d_(*r.group(4, 5, 6))))
    if not ranges and not viewer:
        for r in RANGE_SHORT.finditer(strip_tags(page)):
            try:
                end = d_(r.group(3), r.group(4), r.group(5))
                start = d_(r.group(1), r.group(2), end.year)
                if start > end:
                    start = d_(r.group(1), r.group(2), end.year - 1)
                ranges.add((start, end))
            except ValueError:
                pass
    now = [x for x in ranges if x[0] <= today <= x[1]]
    return min(now, key=lambda x: x[1]) if now else None


def pack_from_total(brand: str, name: str, total_ml: float):
    m = PACK.search(name)
    if m:
        n = int(m.group(1))
        sz = num(m.group(2)) * (1 if m.group(3).lower() == "ml" else 1000)
        if 1 <= n <= 24 and 150 <= sz <= 1000:
            return int(round(sz)), n
    for ml in SIZES[brand]:
        n = round(total_ml / ml)
        if 1 <= n <= 24 and abs(n * ml - total_ml) / total_ml < 0.03:
            return ml, n
    for ml in ALL_SIZES:  # Einzeldose in üblicher Größe (4 % Toleranz)
        if abs(ml - total_ml) / ml < 0.04:
            return ml, 1
    return None


def parse_offers(page: str, brand: str, log=lambda *a: None):
    """Alle Angebote einer kaufDA-Marken-/Stadtseite (alle Läden)."""
    tags = [(m.start(), m.group(0)) for m in TAG_IMG.finditer(page) if "SEO-OFFER" in m.group(0)]
    log(f"   Seite {len(page)} Zeichen, {len(tags)} Angebotsbilder")
    out = []
    for i, (pos, tag) in enumerate(tags):
        alt, title = attr(tag, "alt"), attr(tag, "title")
        m = ALT_OFFER.search(alt)
        if m:
            name, shop, price_s = m.group(1).strip(), m.group(2).strip(), m.group(3)
        else:
            m = OFFER_TITLE.match(title)
            if not m:
                log(f"   Titel nicht lesbar: {alt[:70]!r} / {title[:70]!r}")
                continue
            name, price_s, shop = m.group(1).strip(), m.group(2), m.group(3).strip()
        label = (alt + " " + title + " " + name).lower()
        if not any(w in label for w in BRAND_WORDS[brand]) or any(w in label for w in SKIP_WORDS):
            continue
        end = tags[i + 1][0] if i + 1 < len(tags) else pos + 8000
        text = strip_tags(page[pos + len(tag): min(end, pos + 8000)])
        text = re.split(r"Mehr (?:Angebote|Prospekte)", text)[0]
        price = num(price_s)
        was = next((p for p in (num(x) for x in PRICE.findall(text)) if p > price + 0.005), None)
        perl = None
        for rx in PERL:
            mm = rx.search(text)
            if mm:
                lo = num(mm.group(1))
                hi = num(mm.group(2)) if mm.group(2) else lo
                perl = (min(lo, hi), max(lo, hi))
                break
        if not perl or perl[0] <= 0:
            log(f"   {shop} {price}: kein Literpreis in {text[:160]!r}")
            continue
        out.append({"shop": shop, "name": name, "price": price, "was": was, "perl": perl})
    return out


def build_items(raws, store, brand, subs, val):
    """Macht aus Roh-Angeboten eines Ladens App-Angebote mit Gültigkeit val=(start,ende)."""
    items = []
    for r in raws:
        if not any(x in r["shop"].lower() for x in subs):
            continue
        lo, hi = r["perl"]
        total_lo = r["price"] / hi * 1000          # kleinste mögliche Gesamtmenge
        total_hi = r["price"] / lo * 1000
        pk = pack_from_total(brand, r["name"], total_lo)
        if not pk:
            print(f"   {r['shop']} {r['price']}: Größe unklar (Gesamtmenge {total_lo:.0f} ml)")
            continue
        ml, n = pk
        item = {"s": store, "b": brand, "ml": ml, "n": n, "price": round(r["price"], 2),
                "was": r["was"], "until": val[1].isoformat()}
        if (total_hi - total_lo) / total_lo > 0.04:
            item["note"] = "Größe je nach Sorte"
        if not item["was"]:
            item["was"] = round(REF_PER_L[brand] * ml * n / 1000, 2)
            item["note"] = "Vergleichspreis"
        items.append(item)
    return items


def clean(items, today: dt.date):
    best = {}
    for d in items:
        try:
            price, was, ml, n = float(d["price"]), float(d["was"]), int(d["ml"]), int(d["n"])
            end = dt.date.fromisoformat(d["until"])
            if d["s"] not in STORES or d["b"] not in BRAND_PAGES:
                continue
            if not (today <= end <= today + dt.timedelta(days=21)):
                continue
            if not (150 <= ml <= 1000 and 1 <= n <= 24 and 0 < price < was):
                continue
            if not (0.8 <= price / (n * ml / 1000) <= 7.0):
                continue
            if not (0.05 <= (was - price) / was <= 0.7):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        item = {k: d[k] for k in ("s", "b", "ml", "n", "price", "was", "until")}
        if d.get("note"):
            item["note"] = str(d["note"])[:80]
        key = (item["s"], item["b"], item["ml"], item["n"])
        if key not in best or item["price"] < best[key]["price"]:
            best[key] = item
    return sorted(best.values(), key=lambda x: (x["s"], x["b"], x["ml"], x["n"]))


def load_offers(brand: str, bslug: str):
    urls = []
    if CITY:
        urls.append(CITY_URL.format(city=CITY, brand=bslug))
    urls.append(NATIONAL_URL.format(brand=bslug))
    last = None
    for url in urls:
        try:
            raws = parse_offers(fetch(url), brand, print)
            print(f"{url}: {len(raws)} Angebot(e) gelesen")
            if raws:
                return raws
        except Exception as e:  # noqa: BLE001
            last = e
            print("FEHLER", e, file=sys.stderr)
    if last:
        raise last
    return []


def main() -> int:
    try:
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo("Europe/Berlin")).date()
    except Exception:  # noqa: BLE001
        today = dt.date.today()
    try:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("deals", [])
    except Exception:  # noqa: BLE001
        old = []
    raws, found, failed = {}, [], set()
    for brand, bslug in BRAND_PAGES.items():
        try:
            raws[brand] = load_offers(brand, bslug)
        except Exception:  # noqa: BLE001
            failed |= {(s, brand) for s in STORES}
        time.sleep(2)
    for store, slug, subs in STORE_PAGES:
        for brand, bslug in BRAND_PAGES.items():
            if brand not in raws:
                continue
            mine = [r for r in raws[brand] if any(x in r["shop"].lower() for x in subs)]
            if not mine:
                continue
            url = STORE_URL.format(slug=slug, brand=bslug)
            try:
                val = validity(fetch(url), today, subs)
            except Exception as e:  # noqa: BLE001
                print("FEHLER", e, file=sys.stderr)
                failed.add((store, brand))
                continue
            finally:
                time.sleep(2)
            print(f"{store}/{brand}: {len(mine)} Angebot(e), Prospekt-Gültigkeit {val}")
            if val:
                found += build_items(raws[brand], store, brand, subs, val)
    keep = [d for d in old if (d.get("s"), d.get("b")) in failed]
    deals = clean(found + keep, today)
    if not raws or not deals:
        print("Nichts Gültiges gefunden – deals.json bleibt unverändert.", file=sys.stderr)
        return 1
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stand": today.isoformat(),
        "deals": deals,
        "empty": {},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(deals)} Angebote geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
