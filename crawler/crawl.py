"""Builds scores.json for Guess the Score.

1. Picks the most-voted movies from IMDb's official datasets (also gives the IMDb rating).
2. Finds each movie on Rotten Tomatoes and reads its critic + audience scores.
Runs weekly from GitHub Actions. Python standard library only.
"""
import csv, gzip, html, io, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request

TOP_N = int(os.environ.get("TOP_N", "500"))
OUT = "scores.json"
UA = "Mozilla/5.0 (compatible; guess-the-score-bot/1.0; small personal game)"
PAUSE = 1.5  # seconds between Rotten Tomatoes requests

# Movies the game already lists that may fall outside the top 500
EXTRAS = [("Anchorman: The Legend of Ron Burgundy", 2004), ("A Christmas Story", 1983), ("Snow White and the Seven Dwarfs", 1937),
          ("The Wizard of Oz", 1939), ("Napoleon Dynamite", 2004), ("Talladega Nights: The Ballad of Ricky Bobby", 2006),
          ("Elf", 2003), ("Mean Girls", 2004), ("Step Brothers", 2008), ("Friday", 1995), ("Bad Boys", 1995),
          ("White Chicks", 2004), ("Scary Movie", 2000), ("The Waterboy", 1998), ("Happy Gilmore", 1996),
          ("Hocus Pocus", 1993), ("The Polar Express", 2004), ("How the Grinch Stole Christmas", 2000),
          ("Night at the Museum", 2006), ("Space Jam", 1996), ("Remember the Titans", 2000), ("Creed", 2015),
          ("Rush Hour", 1998), ("Madagascar", 2005), ("Ice Age", 2002), ("Cars", 2006), ("Twilight", 2008),
          ("The Notebook", 2004), ("Love Actually", 2003), ("Bridesmaids", 2011), ("21 Jump Street", 2012),
          ("Barbie", 2023), ("Coraline", 2009), ("Beetlejuice", 1988), ("Halloween", 1978), ("Us", 2019),
          ("Jumanji", 1995), ("Grease", 1978), ("The Breakfast Club", 1985), ("The Conjuring", 2013), ("It", 2017)]


def get(url, binary=False, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace")
        except Exception as e:
            print(f"  retry {i+1} {url}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def imdb_top():
    print("Downloading IMDb ratings…")
    ratings = {}
    raw = get("https://datasets.imdbws.com/title.ratings.tsv.gz", binary=True)
    for row in csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), "utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
        v = int(row["numVotes"])
        if v >= 50000:
            ratings[row["tconst"]] = (float(row["averageRating"]), v)
    print(f"  {len(ratings)} titles with 50k+ votes")
    print("Downloading IMDb titles…")
    movies = []
    req = urllib.request.Request("https://datasets.imdbws.com/title.basics.tsv.gz", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        for row in csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=r), "utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
            t = row["tconst"]
            if t in ratings and row["titleType"] == "movie" and row["startYear"].isdigit():
                movies.append({"imdb": t, "t": row["primaryTitle"], "y": int(row["startYear"]), "i": ratings[t][0], "votes": ratings[t][1]})
    movies.sort(key=lambda m: -m["votes"])
    return movies


def rt_search(title, year):
    q = urllib.parse.quote(title)
    page = get(f"https://www.rottentomatoes.com/search?search={q}")
    time.sleep(PAUSE)
    if not page:
        return None
    sec = re.search(r'<search-page-result[^>]*type="movie"[^>]*>([\s\S]*?)</search-page-result>', page)
    if not sec:
        return None
    best = None
    for m in re.finditer(r"<search-page-media-row([^>]*)>([\s\S]*?)</search-page-media-row>", sec.group(1)):
        attrs, body = m.group(1), m.group(2)
        ym = re.search(r'release-year="(\d{4})"', attrs)
        href = re.search(r'href="https://www\.rottentomatoes\.com(/m/[^"]+)"', body)
        alt = re.search(r'alt="([^"]*)"', body)
        if not (ym and href):
            continue
        y = int(ym.group(1))
        name = html.unescape(alt.group(1)) if alt else ""
        score = (abs(y - year) <= 1) * 2 + (norm(name) == norm(title)) * 3 - abs(y - year) * 0.1
        if abs(y - year) <= 1 and (best is None or score > best[0]):
            best = (score, href.group(1))
    return best[1] if best else None


def rt_scores(slug):
    page = get("https://www.rottentomatoes.com" + slug)
    time.sleep(PAUSE)
    if not page:
        return None
    m = re.search(r'<script[^>]*id="media-scorecard-json"[^>]*>([\s\S]*?)</script>', page)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None
    pct = lambda o: int(o["scorePercent"].rstrip("%")) if o and str(o.get("scorePercent", "")).rstrip("%").isdigit() else None
    return pct(d.get("criticsScore")), pct(d.get("audienceScore"))


def main():
    old = {}
    if os.path.exists(OUT):
        for m in json.load(open(OUT)).get("movies", []):
            old[(norm(m["t"]), m["y"])] = m
    allm = imdb_top()
    top = allm[:TOP_N]
    have = {(norm(m["t"]), m["y"]) for m in top}
    by_key = {(norm(m["t"]), m["y"]): m for m in allm}
    for t, y in EXTRAS:
        k = (norm(t), y)
        if k not in have:
            top.append(by_key.get(k) or {"imdb": None, "t": t, "y": y, "i": None})
            have.add(k)
    out, ok = [], 0
    for n, m in enumerate(top, 1):
        k = (norm(m["t"]), m["y"])
        slug = (old.get(k) or {}).get("slug") or rt_search(m["t"], m["y"])
        c = a = None
        if slug:
            s = rt_scores(slug)
            if s:
                c, a = s
        if c is None and a is None and k in old:  # keep last good numbers if a fetch failed
            c, a = old[k].get("c"), old[k].get("a")
        ok += c is not None and a is not None
        out.append({"t": m["t"], "y": m["y"], "c": c, "a": a, "i": m.get("i"), "imdb": m.get("imdb"), "slug": slug})
        print(f"{n:4} {m['t']} ({m['y']}): C {c} A {a} IMDb {m.get('i')}")
    json.dump({"updated": time.strftime("%Y-%m-%d"), "count": len(out), "complete": ok, "movies": out},
              open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"Done: {ok}/{len(out)} movies with both scores")


if __name__ == "__main__":
    main()
