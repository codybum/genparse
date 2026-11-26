"""Verify master.json, independently of the audit program.

    python3 audit/verify.py master.json audit/cache

Reads the saved Mathematics Genealogy Project pages with its own, cruder method
(every person link above the student table) and checks that master.json and the
source agree exactly. Exits with status 1 if any check fails.
"""
import csv
import json
import os
import re
import sys

here = os.path.dirname(os.path.abspath(__file__))
master_path, cache = sys.argv[1], sys.argv[2]
raw = open(master_path, encoding="utf-8").read()
data = json.loads(raw)
with open(os.path.join(here, "local_people.json"), encoding="utf-8") as f:
    local_people = {int(k): v for k, v in json.load(f).items()}
with open(os.path.join(here, "keep_connections.csv"), encoding="utf-8") as f:
    rulings = {(int(r["advisor_id"]), int(r["student_id"])) for r in csv.DictReader(f)}

ok = True


def check(name, passed, detail=""):
    global ok
    print(("PASS  " if passed else "FAIL  ") + name + ("  " + str(detail) if detail and not passed else ""))
    ok = ok and passed


def walk(start, step):
    seen, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(step.get(n, ()))
    return seen


def source_advisors(person_id):
    page = open(os.path.join(cache, "{}.html".format(person_id)), encoding="utf-8", errors="replace").read()
    start, end = page.find("<h2"), page.find("<th>Name</th>")
    head = page[start:end if end >= 0 else len(page)]
    return {int(x) for x in re.findall(r'href="id\.php\?id=(\d+)"', head)} - {person_id}


# the file against itself
check("keys equal record ids", all(int(k) == r["id"] for k, r in data.items()))
check("every record has the required fields", all(
    all(field in r for field in ("id", "name", "students", "advisors", "degreeLists", "isProcessed"))
    for r in data.values()))
edges = {(a, int(k)) for k, r in data.items() for a in r["advisors"]}
mirror = {(int(k), s) for k, r in data.items() for s in r["students"]}
check("advisor and student lists mirror exactly", edges == mirror)
check("every id referenced exists", all(str(x) in data for e in edges for x in e))
check("no id repeated inside a list", all(
    len(set(r["advisors"])) == len(r["advisors"]) and len(set(r["students"])) == len(r["students"])
    for r in data.values()))
up, down = {}, {}
for a, s in edges:
    up.setdefault(s, set()).add(a)
    down.setdefault(a, set()).add(s)
root = min(local_people)
ancestors, descendants = walk(root, up), walk(root, down)
check("everyone is an ancestor or descendant of id {}".format(root),
      {int(k) for k in data} == ancestors | descendants)
waiting = {int(k): len(r["advisors"]) for k, r in data.items()}
ready, placed = [n for n, count in waiting.items() if count == 0], 0
while ready:
    n = ready.pop()
    placed += 1
    for s in down.get(n, ()):
        waiting[s] -= 1
        if waiting[s] == 0:
            ready.append(s)
check("no cycles", placed == len(data))

# the file against the source
at_project = [int(k) for k in data if int(k) not in local_people]
missing = [i for i in at_project if not os.path.exists(os.path.join(cache, "{}.html".format(i)))]
check("a source record is saved for every person", not missing, missing[:5])
if not missing:
    source = {(a, i) for i in at_project for a in source_advisors(i)}
    in_file = {e for e in edges if e[1] not in local_people}
    check("every advisor the source lists is in the file", source <= in_file, sorted(source - in_file)[:5])
    check("every connection in the file is at the source or kept by a recorded ruling",
          in_file - source == rulings, sorted(in_file - source))
check("local connections match local_people.json",
      {e for e in edges if e[1] in local_people}
      == {(a, i) for i, p in local_people.items() for a in p["advisors"]})

sys.path.insert(0, here)
import mgplib  # noqa: E402

check("file is written in its standard layout", mgplib.dump_master(data) == raw)

depth, queue = {root: 0}, [root]
while queue:
    n = queue.pop(0)
    for a in up.get(n, ()):
        if a not in depth:
            depth[a] = depth[n] + 1
            queue.append(a)
print("people {}  connections {}  ancestors of id {} {}  descendants {}  generations {}".format(
    len(data), len(edges), root, len(ancestors) - 1, len(descendants) - 1, max(depth.values())))
print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
