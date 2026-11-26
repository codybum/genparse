"""Check that every wikiUrl and wikiImage in master.json still answers.

    python3 audit/check_links.py master.json audit/links.csv

Writes one row per link with the HTTP status and, when the server redirects,
the address it now points to. Waits one second between requests.
"""
import csv
import datetime
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import mgplib  # noqa: E402


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


def main():
    master_path, out_path = sys.argv[1], sys.argv[2]
    data = mgplib.load_master(master_path)
    today = datetime.date.today().isoformat()
    rows = []
    for key, record in data.items():
        for field in ("wikiUrl", "wikiImage"):
            url = record.get(field)
            if not url:
                continue
            status, final = probe(url)
            moved = urllib.parse.unquote(final) != urllib.parse.unquote(quote(url))
            rows.append({"id": record["id"], "name": record["name"], "field": field, "url": url,
                         "status": status, "ok": "yes" if status == 200 else "no",
                         "now_at": urllib.parse.unquote(final) if moved else "", "checked": today})
            if len(rows) % 50 == 0:
                print("checked {}".format(len(rows)), flush=True)
            time.sleep(1.0)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    bad = [r for r in rows if r["ok"] == "no"]
    print("links checked: {}  not answering: {}  moved: {}".format(
        len(rows), len(bad), sum(1 for r in rows if r["now_at"])), flush=True)


if __name__ == "__main__":
    main()
