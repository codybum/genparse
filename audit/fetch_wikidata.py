"""Second source for the audit: what Wikidata records for the same people.

    python3 audit/fetch_wikidata.py audit/cache

Wikidata stores each person's Mathematics Genealogy Project id (property P549),
so the join is exact. For every cached project record this saves the English
Wikipedia page, the portrait, and the advisors Wikidata lists (doctoral advisor,
P184, and student of, P1066) to audit/cache/wikidata.json.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mgplib  # noqa: E402

ENDPOINT = "https://query.wikidata.org/sparql"

PEOPLE = """
SELECT ?mgp ?item ?article ?image WHERE {
  VALUES ?mgp { %s }
  ?item wdt:P549 ?mgp .
  OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
  OPTIONAL { ?item wdt:P18 ?image . }
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


def run(query):
    body = urllib.parse.urlencode({"query": query, "format": "json"}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, headers={
        "User-Agent": mgplib.USER_AGENT, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["results"]["bindings"]


def main():
    cache_dir = sys.argv[1]
    ids = sorted(int(name[:-5]) for name in os.listdir(cache_dir)
                 if name.endswith(".html") and name[:-5].isdigit())
    people = {}
    for start in range(0, len(ids), 150):
        values = " ".join('"{}"'.format(i) for i in ids[start:start + 150])
        for row in run(PEOPLE % values):
            entry = people.setdefault(row["mgp"]["value"], {"advisors": []})
            entry["item"] = row["item"]["value"]
            if "article" in row:
                entry["wikiUrl"] = urllib.parse.unquote(row["article"]["value"])
            if "image" in row and "wikiImage" not in entry:
                entry["wikiImage"] = row["image"]["value"]
        time.sleep(2)
        for row in run(ADVISORS % values):
            entry = people.setdefault(row["mgp"]["value"], {"advisors": []})
            advisor = {"relation": row["prop"]["value"],
                       "item": row["advisor"]["value"],
                       "name": row.get("advisorLabel", {}).get("value", ""),
                       "mgp": int(row["advisorMgp"]["value"]) if "advisorMgp" in row
                       and row["advisorMgp"]["value"].isdigit() else None}
            if advisor not in entry["advisors"]:
                entry["advisors"].append(advisor)
        time.sleep(2)
    out = os.path.join(cache_dir, "wikidata.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(people, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("wikidata: {} of {} people matched".format(len(people), len(ids)))


if __name__ == "__main__":
    main()
