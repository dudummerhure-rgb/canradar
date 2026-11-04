#!/usr/bin/env python3
"""Liest aktuelle Monster-/Red-Bull-Angebote von kaufDA und schreibt deals.json (Format 2, mit Regionen).

Komplett kostenlos: nur Python-Standardbibliothek, kein API-Key. Läuft über GitHub Actions.

Ablauf:
  1. Preise: kaufDA-Stadtseite je Region. Die Kategorie-Seite (z. B. /Koeln/Angebote/Energydrink) enthält
     beide Marken mit den Preisen DIESER Stadt. Zusätzlich werden Marken-Seiten der Stadt gelesen, falls es sie gibt.
     Es gibt KEINEN Rückfall auf die Deutschland-Seite: kaufDA zeigt dort Angebote für Berlin (Standort des
     Servers), z. B. Netto Monster 0,88 €, das in Köln gar nicht gilt. Leitet kaufDA eine Stadt-Adresse auf die
     Deutschland-Seite um, wird das erkannt und die Seite verworfen.
  2. Gültigkeit: kaufDA-Händlerseite (z. B. /Edeka/Sortiment/Red-Bull) -> Prospekt-Zeitraum.
     Angebote, deren Prospekt erst nächste Woche startet, werden übersprungen.
  3. Zusammenführen: Ein Angebot, das (fast) überall auftaucht, ist "bundesweit" (kein Feld "r").
     Taucht es nur in manchen Regionen auf, steht in "r" die Liste dieser Regionen. Die App zeigt
     es dann nur dort an, wo der Standort zu einer dieser Regionen passt.
Sicherheitsnetze: Fehler bei einer Seite lassen die alten, noch gültigen Angebote stehen;
sind gar keine Angebote lesbar, werden nur abgelaufene entfernt und der Lauf wird rot.

Neue Region hinzufügen: einfach einen Eintrag in REGIONS ergänzen (slug = Schreibweise in der kaufDA-Adresse).
"""
import datetime as dt
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "deals.json"
CITY_URL = "https://www.kaufda.de/{city}/Angebote/{brand}"
CATEGORY_URL = "https://www.kaufda.de/{city}/Angebote/Energydrink"   # enthält Monster UND Red Bull, mit Preisen der Stadt
STORE_URL = "https://www.kaufda.de/{slug}/Sortiment/{brand}"
UA = "Mozilla/5.0 (compatible; CanRadarBot/1.1; privates Hobbyprojekt)"

PAUSE = 1.5                 # Sekunden Pause zwischen Seitenabrufen (höflich zu kaufDA)
TIME_BUDGET = 8 * 60        # nach so vielen Sekunden wird nichts Neues mehr geladen
MAX_FAILS_IN_ROW = 4        # so viele Fehlschläge hintereinander = kaufDA down, Abbruch
NATIONAL_SHARE = 0.5        # ab diesem Anteil der Regionen gilt ein Angebot als bundesweit (überall = anzeigen)
MIN_REGIONS_FOR_NATIONAL = 5

