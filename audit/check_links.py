"""Check that every wikiUrl and wikiImage in master.json still answers.

    python3 audit/check_links.py master.json audit/links.csv

Writes one row per link. A link is ok when the address answers as written.
When it does not, now_at gives the working address if there is one.

Wikipedia pages and portraits held by Wikimedia are checked through the
Wikimedia API, which says whether the page or file exists and what its address
is. Everything else is requested directly, one second apart.
"""
import csv
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mgplib  # noqa: E402

WIKIMEDIA_FILES = "upload.wikimedia.org"


def quote(url):
    """Percent-encode a URL that may hold raw non-ASCII characters."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc.encode("idna").decode("ascii"),
        urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._"),
        urllib.parse.quote(parts.query, safe="=&%:@!$'()*+,;/?~-._"), ""))


def probe(url):
    """Return (status, final_url). Status is an HTTP code or a short error word."""
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(quote(url), method=method,
                                         headers={"User-Agent": mgplib.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.geturl()
        except urllib.error.HTTPError as error:
            if method == "HEAD" and error.code in (403, 405, 501):
                continue  # some servers refuse HEAD; try GET
            return error.code, url
        except Exception as error:
            return type(error).__name__, url
    return "error", url


def api_get(url):
    """Call a Wikimedia API, waiting and retrying when the server says it is busy."""
    for attempt in range(6):
        request = urllib.request.Request(url, headers={"User-Agent": mgplib.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 5:
                raise
            wait = error.headers.get("Retry-After", "")
            time.sleep(min(int(wait) if wait.isdigit() else 30 * (attempt + 1), 300))


def wikimedia_file(url):
    """Return (api host, file name) for an upload.wikimedia.org address."""
    segments = urllib.parse.unquote(urllib.parse.urlsplit(url).path).split("/")
    # /wikipedia/<project>/[thumb/]<x>/<xy>/<name>[/<size>px-<name>]
    project = segments[2]
    name = segments[6] if segments[3] == "thumb" else segments[5]
    api = "commons.wikimedia.org" if project == "commons" else "{}.wikipedia.org".format(project)
    return api, name


def wikimedia_addresses(urls):
    """Ask the Wikimedia API where each file is. Returns {url: address or None}."""
    by_api, found = {}, {}
    for url in urls:
        try:
            api, name = wikimedia_file(url)
        except IndexError:
            found[url] = None
            continue
        by_api.setdefault(api, []).append((url, "File:" + name.replace("_", " ")))
    for api, pairs in by_api.items():
        for start in range(0, len(pairs), 40):
            chunk = pairs[start:start + 40]
            query = urllib.parse.urlencode({
                "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url",
                "titles": "|".join(title for _, title in chunk)})
            answer = api_get("https://{}/w/api.php?{}".format(api, query)).get("query", {})
            renamed = {hop["from"]: hop["to"] for hop in answer.get("normalized", [])}
            pages = {page["title"]: page for page in answer.get("pages", {}).values()}
            for url, title in chunk:
                page = pages.get(renamed.get(title, title), {})
                info = page.get("imageinfo")
                found[url] = info[0]["url"].split("?")[0] if info else None
            time.sleep(2)
    return found


def wikipedia_pages(urls):
    """Ask each Wikipedia whether the page exists as addressed. Returns {url: (status, now_at)}."""
    by_host, found = {}, {}
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        title = urllib.parse.unquote(parts.path.split("/wiki/", 1)[1]).replace("_", " ").strip()
        if parts.fragment or not title:
            found[url] = ("not a page address", "")
        else:
            by_host.setdefault(parts.netloc, []).append((url, title))
    for host, pairs in by_host.items():
        for start in range(0, len(pairs), 40):
            chunk = pairs[start:start + 40]
            query = urllib.parse.urlencode({"action": "query", "format": "json", "redirects": 1,
                                            "titles": "|".join(title for _, title in chunk)})
            answer = api_get("https://{}/w/api.php?{}".format(host, query)).get("query", {})
            renamed = {hop["from"]: hop["to"] for hop in answer.get("normalized", [])}
            moved = {hop["from"]: hop["to"] for hop in answer.get("redirects", [])}
            pages = {page["title"]: page for page in answer.get("pages", {}).values()}
            for url, title in chunk:
                name = renamed.get(title, title)
                final = moved.get(name, name)
                if final not in pages or "missing" in pages[final] or "invalid" in pages[final]:
                    found[url] = ("no such page", "")
                elif final != name:
                    found[url] = (200, "https://{}/wiki/{}".format(host, final.replace(" ", "_")))
                else:
                    found[url] = (200, "")
            time.sleep(2)
    return found


def main():
    master_path, out_path = sys.argv[1], sys.argv[2]
    data = mgplib.load_master(master_path)
    today = datetime.date.today().isoformat()
    links = [(record, field, record[field]) for record in data.values()
             for field in ("wikiUrl", "wikiImage") if record.get(field)]
    at_wikimedia = wikimedia_addresses(sorted(
        {url for _, _, url in links if urllib.parse.urlsplit(url).netloc == WIKIMEDIA_FILES}))
    at_wikipedia = wikipedia_pages(sorted(
        {url for _, _, url in links
         if urllib.parse.urlsplit(url).netloc.endswith(".wikipedia.org") and "/wiki/" in url}))
    rows = []
    for record, field, url in links:
        if url in at_wikipedia:
            status, now_at = at_wikipedia[url]
        elif url in at_wikimedia:
            address = at_wikimedia[url]
            if address is None:
                status, now_at = "gone", ""
            elif urllib.parse.unquote(address) == urllib.parse.unquote(url).replace("http://", "https://"):
                status, now_at = 200, ""
            else:
                status, now_at = "file exists at another address", address
        else:
            status, final = probe(url)
            now_at = urllib.parse.unquote(final) if (
                status == 200 and urllib.parse.unquote(final) != urllib.parse.unquote(quote(url))) else ""
            time.sleep(1.0)
        rows.append({"id": record["id"], "name": record["name"], "field": field, "url": url,
                     "status": status, "ok": "yes" if status == 200 else "no",
                     "now_at": now_at, "checked": today})
        if len(rows) % 50 == 0:
            print("checked {}".format(len(rows)), flush=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("links checked: {}  not answering as written: {}".format(
        len(rows), sum(1 for r in rows if r["ok"] == "no")), flush=True)


if __name__ == "__main__":
    main()
