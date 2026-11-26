"""Shared helpers for auditing master.json against the Mathematics Genealogy Project.

Python 3 standard library only.
"""
import html
import json
import os
import re

MGP_URL = "https://www.mathgenealogy.org/id.php?id={}"
USER_AGENT = "academic-genealogy-audit/1.0"

# People who are not in the Mathematics Genealogy Project's database are kept in
# audit/local_people.json, each with the advisors and the source that records them.
# Their ids (1, 2, ...) are this file's own and are never looked up at the project.
_LOCAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_people.json")
with open(_LOCAL_PATH, encoding="utf-8") as _f:
    LOCAL_PEOPLE = {int(key): value for key, value in json.load(_f).items()}
LOCAL_IDS = set(LOCAL_PEOPLE)
LOCAL_SOURCES = {(advisor, person_id): person["source"]
                 for person_id, person in LOCAL_PEOPLE.items() for advisor in person["advisors"]}

# The site's robots.txt asks every client to wait 10 seconds between requests.
CRAWL_DELAY = 10.5


def load_master(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _inline(value):
    # master.json was first written by Gson, which escapes these five characters.
    text = json.dumps(value, ensure_ascii=False)
    for char in "<>&='":
        text = text.replace(char, "\\u{:04x}".format(ord(char)))
    return text


def dump_master(data):
    """Serialise in the layout master.json has always used.

    Tab indentation, one field per line, id lists on a single line. Keeping the
    layout means a change to the data shows up as a small, readable diff.
    """
    out = ["{"]
    people = list(data.items())
    for n, (key, rec) in enumerate(people):
        out.append("\t" + _inline(key) + ": {")
        fields = list(rec.items())
        for m, (name, value) in enumerate(fields):
            comma = "," if m < len(fields) - 1 else ""
            if isinstance(value, dict):
                if not value:
                    out.append("\t\t" + _inline(name) + ": {}" + comma)
                    continue
                out.append("\t\t" + _inline(name) + ": {")
                items = list(value.items())
                for j, (k, v) in enumerate(items):
                    out.append("\t\t\t" + _inline(k) + ": " + _inline(v) + ("," if j < len(items) - 1 else ""))
                out.append("\t\t}" + comma)
            else:
                out.append("\t\t" + _inline(name) + ": " + _inline(value) + comma)
        out.append("\t}" + ("," if n < len(people) - 1 else ""))
    out.append("}")
    return "\n".join(out) + "\n"


def _clean(fragment):
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def parse_record(page):
    """Parse one saved id.php page into name, degrees, advisors and students."""
    m = re.search(r"<h2[^>]*>(.*?)</h2>", page, re.S)
    name = _clean(m.group(1)) if m else ""
    exists = bool(name) and "ID that does not exist" not in page
    cut = page.find("<th>Name</th>")
    head, table = (page[:cut], page[cut:]) if cut >= 0 else (page, "")

    # The project labels an advisor "Advisor 1", "Doctoral advisor", "Official Advisor",
    # "Mentor" and so on. Every labelled link above the student table is an advisor.
    start = head.find("<h2")
    advisors, seen = [], set()
    for label, adv_id, adv_name in re.findall(
            r'([A-Za-z][A-Za-z .\-]{0,40}?)\s*\d*:\s*<a href="id\.php\?id=(\d+)">(.*?)</a>',
            head[start:], re.S):
        if int(adv_id) not in seen:
            seen.add(int(adv_id))
            advisors.append({"id": int(adv_id), "name": _clean(adv_name), "relation": label.strip()})
    # a person link above the student table that carries no label is still reported
    unlabelled = set(int(i) for i in re.findall(r'<a href="id\.php\?id=(\d+)">', head[start:])) - seen
    for adv_id in sorted(unlabelled):
        advisors.append({"id": adv_id, "name": "", "relation": "unlabelled link"})

    degrees = []
    for m in re.finditer(r'<span style="margin-right: 0\.5em">(.*?)</div>', head, re.S):
        block = m.group(1)
        flags = re.findall(r'<img[^>]*src="(img/flags/[^"]+)"', block)
        text = _clean(re.sub(r"<img[^>]*>", " ", block))
        if text:
            degrees.append({"text": text, "flags": flags})

    students = [
        {"id": int(i), "name": _clean(n), "school": _clean(s), "year": _clean(y)}
        for i, n, s, y in re.findall(
            r'<td><a href="id\.php\?id=(\d+)">(.*?)</a></td><td>(.*?)</td><td[^>]*>(.*?)</td>',
            table, re.S)
    ]
    return {"exists": exists, "name": name, "advisors": advisors,
            "degrees": degrees, "students": students}


def cached_path(cache_dir, person_id):
    return os.path.join(cache_dir, "{}.html".format(person_id))


def read_cached(cache_dir, person_id):
    """Return the parsed record for an id, or None when its page is not cached."""
    path = cached_path(cache_dir, person_id)
    if not os.path.exists(path) or os.path.getsize(path) < 2000:
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_record(f.read())