# Regionen: id (für die App), Name, kaufDA-Schreibweise, Mittelpunkt, Radius in km, in dem der Standort dazugehört.
REGIONS = [
    {"id": "koeln", "name": "Köln", "slug": "Koeln", "lat": 50.9375, "lon": 6.9603, "radius": 30},
    {"id": "bonn", "name": "Bonn", "slug": "Bonn", "lat": 50.7374, "lon": 7.0982, "radius": 20},
    {"id": "aachen", "name": "Aachen", "slug": "Aachen", "lat": 50.7753, "lon": 6.0839, "radius": 25},
    {"id": "duesseldorf", "name": "Düsseldorf", "slug": "Duesseldorf", "lat": 51.2277, "lon": 6.7735, "radius": 25},
    {"id": "essen", "name": "Essen", "slug": "Essen", "lat": 51.4556, "lon": 7.0116, "radius": 25},
    {"id": "dortmund", "name": "Dortmund", "slug": "Dortmund", "lat": 51.5136, "lon": 7.4653, "radius": 25},
    {"id": "frankfurt", "name": "Frankfurt am Main", "slug": "Frankfurt-am-Main", "lat": 50.1109, "lon": 8.6821, "radius": 35},
    {"id": "stuttgart", "name": "Stuttgart", "slug": "Stuttgart", "lat": 48.7758, "lon": 9.1829, "radius": 35},
    {"id": "muenchen", "name": "München", "slug": "Muenchen", "lat": 48.1351, "lon": 11.5820, "radius": 40},
    {"id": "nuernberg", "name": "Nürnberg", "slug": "Nuernberg", "lat": 49.4521, "lon": 11.0767, "radius": 30},
    {"id": "berlin", "name": "Berlin", "slug": "Berlin", "lat": 52.5200, "lon": 13.4050, "radius": 45},
    {"id": "hamburg", "name": "Hamburg", "slug": "Hamburg", "lat": 53.5511, "lon": 9.9937, "radius": 40},
    {"id": "bremen", "name": "Bremen", "slug": "Bremen", "lat": 53.0793, "lon": 8.8017, "radius": 30},
    {"id": "hannover", "name": "Hannover", "slug": "Hannover", "lat": 52.3759, "lon": 9.7320, "radius": 30},
    {"id": "leipzig", "name": "Leipzig", "slug": "Leipzig", "lat": 51.3397, "lon": 12.3731, "radius": 30},
    {"id": "dresden", "name": "Dresden", "slug": "Dresden", "lat": 51.0504, "lon": 13.7373, "radius": 30},
]

# (App-Laden, kaufDA-Händlerseite für die Gültigkeit, Muster für den Ladennamen im Angebot, Muster im Prospekttitel)
STORE_PAGES = [
    ("lidl", "Lidl", (r"\blidl\b",), (r"\blidl\b",)),
    ("netto", "Netto-Marken-Discount", (r"marken",), (r"marken",)),
    ("netto", "Netto-Supermarkt", (r"scottie", r"supermarkt"), (r"scottie", r"supermarkt")),
    ("aldi", "Aldi-Sued", (r"\bs(?:ü|ue)d\b",), (r"\bs(?:ü|ue)d\b",)),
    ("aldi", "Aldi-Nord", (r"\bnord\b",), (r"\bnord\b",)),
    ("edeka", "Edeka", (r"edeka",), (r"edeka", r"e[\s-]?center")),
    ("edeka", "Edeka", (r"e[\s-]?center",), (r"edeka", r"e[\s-]?center")),   # E center gehört zu Edeka
    ("kaufland", "Kaufland", (r"kaufland",), (r"kaufland",)),
    ("penny", "Penny-Markt", (r"penny",), (r"penny",)),
    ("rewe", "REWE", (r"\brewe\b",), (r"\brewe\b",)),
    ("trinkgut", "Trinkgut", (r"trinkgut",), (r"trinkgut",)),
    ("marktkauf", "Marktkauf", (r"marktkauf",), (r"marktkauf",)),
    ("hit", "HIT", (r"\bhit\b",), (r"\bhit\b",)),
    ("mueller", "Mueller", (r"m(?:ü|ue)ller",), (r"m(?:ü|ue)ller",)),
    ("norma", "Norma", (r"\bnorma\b",), (r"\bnorma\b",)),
]
BRAND_PAGES = {"monster": "Monster-Energy", "redbull": "Red-Bull"}
BRAND_RX = {
    "monster": re.compile(r"\bmonster\b", re.I),
    "redbull": re.compile(r"\bred\s?bull\b", re.I),
}
STORES = {s for s, _, _, _ in STORE_PAGES}
SIZES = {"monster": [500, 355, 473, 330], "redbull": [250, 330, 355, 473]}
ALL_SIZES = [250, 330, 355, 473, 500]  # 440 ml gibt es bei Monster/Red Bull in Deutschland nicht
# Normalpreis pro Liter, wenn die Seite keinen Streichpreis nennt (Dose 0,5 l = 1,59 / 0,25 l = 1,49)
REF_PER_L = {"monster": 3.18, "redbull": 5.96}
# Produkte, die nur zufällig "Monster"/"Red Bull" im Namen haben oder keine Einzeldose sind
SKIP_WORDS = ("cola", "kalender", "tray", "box", "geschenk", "munch", "monster high", "mash",
              "spielzeug", "tasse", "shirt", "kappe", "cap ", "becher", "glas", "kühlschrank")

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

