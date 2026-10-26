#!/usr/bin/env python3
"""Liest aktuelle Monster-/Red-Bull-Angebote von kaufDA-Händlerseiten und schreibt data/deals.json.

Komplett kostenlos: nur Python-Standardbibliothek, kein API-Key. Läuft über GitHub Actions.
Sicherheitsnetze:
  * Eine Seite, die nicht ladbar oder nicht lesbar ist, lässt die alten Angebote dieses
    Ladens/dieser Marke stehen (solange sie noch gültig sind).
  * Findet das Skript insgesamt nichts Gültiges, wird die Datei NICHT überschrieben.
"""
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "deals.json"
BASE = "https://www.kaufda.de/{slug}/Sortiment/{brand}"
UA = "Mozilla/5.0 (compatible; CanRadarBot/1.0; privates Hobbyprojekt)"

# App-Laden -> kaufDA-Seiten (Aldi/Netto haben zwei Seiten)
STORE_PAGES = [
    ("lidl", "Lidl"), ("netto", "Netto-Marken-Discount"), ("netto", "Netto-Supermarkt"),
    ("aldi", "Aldi-Sued"), ("aldi", "Aldi-Nord"), ("edeka", "Edeka"),
    ("kaufland", "Kaufland"), ("penny", "Penny-Markt"), ("rewe", "REWE"),
]
BRAND_PAGES = {"monster": "Monster-Energy", "redbull": "Red-Bull"}
# Wie heißt der Laden im Angebotstitel? (kleingeschrieben, "enthält")
STORE_MATCH = {"lidl": "lidl", "netto": "netto", "aldi": "aldi", "edeka": "edeka",
               "kaufland": "kaufland", "penny": "penny", "rewe": "rewe"}
SIZES = {"monster": [500, 355, 473, 440, 330], "redbull": [250, 330, 355, 473]}
ALL_SIZES = [250, 330, 355, 440, 473, 500]
# Normalpreis pro Liter, wenn die Seite keinen Streichpreis nennt (Dose 0,5 l = 1,59 / 0,25 l = 1,49)
REF_PER_L = {"monster": 3.18, "redbull": 5.96}
SKIP_WORDS = ("cola", "kalender", "tray", "box", "geschenk")

TAG_IMG = re.compile(r"<img\b[^>]*>", re.I | re.S)
TITLE = re.compile(r"""\btitle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I | re.S)
OFFER_TITLE = re.compile(r"^(.*?)\s+für\s+(?:ab\s+)?(\d+,\d{2})\s*€\s+bei\s+(.+?)\s+im Angebot\s*$", re.S)
PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")
PERL = [
    re.compile(r"1\s*[lL]\s*=\s*(\d+[.,]\d+)"),
    re.compile(r"€\s*(\d+[.,]\d+)\s*/\s*1\s*[lL]"),
    re.compile(r"(\d+[.,]\d+)\s*/\s*[lL]\b"),
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
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def validity(page: str, today: dt.date):
    """Gibt (start, ende) der aktuell gültigen Prospekt-Zeit zurück oder None."""
    ranges = set()
    for tag in TAG_IMG.findall(page):
        if "BROCHURE-BOX-VIEWER" in tag:
            m = TITLE.search(tag)
            r = RANGE.search(html.unescape((m.group(1) or m.group(2)) if m else ""))
            if r:
                ranges.add((d_(*r.group(1, 2, 3)), d_(*r.group(4, 5, 6))))
    if not ranges:
        r = RANGE_TXT.search(strip_tags(page))
        if r:
            ranges.add((d_(*r.group(1, 2, 3)), d_(*r.group(4, 5, 6))))
    if not ranges:
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


def parse_page(page: str, store: str, brand: str, today: dt.date, log=lambda *a: None):
    """Liefert die gültigen Angebote dieses Ladens auf der Seite (Liste von dicts)."""
    tags = [(m.start(), m.group(0)) for m in TAG_IMG.finditer(page) if "SEO-OFFER" in m.group(0)]
    val = validity(page, today)
    log(f"   Seite {len(page)} Zeichen, {len(tags)} Angebotsbilder, Gültigkeit: {val}")
    if not val:
        return []
    until = val[1]
    out = []
    for i, (pos, tag) in enumerate(tags):
        t = TITLE.search(tag)
        if not t:
            continue
        title = html.unescape(t.group(1) or t.group(2))
        m = OFFER_TITLE.match(title)
        if not m:
            log(f"   Titel nicht lesbar: {title[:90]!r}")
            continue
        name, price_s, shop = m.group(1).strip(), m.group(2), m.group(3).strip()
        if STORE_MATCH[store] not in shop.lower() or any(w in name.lower() for w in SKIP_WORDS):
            continue
        end = tags[i + 1][0] if i + 1 < len(tags) else pos + 1500
        text = strip_tags(page[pos + len(tag): min(end, pos + 1500)])
        text = re.split(r"Mehr (?:Angebote|Prospekte)", text)[0]
        price = num(price_s)
        prices = [num(p) for p in PRICE.findall(text)]
        was = next((p for p in prices if p > price + 0.005), None)
        perl = None
        for rx in PERL:
            mm = rx.search(text)
            if mm:
                perl = num(mm.group(1))
                break
        if not perl or perl <= 0:
            log(f"   {name} {price}: kein Literpreis in {text[:120]!r}")
            continue  # ohne Literpreis ist Größe/Anzahl nicht sicher bestimmbar
        pk = pack_from_total(brand, name, price / perl * 1000)
        if not pk:
            log(f"   {name} {price}: Größe unklar (Gesamtmenge {price / perl * 1000:.0f} ml)")
            continue
        ml, n = pk
        item = {"s": store, "b": brand, "ml": ml, "n": n, "price": round(price, 2),
                "was": None, "until": until.isoformat()}
        if was:
            item["was"] = round(was, 2)
        else:
            item["was"] = round(REF_PER_L[brand] * ml * n / 1000, 2)
            item["note"] = "Vergleichspreis"
        out.append(item)
    return out


def clean(items, today: dt.date):
    best = {}
    for d in items:
        try:
            price, was, ml, n = float(d["price"]), float(d["was"]), int(d["ml"]), int(d["n"])
            end = dt.date.fromisoformat(d["until"])
            if d["s"] not in STORE_MATCH or d["b"] not in BRAND_PAGES:
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
    found, failed, ok_pages = [], set(), 0
    for store, slug in STORE_PAGES:
        for brand, bslug in BRAND_PAGES.items():
            url = BASE.format(slug=slug, brand=bslug)
            try:
                page = fetch(url)
                got = parse_page(page, store, brand, today, print)
                ok_pages += 1
                print(f"{url}: {len(got)} Angebot(e)")
                found += got
            except Exception as e:  # noqa: BLE001
                print("FEHLER", e, file=sys.stderr)
                failed.add((store, brand))
            time.sleep(2)
    # Bei fehlgeschlagenen Seiten die alten (noch gültigen) Angebote behalten
    keep = [d for d in old if (d.get("s"), d.get("b")) in failed]
    deals = clean(found + keep, today)
    if ok_pages == 0 or not deals:
        print("Nichts Gültiges gefunden – deals.json bleibt unverändert.", file=sys.stderr)
        return 1
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stand": today.isoformat(),
        "deals": deals,
        "empty": {},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(deals)} Angebote geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
