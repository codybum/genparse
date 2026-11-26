"""Second source for the audit: what Wikidata and Wikipedia record for the same people.

    python3 audit/fetch_wikidata.py audit/cache master.json

Wikidata stores each person's Mathematics Genealogy Project id (property P549),
so the join is exact. For every person in master.json and every cached project
record this saves, to audit/cache/wikidata.json:

    item          the person's Wikidata item
    wikiUrl       their Wikipedia page, English when there is one
    wikiImage     their portrait
    description   a one-line description
    sitelinks     how many wiki pages exist about them, across languages and projects
    born, died    years of birth and death, used to test that the match is the right person
    advisors      the advisors Wikidata lists (doctoral advisor P184, student of P1066)
    file_page     what the wikiUrl now in master.json actually points to
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mgplib  # noqa: E402

ENDPOINT = "https://query.wikidata.org/sparql"

# When a person has no English page, take the first of these that exists.
LANGUAGES = ["en", "de", "fr", "it", "nl", "pl", "es", "la", "ru", "cs", "pt", "el", "tr", "fa", "ar"]

PEOPLE = """
SELECT ?mgp ?item ?image ?links ?description ?born ?died WHERE {
  VALUES ?mgp { %s }
  ?item wdt:P549 ?mgp ; wikibase:sitelinks ?links .
  OPTIONAL { ?item wdt:P18 ?image . }
  OPTIONAL { ?item wdt:P569 ?birth . BIND(YEAR(?birth) AS ?born) }
  OPTIONAL { ?item wdt:P570 ?death . BIND(YEAR(?death) AS ?died) }
  OPTIONAL { ?item schema:description ?description . FILTER(LANG(?description) = "en") }
}"""

PAGES = """
SELECT ?mgp ?article ?lang WHERE {
  VALUES ?mgp { %s }
  ?item wdt:P549 ?mgp .
  ?article schema:about ?item ; schema:isPartOf ?site ; schema:inLanguage ?lang .
  ?site wikibase:wikiGroup "wikipedia" .
  FILTER(?lang IN (%s))
}"""

ADVISORS = """
SELECT ?mgp ?prop ?advisor ?advisorLabel ?advisorMgp WHERE {
  VALUES ?mgp { %s }
  ?item wdt:P549 ?mgp .
  { ?item wdt:P184 ?advisor . BIND("doctoral advisor" AS ?prop) }
  UNION
  { ?item wdt:P1066 ?advisor . BIND("student of" AS ?prop) }
  OPTIONAL { ?advisor wdt:P549 ?advisorMgp . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}"""


def get_json(url, data=None):
    """GET or POST, waiting and retrying when the server says it is busy."""
    for attempt in range(6):
        request = urllib.request.Request(url, data=data, headers={
            "User-Agent": mgplib.USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 5:
                raise
            wait = error.headers.get("Retry-After", "")
            time.sleep(min(int(wait) if wait.isdigit() else 30 * (attempt + 1), 300))


def sparql(query):
    body = urllib.parse.urlencode({"query": query, "format": "json"}).encode()
    return get_json(ENDPOINT, body)["results"]["bindings"]


def title_of(url):
    """Return (host, page title) for a Wikipedia address, or (None, None)."""
    parts = urllib.parse.urlsplit(url)
    if not parts.netloc.endswith(".wikipedia.org") or "/wiki/" not in parts.path:
        return None, None
    title = urllib.parse.unquote(parts.path.split("/wiki/", 1)[1]).replace("_", " ").strip()
    return parts.netloc, title


def same_title_other_case(host, title):
    """Find a page whose title equals this one apart from capital letters."""
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": title,
        "gsrlimit": 5, "prop": "pageprops", "ppprop": "wikibase_item"})
    time.sleep(1)
    answer = get_json("https://{}/w/api.php?{}".format(host, query)).get("query", {})
    wanted = title.replace("_", " ").casefold()
    for page in answer.get("pages", {}).values():
        if page["title"].casefold() == wanted:
            return page
    return None


def resolve_pages(urls):
    """For each Wikipedia address say which page and which Wikidata item it leads to."""
    by_host, result = {}, {}
    for url in urls:
        host, title = title_of(url)
        if host is None:
            result[url] = {"url": url, "status": "not a Wikipedia page"}
        else:
            by_host.setdefault(host, []).append((url, title))
    for host, pairs in by_host.items():
        for start in range(0, len(pairs), 40):
            chunk = pairs[start:start + 40]
            query = urllib.parse.urlencode({
                "action": "query", "format": "json", "redirects": 1, "prop": "pageprops",
                "ppprop": "wikibase_item", "titles": "|".join(t for _, t in chunk)})
            answer = get_json("https://{}/w/api.php?{}".format(host, query)).get("query", {})
            step = {}
            for kind in ("normalized", "redirects"):
                for hop in answer.get(kind, []):
                    step[hop["from"]] = hop["to"]
            pages = {p["title"]: p for p in answer.get("pages", {}).values()}
            for url, title in chunk:
                final, hops = title, 0
                while final in step and hops < 5:
                    final, hops = step[final], hops + 1
                page = pages.get(final)
                if page is None or "missing" in page or "invalid" in page:
                    page = same_title_other_case(host, title)
                    if page is None:
                        result[url] = {"url": url, "status": "no such page"}
                        continue
                    final = page["title"]
                item = page.get("pageprops", {}).get("wikibase_item", "")
                result[url] = {
                    "url": url, "status": "found",
                    "leads_to": "https://{}/wiki/{}".format(host, final.replace(" ", "_")),
                    "item": "http://www.wikidata.org/entity/" + item if item else ""}
            time.sleep(1)
    return result


def main():
    cache_dir = sys.argv[1]
    ids = {int(name[:-5]) for name in os.listdir(cache_dir)
           if name.endswith(".html") and name[:-5].isdigit()}
    master = mgplib.load_master(sys.argv[2]) if len(sys.argv) > 2 else {}
    ids |= {int(k) for k in master} - mgplib.LOCAL_IDS
    ids = sorted(ids)
    languages = ", ".join('"{}"'.format(code) for code in LANGUAGES)
    people = {}
    for start in range(0, len(ids), 100):
        values = " ".join('"{}"'.format(i) for i in ids[start:start + 100])
        for row in sparql(PEOPLE % values):
            entry = people.setdefault(row["mgp"]["value"], {"advisors": [], "pages": {}})
            entry["item"] = row["item"]["value"]
            entry["sitelinks"] = int(row["links"]["value"])
            if "description" in row:
                entry["description"] = row["description"]["value"]
            if "image" in row and "wikiImage" not in entry:
                entry["wikiImage"] = row["image"]["value"]
            if "born" in row:
                entry["born"] = min(entry.get("born", 9999), int(row["born"]["value"]))
            if "died" in row:
                entry["died"] = max(entry.get("died", -9999), int(row["died"]["value"]))
        time.sleep(5)
        for row in sparql(PAGES % (values, languages)):
            entry = people.setdefault(row["mgp"]["value"], {"advisors": [], "pages": {}})
            entry["pages"][row["lang"]["value"]] = urllib.parse.unquote(row["article"]["value"])
        time.sleep(5)
        for row in sparql(ADVISORS % values):
            entry = people.setdefault(row["mgp"]["value"], {"advisors": [], "pages": {}})
            advisor = {"relation": row["prop"]["value"],
                       "item": row["advisor"]["value"],
                       "name": row.get("advisorLabel", {}).get("value", ""),
                       "mgp": int(row["advisorMgp"]["value"]) if "advisorMgp" in row
                       and row["advisorMgp"]["value"].isdigit() else None}
            if advisor not in entry["advisors"]:
                entry["advisors"].append(advisor)
        time.sleep(5)
    for entry in people.values():
        for code in LANGUAGES:
            if code in entry["pages"]:
                entry["wikiUrl"], entry["wikiUrl_language"] = entry["pages"][code], code
                break

    # what the addresses now in the file lead to
    in_file = {k: r["wikiUrl"] for k, r in master.items() if r.get("wikiUrl")}
    resolved = resolve_pages(sorted(set(in_file.values())))
    for key, url in in_file.items():
        people.setdefault(key, {"advisors": [], "pages": {}})["file_page"] = resolved[url]

    out = os.path.join(cache_dir, "wikidata.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(people, f, ensure_ascii=False, indent=1, sort_keys=True)
    matched = sum(1 for i in ids if str(i) in people and people[str(i)].get("item"))
    print("wikidata: {} of {} people matched, {} with a Wikipedia page ({} English)".format(
        matched, len(ids), sum(1 for e in people.values() if e.get("wikiUrl")),
        sum(1 for e in people.values() if e.get("wikiUrl_language") == "en")))


if __name__ == "__main__":
    main()