_START = time.time()
_fails_in_row = 0


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


class NotFound(RuntimeError):
    """Seite gibt es nicht (404) oder kaufDA hat sie auf eine andere Seite umgeleitet."""


def fetch(url: str, expect: str = "") -> str:
    """Seite laden (3 Versuche). expect = Text, der in der Adresse nach Umleitungen noch stehen muss
    (z. B. der Städtename). Bricht früh ab, wenn kaufDA offensichtlich nicht antwortet."""
    global _fails_in_row
    if time.time() - _START > TIME_BUDGET:
        raise RuntimeError(f"{url}: Zeitbudget aufgebraucht")
    if _fails_in_row >= MAX_FAILS_IN_ROW:
        raise RuntimeError(f"{url}: abgebrochen, kaufDA antwortet nicht")
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                final = r.geturl()
                text = r.read().decode("utf-8", "replace")
            _fails_in_row = 0
            if expect and expect.lower() not in final.lower():
                raise NotFound(f"{url}: umgeleitet nach {final}")
            return text
        except NotFound:
            raise
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                _fails_in_row = 0
                raise NotFound(f"{url}: HTTP {e.code}") from e
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    _fails_in_row += 1
    raise RuntimeError(f"{url}: {last}")


def strip_tags(s: str) -> str:
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]*$", " ", s)  # abgeschnittenes Tag am Ende
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def shop_is(shop: str, patterns) -> bool:
    low = shop.lower()
    return any(re.search(p, low) for p in patterns)


def validity(page: str, today: dt.date, subs=()):
    """(start, ende) des aktuell gültigen Prospekts dieses Ladens oder None."""
    ranges, viewer = set(), False
    for tag in TAG_IMG.findall(page):
        if "BROCHURE-BOX-VIEWER" in tag:
            viewer = True
            title = attr(tag, "title")
            r = RANGE.search(title)
            if r and (not subs or shop_is(title, subs)):
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
    if brand == "monster" and 400 <= total_ml <= 560:
        return 500, 1  # Monster-Dose in Deutschland: 500 ml (ungenaue Literpreise der Seite abfangen)
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
        # Die Marke muss im Produktnamen stehen (nicht nur irgendwo im Bildtext)
        if not BRAND_RX[brand].search(name):
            continue
        label = (alt + " " + title + " " + name).lower()
        if any(w in label for w in SKIP_WORDS):
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


def build_items(raws, store, brand, subs, val, log=print):
    """Macht aus Roh-Angeboten eines Ladens App-Angebote mit Gültigkeit val=(start,ende)."""
    items = []
    for r in raws:
        if not shop_is(r["shop"], subs):
            continue
        lo, hi = r["perl"]
        total_lo = r["price"] / hi * 1000          # kleinste mögliche Gesamtmenge
        total_hi = r["price"] / lo * 1000
        pk = pack_from_total(brand, r["name"], total_lo)
        if not pk:
            log(f"   {r['shop']} {r['price']}: Größe unklar (Gesamtmenge {total_lo:.0f} ml)")
            continue
        ml, n = pk
        item = {"s": store, "b": brand, "ml": ml, "n": n, "price": round(r["price"], 2),
                "was": r["was"], "until": val[1].isoformat(), "sh": r["shop"][:40]}
        if (total_hi - total_lo) / total_lo > 0.04:
            item["note"] = "Größe je nach Sorte"
        if not item["was"]:
            item["was"] = round(REF_PER_L[brand] * ml * n / 1000, 2)
            item["note"] = "Vergleichspreis"
        items.append(item)
    return items


def valid(d, today: dt.date):
    """Prüft ein Angebot auf Plausibilität. Gibt das bereinigte Angebot zurück oder None."""
    try:
        price, was, ml, n = float(d["price"]), float(d["was"]), int(d["ml"]), int(d["n"])
        end = dt.date.fromisoformat(d["until"])
        if d["s"] not in STORES or d["b"] not in BRAND_PAGES:
            return None
        if not (today <= end <= today + dt.timedelta(days=21)):
            return None
        if not (150 <= ml <= 1000 and 1 <= n <= 24 and 0 < price < was):
            return None
        if not (0.8 <= price / (n * ml / 1000) <= 7.0):
            return None
        if not (0.05 <= (was - price) / was <= 0.7):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    item = {k: d[k] for k in ("s", "b", "ml", "n", "price", "was", "until")}
    if d.get("note"):
        item["note"] = str(d["note"])[:80]
    if d.get("sh"):
        item["sh"] = str(d["sh"])[:40]
    r = d.get("r")
    if isinstance(r, list) and r:
        item["r"] = [x for x in r if isinstance(x, str)]
    return item


def clean(items, today: dt.date, log=None):
    """Innerhalb einer Region: je Laden/Marke/Größe nur das günstigste Angebot."""
    best = {}
    for d in items:
        item = valid(d, today)
        if not item:
            if log:
                log(f"   verworfen (unplausibel/abgelaufen): {d.get('s')}/{d.get('b')} {d.get('n')}x{d.get('ml')} ml "
                    f"{d.get('price')} € statt {d.get('was')} bis {d.get('until')}")
            continue
        key = (item["s"], item["b"], item["ml"], item["n"])
        if key not in best or item["price"] < best[key]["price"]:
            best[key] = item
    return sorted(best.values(), key=lambda x: (x["s"], x["b"], x["ml"], x["n"]))


def load_region(region):
    """{Marke: [Angebote]} einer Region. Wirft nur, wenn gar keine Seite der Region lesbar war."""
    slug = region["slug"]
    urls = [CATEGORY_URL.format(city=slug)] + [CITY_URL.format(city=slug, brand=b) for b in BRAND_PAGES.values()]
    pages, errors = [], []
    for url in urls:
        try:
            pages.append((url, fetch(url, expect=slug)))
        except NotFound as e:
            print(f"   (gibt es nicht) {e}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            print("FEHLER", e, file=sys.stderr)
        time.sleep(PAUSE)
    if not pages:
        raise RuntimeError(f"{region['id']}: keine Seite lesbar ({errors[-1] if errors else 'nicht vorhanden'})")
    result = {}
    for brand in BRAND_PAGES:
        seen, offers = set(), []
        for url, page in pages:
            for o in parse_offers(page, brand, lambda *a: print(f"   [{url.split('/')[-1]}]", *a)):
                key = (o["shop"].lower(), o["price"], o["name"].lower())
                if key not in seen:
                    seen.add(key)
                    offers.append(o)
        print(f"{region['id']}/{brand}: {len(offers)} Angebot(e) gelesen")
        result[brand] = offers
    return result


def merge_regions(per_region, active):
    """Fasst die Angebote aller Regionen zusammen. Bundesweit = kein Feld "r"."""
    groups = {}
    for rid, deals in per_region.items():
        for d in deals:
            key = (d["s"], d["b"], d["ml"], d["n"], d["price"], d["was"], d["until"], d.get("note", ""))
            if key not in groups:
                groups[key] = {"deal": d, "regs": set()}
            groups[key]["regs"].add(rid)
    out = []
    can_infer = len(active) >= MIN_REGIONS_FOR_NATIONAL
    for g in groups.values():
        item = {k: v for k, v in g["deal"].items() if k != "r"}
        share = len(g["regs"] & set(active)) / len(active) if active else 0
        if can_infer and share >= NATIONAL_SHARE:
            item.pop("sh", None)  # Ladenname stammt aus einer einzelnen Region, bei bundesweit weglassen
        else:
            item["r"] = sorted(g["regs"])
        out.append(item)
    return sorted(out, key=lambda x: (x["s"], x["b"], x["ml"], x["n"], x["price"], ",".join(x.get("r", []))))


def write_out(deals, today: dt.date) -> None:
    doc = {
        "version": 2,
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stand": today.isoformat(),
        "regions": [{k: r[k] for k in ("id", "name", "lat", "lon", "radius")} for r in REGIONS],
        "deals": deals,
        "empty": {},
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    try:
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo("Europe/Berlin")).date()
    except Exception:  # noqa: BLE001
        today = dt.date.today()
    rids = [r["id"] for r in REGIONS]
    try:
        old_doc = json.loads(OUT.read_text(encoding="utf-8"))
        # Alte Dateien ohne Regionen (Format 1) stammen aus einer ungeprüften Quelle: nicht übernehmen
        old = old_doc.get("deals", []) if old_doc.get("version") == 2 else []
    except Exception:  # noqa: BLE001
        old = []

    # 1) Angebote je Region und Marke lesen
    raws = {rid: {} for rid in rids}
    failed_rb = set()
    for region in REGIONS:
        try:
            raws[region["id"]] = load_region(region)
        except Exception as e:  # noqa: BLE001
            print("FEHLER", e, file=sys.stderr)
            failed_rb |= {(region["id"], b) for b in BRAND_PAGES}
    got_offers = any(raws[rid].get(b) for rid in rids for b in BRAND_PAGES)

    # 2) Gültigkeit je Laden und Marke (einmal pro Laden, nicht pro Region)
    val_cache = {}

    def get_validity(slug, brand, subs):
        k = (slug, brand, subs)
        if k not in val_cache:
            try:
                val_cache[k] = ("ok", validity(fetch(STORE_URL.format(slug=slug, brand=BRAND_PAGES[brand])), today, subs))
            except Exception as e:  # noqa: BLE001
                print("FEHLER", e, file=sys.stderr)
                val_cache[k] = ("fail", None)
            print(f"{slug}/{brand}: Prospekt-Gültigkeit {val_cache[k][1]}" if val_cache[k][0] == "ok"
                  else f"{slug}/{brand}: Gültigkeit nicht lesbar", file=None if val_cache[k][0] == "ok" else sys.stderr)
            time.sleep(PAUSE)
        return val_cache[k]

    found = {rid: [] for rid in rids}
    failed_sb = set()
    if got_offers:
        for region in REGIONS:
            rid = region["id"]
            for brand in BRAND_PAGES:
                if brand not in raws[rid]:
                    continue
                for store, slug, subs, vsubs in STORE_PAGES:
                    mine = [r for r in raws[rid][brand] if shop_is(r["shop"], subs)]
                    if not mine:
                        continue
                    state, val = get_validity(slug, brand, vsubs)
                    if state == "fail":
                        failed_sb.add((store, brand))
                        continue
                    if not val:
                        continue
                    try:
                        items = build_items(mine, store, brand, subs, val)
                    except Exception as e:  # noqa: BLE001
                        print("FEHLER beim Auswerten", rid, store, brand, e, file=sys.stderr)
                        failed_sb.add((store, brand))
                        continue
                    for it in items:
                        print(f"   {rid}: {store}/{brand} {it['n']}x{it['ml']} ml {it['price']} € (laut kaufDA: {it['sh']})")
                    found[rid] += items

    # 3) Nicht lesbare Seiten: alte, noch gültige Angebote behalten
    def old_for(rid):
        res = []
        for d in old:
            r = d.get("r")
            if not r or rid in r:
                res.append({k: v for k, v in d.items() if k != "r"})
        return res

    per_region, active = {}, []
    for rid in rids:
        keep = [d for d in old_for(rid) if (rid, d.get("b")) in failed_rb or (d.get("s"), d.get("b")) in failed_sb]
        per_region[rid] = clean(found[rid] + keep, today, print)
        if any((rid, b) not in failed_rb for b in BRAND_PAGES):
            active.append(rid)

    if not got_offers:
        # kaufDA nicht erreichbar oder Seitenaufbau geändert: alte Angebote behalten,
        # aber abgelaufene entfernen, damit nie ein veraltetes Angebot angezeigt wird.
        kept = [v for v in (valid(d, today) for d in old) if v]
        write_out(kept, today)
        print("Keine Angebote auf kaufDA gelesen (Seite down oder Aufbau geändert) – "
              "nur abgelaufene Angebote entfernt.", file=sys.stderr)
        return 1

    deals = merge_regions(per_region, active)
    write_out(deals, today)

    nat = sum(1 for d in deals if "r" not in d)
    print(f"Regionen mit Daten: {len(active)}/{len(rids)} | Angebote gesamt: {len(deals)} "
          f"(bundesweit {nat}, regional {len(deals) - nat})")
    for rid in rids:
        print(f"   {rid}: {len(per_region[rid])} Angebot(e)")
    if not deals:
        # z. B. Sonntag: alte Prospekte abgelaufen, neue starten erst Montag -> kein Fehler
        print("Aktuell keine gültigen Angebote – Liste ist leer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
