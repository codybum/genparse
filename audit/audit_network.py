"""Full audit of master.json: every person and every connection, against the source.

    python3 audit/audit_network.py master.json audit/cache audit            # report only
    python3 audit/audit_network.py master.json audit/cache audit --apply    # also update master.json

audit/import_2017_edges.csv is the first import, of September 2017 (columns
id,Source,Target). It tells a connection added by hand from one the source has
since dropped. links.csv in the output folder, written by check_links.py, lets a
portrait whose address is gone be corrected.

Run audit/fetch_mgp.py --closure and audit/fetch_wikidata.py first.

Files written to the output folder:
    connections.csv   one row per connection, in the file or at a source
    source_records.json   what the project's record of each person said when fetched
    people.csv        one row per person, the file's values beside the source's
    changes.csv       every change --apply makes to master.json
    AUDIT.md          the summary and every finding

Status of a connection:
    confirmed      in master.json and in the Mathematics Genealogy Project
    new            in the project, not in master.json before this audit
    dataset_only   in master.json, not in the project today
    local          one end is a person who is not in the project (mgplib.LOCAL_IDS)
    wikidata_only  an advisor Wikidata lists that neither the file nor the project has

    withdrawn      in the first import, since dropped by the project, no second source

--apply adds the new connections and the people they bring in, and fills fields
that are empty in the file (degrees, Wikipedia page, portrait). It removes a
connection only when the project has withdrawn it, and a person only when that
leaves them no longer an ancestor. It corrects a
Wikipedia link only when the link does not lead to the person, when the address
is malformed, or when an English page now exists and the file points to another
language. Names and degree lines already in the file are never changed.
Every change, with the old value, goes to changes.csv.
"""
import csv
import datetime
import json
import os
import re
import sys
import unicodedata
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mgplib  # noqa: E402


def years(degree_texts):
    found = []
    for text in degree_texts:
        found.extend(int(y) for y in re.findall(r"\b(\d{3,4})\b", text) if 500 <= int(y) <= 2100)
    return found


def fold(name):
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", " ", name.lower()).strip()


FIELD_ORDER = ["id", "name", "students", "advisors", "wikiUrl", "wikiImage", "degreeLists", "isProcessed"]


def set_field(record, field, value):
    """Set or remove a field, keeping the field order master.json has always used."""
    if value is None:
        record.pop(field, None)
    else:
        record[field] = value
    ordered = sorted(record.items(), key=lambda kv: FIELD_ORDER.index(kv[0])
                     if kv[0] in FIELD_ORDER else len(FIELD_ORDER))
    record.clear()
    record.update(ordered)


def plain(url):
    return urllib.parse.unquote(url).replace("http://", "https://").replace(" ", "_")


def host(url):
    return urllib.parse.urlsplit(url).netloc


def write_csv(path, table, fields):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(table)


def main():
    args = sys.argv[1:]
    master_path, cache_dir, out_dir = args[0], args[1], args[2]
    apply = "--apply" in args
    here = os.path.dirname(os.path.abspath(__file__))
    import_path = os.path.join(here, "import_2017_edges.csv")
    if not os.path.exists(import_path):
        import_path = None
    links_path = os.path.join(out_dir, "links.csv")
    today = datetime.date.today().isoformat()
    data = mgplib.load_master(master_path)
    # people kept in local_people.json who are not in the file yet
    added_local = []
    for person_id, person in sorted(mgplib.LOCAL_PEOPLE.items()):
        if str(person_id) in data:
            continue
        data[str(person_id)] = {"id": person_id, "name": person["name"], "students": [],
                                "advisors": list(person["advisors"]),
                                "degreeLists": dict(person["degreeLists"]), "isProcessed": True}
        for advisor in person["advisors"]:
            data[str(advisor)]["students"].append(person_id)
        added_local.append(person_id)
    in_file = {int(k) for k in data} - set(added_local)

    wikidata = {}
    wd_path = os.path.join(cache_dir, "wikidata.json")
    if os.path.exists(wd_path):
        with open(wd_path, encoding="utf-8") as f:
            wikidata = json.load(f)

    # Wikipedia links settled by hand, each with its reason
    manual = {}
    manual_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wikipedia_manual.csv")
    if os.path.exists(manual_path):
        with open(manual_path, encoding="utf-8") as f:
            manual = {int(row["id"]): row for row in csv.DictReader(f)}

    bad_images = {}
    if os.path.exists(links_path):
        with open(links_path, encoding="utf-8") as f:
            bad_images = {int(r["id"]): r for r in csv.DictReader(f)
                          if r["field"] == "wikiImage" and r["ok"] == "no"}

    first_import = None
    if import_path:
        with open(import_path, encoding="utf-8") as f:
            first_import = {(int(r["Source"]), int(r["Target"])) for r in csv.DictReader(f)}

    # ---- walk the source upward from everyone in the file ---------------------
    source, not_fetched, not_at_source = {}, set(), set()
    frontier = set(in_file - mgplib.LOCAL_IDS)
    while frontier:
        person_id = frontier.pop()
        record = mgplib.read_cached(cache_dir, person_id)
        if record is None:
            not_fetched.add(person_id)
            continue
        if not record["exists"]:
            not_at_source.add(person_id)
            continue
        source[person_id] = record
        for advisor in record["advisors"]:
            if advisor["id"] not in source:
                frontier.add(advisor["id"])
    source_names = {a["id"]: a["name"] for r in source.values() for a in r["advisors"] if a["name"]}

    wikidata_names = {a["mgp"]: a["name"] for e in wikidata.values()
                      for a in e.get("advisors", []) if a.get("mgp")}

    def name_of(person_id):
        if str(person_id) in data:
            return data[str(person_id)]["name"]
        if person_id in source:
            return source[person_id]["name"]
        return source_names.get(person_id, "") or wikidata_names.get(person_id, "")

    file_edges = {(a, int(k)) for k, r in data.items() for a in r.get("advisors", [])}
    source_edges = {(a["id"], s) for s, r in source.items() for a in r["advisors"]}
    relation = {(a["id"], s): a["relation"] for s, r in source.items() for a in r["advisors"]}
    everyone = in_file | set(source) | set(added_local)
    edges_before = {e for e in file_edges if e[1] not in added_local}

    def wikidata_relations(advisor_id, student_id):
        entry = wikidata.get(str(student_id), {})
        return sorted({a["relation"] for a in entry.get("advisors", []) if a.get("mgp") == advisor_id})

    wikidata_edges = {(a["mgp"], int(s)) for s, e in wikidata.items() if int(s) in everyone
                      for a in e.get("advisors", []) if a.get("mgp")}

    # ---- connections the source has withdrawn, and the graph that results --------
    # In the first import, gone from the project's record of the student today,
    # and not supported by Wikidata: the source has corrected itself.
    withdrawn = set()
    if first_import is not None:
        withdrawn = {(a, s) for a, s in file_edges - source_edges
                     if s in source and (a, s) in first_import and not wikidata_relations(a, s)}
    # the owner can rule that a withdrawn connection stays: audit/keep_connections.csv
    kept_by_ruling = {}
    keep_path = os.path.join(here, "keep_connections.csv")
    if os.path.exists(keep_path):
        with open(keep_path, encoding="utf-8") as f:
            kept_by_ruling = {(int(r["advisor_id"]), int(r["student_id"])): r["reason"]
                              for r in csv.DictReader(f)}
    withdrawn -= set(kept_by_ruling)
    known = in_file | set(source) | set(added_local)
    final_edges = {e for e in (file_edges | source_edges) - withdrawn if e[0] in known and e[1] in known}
    root = min(mgplib.LOCAL_IDS)

    def walk(start, step):
        seen, stack = set(), [start]
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(step.get(n, ()))
        return seen

    up, down = {}, {}
    for a, s in final_edges:
        up.setdefault(s, set()).add(a)
        down.setdefault(a, set()).add(s)
    tied = walk(root, up) | walk(root, down)   # every ancestor and descendant of the root
    final_edges = {e for e in final_edges if e[0] in tied and e[1] in tied}

    # ---- connections -------------------------------------------------------------
    rows = []
    for advisor_id, student_id in sorted(file_edges | source_edges | wikidata_edges,
                                         key=lambda e: (e[1], e[0])):
        edge = (advisor_id, student_id)
        in_f, in_s = edge in file_edges, edge in source_edges
        if student_id in mgplib.LOCAL_IDS:
            status = "local"
        elif in_f and in_s:
            status = "confirmed"
        elif in_s:
            status = "new"
        elif in_f and (student_id in not_fetched or student_id in not_at_source):
            status = "not_checked"
        elif edge in withdrawn:
            status = "withdrawn"
        elif in_f:
            status = "dataset_only"
        else:
            status = "wikidata_only"
        if first_import is None:
            origin = ""
        elif not in_f:
            origin = ""
        elif edge in first_import:
            origin = "2017 import"
        else:
            origin = "added after the 2017 import"
        rows.append({
            "advisor_id": advisor_id, "advisor": name_of(advisor_id),
            "student_id": student_id, "student": name_of(student_id),
            "status": status,
            "in_master_before_audit": "yes" if edge in edges_before else "no",
            "in_master_after_audit": "yes" if edge in final_edges else "no",
            "in_math_genealogy": "yes" if in_s else "no",
            "relation_at_source": relation.get(edge, ""),
            "other_evidence": kept_by_ruling.get(edge, ""),
            "wikidata": "; ".join(wikidata_relations(advisor_id, student_id)),
            "origin_in_master": origin,
            "advisor_was_in_master": "yes" if advisor_id in in_file else "no",
            "checked": today,
            "source": mgplib.LOCAL_SOURCES.get(edge, "") if student_id in mgplib.LOCAL_IDS
            else mgplib.MGP_URL.format(student_id),
        })

    # ---- structure of the file ---------------------------------------------------
    findings = []
    for k, r in data.items():
        if r.get("id") != int(k):
            findings.append("key {} holds a record whose id is {}".format(k, r.get("id")))
        for a in r.get("advisors", []):
            if str(a) not in data:
                findings.append("{} lists advisor {} who is not in the file".format(k, a))
            elif int(k) not in data[str(a)].get("students", []):
                findings.append("{} lists advisor {} but is missing from that advisor's students".format(k, a))
        for s in r.get("students", []):
            if str(s) not in data:
                findings.append("{} lists student {} who is not in the file".format(k, s))
            elif int(k) not in data[str(s)].get("advisors", []):
                findings.append("{} lists student {} but is missing from that student's advisors".format(k, s))
        for field in ("advisors", "students"):
            if len(set(r.get(field, []))) != len(r.get(field, [])):
                findings.append("{} repeats an id in {}".format(k, field))

    # everyone must be tied to the root, as an ancestor or a descendant; and no cycles
    reached, stack = set(), [root]
    while stack:
        n = stack.pop()
        if n in reached or str(n) not in data:
            continue
        reached.add(n)
        stack.extend(data[str(n)].get("advisors", []))
        stack.extend(data[str(n)].get("students", []))
    unreachable = sorted({int(k) for k in data} - reached)
    state, cycles = {}, []

    def visit(n, path):
        state[n] = 1
        for a in data.get(str(n), {}).get("advisors", []):
            if state.get(a) == 1:
                cycles.append(path + [n, a])
            elif a not in state:
                visit(a, path + [n])
        state[n] = 2

    sys.setrecursionlimit(10000)
    for n in sorted(in_file):
        if n not in state:
            visit(n, [])

    by_name, by_item = {}, {}
    for k, r in data.items():
        by_name.setdefault(fold(r["name"]), []).append(int(k))
        item = wikidata.get(k, {}).get("item")
        if item:
            by_item.setdefault(item, []).append(int(k))
    same_name = {n: ids for n, ids in by_name.items() if len(ids) > 1}
    same_item = {i: ids for i, ids in by_item.items() if len(ids) > 1}

    # date order: an advisor's first dated entry should not follow the student's last
    out_of_order = []
    for advisor_id, student_id in sorted(file_edges):
        a, s = data.get(str(advisor_id)), data.get(str(student_id))
        if not a or not s:
            continue
        ya, ys = years(a["degreeLists"]), years(s["degreeLists"])
        if ya and ys and min(ya) > max(ys):
            out_of_order.append((advisor_id, a["name"], min(ya), student_id, s["name"], max(ys)))

    # ---- is the Wikidata match the right person? ----------------------------------
    def match_of(person_id):
        """A degree dated before the person was ten, or after they died, means a wrong match."""
        wd = wikidata.get(str(person_id), {})
        rec, src = data.get(str(person_id)), source.get(person_id)
        # the file's own dates when it has them, otherwise the project's
        dated = years(list(rec["degreeLists"]) if rec else []) or years(
            [d["text"] for d in src["degrees"]] if src else [])
        if not wd.get("item"):
            return "no match"
        if not dated or ("born" not in wd and "died" not in wd):
            return "undated"
        if "born" in wd and min(dated) < wd["born"] + 10:
            return "suspect"
        if "died" in wd and min(dated) > wd["died"]:
            return "suspect"
        return "plausible"

    match = {person_id: match_of(person_id) for person_id in everyone}
    suspects = sorted(i for i, m in match.items() if m == "suspect")
    for person_id in suspects:  # a suspect match is not used for anything below
        kept = wikidata.get(str(person_id), {})
        wikidata[str(person_id)] = {"advisors": [], "file_page": kept.get("file_page", {}),
                                    "suspect_item": kept.get("item", "")}

    # ---- people ------------------------------------------------------------------
    new_people = sorted((set(source) - in_file) & tied)
    removed_people = sorted((in_file | set(added_local)) - tied)
    people_rows, name_diffs, degree_diffs, year_diffs, wiki_diffs = [], [], [], [], []
    checks = {}
    for person_id in sorted(everyone):
        rec, src = data.get(str(person_id)), source.get(person_id)
        wd = wikidata.get(str(person_id), {})
        file_degrees = list(rec["degreeLists"]) if rec else []
        src_degrees = [d["text"] for d in src["degrees"]] if src else []
        status = ("local" if person_id in mgplib.LOCAL_IDS
                  else "removed, no longer an ancestor" if person_id in removed_people
                  else "not added, not an ancestor" if rec is None and person_id not in tied
                  else "new" if rec is None
                  else "not at the project" if person_id in not_at_source
                  else "not fetched" if person_id in not_fetched else "existing")
        name_same = degrees_same = years_same = wiki_check = ""
        if rec and src:
            name_same = "yes" if re.sub(r"\s+", " ", rec["name"]).strip() == src["name"] else "no"
            degrees_same = "yes" if set(file_degrees) == set(src_degrees) else "no"
            years_same = "yes" if set(years(file_degrees)) == set(years(src_degrees)) else "no"
            if name_same == "no":
                name_diffs.append((person_id, rec["name"], src["name"]))
            if degrees_same == "no":
                degree_diffs.append((person_id, rec["name"], file_degrees, src_degrees))
            if years_same == "no":
                year_diffs.append((person_id, rec["name"], sorted(set(years(file_degrees))),
                                   sorted(set(years(src_degrees)))))
        page = wd.get("file_page", {})
        if rec and rec.get("wikiUrl") and person_id not in mgplib.LOCAL_IDS:
            if not page:
                wiki_check = "not checked"
            elif page["status"] != "found":
                wiki_check = page["status"]
            elif not page.get("item"):
                wiki_check = "page has no Wikidata item"
            elif match.get(person_id) == "suspect":
                wiki_check = "not checked, Wikidata match is suspect"
            elif page["item"] == wd.get("item"):
                wiki_check = "same person"
            else:
                wiki_check = "different subject"
            if wiki_check != "same person":
                wiki_diffs.append((person_id, rec["name"], rec["wikiUrl"], wiki_check,
                                   page.get("leads_to", ""), wd.get("wikiUrl", "")))
            checks[person_id] = wiki_check
        people_rows.append({
            "id": person_id, "status": status,
            "name_in_master": rec["name"] if rec else "",
            "name_in_math_genealogy": src["name"] if src else "",
            "name_same": name_same,
            "degrees_in_master": " | ".join(file_degrees),
            "degrees_in_math_genealogy": " | ".join(src_degrees),
            "degrees_same": degrees_same, "years_same": years_same,
            "advisors_in_master": len(rec["advisors"]) if rec else "",
            "advisors_in_math_genealogy": len(src["advisors"]) if src else "",
            "wikiUrl_in_master": rec.get("wikiUrl", "") if rec else "",
            "wikiUrl_check": wiki_check,
            "wikiUrl_leads_to": page.get("leads_to", "") if rec and rec.get("wikiUrl") else "",
            "wikipedia_per_wikidata": wd.get("wikiUrl", ""),
            "has_portrait_in_master": ("yes" if rec.get("wikiImage") else "no") if rec else "",
            "wikidata_item": wd.get("item", "") or wd.get("suspect_item", ""),
            "wikidata_match": match.get(person_id, ""),
            "wiki_pages": wd.get("sitelinks", ""),
            "description": wd.get("description", ""),
            "checked": today,
            "source": "" if person_id in mgplib.LOCAL_IDS else mgplib.MGP_URL.format(person_id),
        })

    # ---- what --apply does ---------------------------------------------------------
    changes = []
    for person_id in added_local:
        person = mgplib.LOCAL_PEOPLE[person_id]
        changes.append({"change": "person added", "id": person_id, "name": person["name"], "field": "",
                        "old_value": "", "value": " | ".join(person["degreeLists"]),
                        "source": person["source"], "date": today})
        for advisor in person["advisors"]:
            changes.append({"change": "connection added", "id": person_id, "name": person["name"],
                            "field": "advisors", "old_value": "",
                            "value": "{} ({})".format(name_of(advisor), advisor),
                            "source": person["source"], "date": today})
    for person_id in new_people:
        src, wd = source[person_id], wikidata.get(str(person_id), {})
        record = {"id": person_id, "name": src["name"], "students": [], "advisors": []}
        decision = manual.get(person_id)
        if decision and decision["decision"] == "set":
            record["wikiUrl"] = decision["wikiUrl"]
        elif wd.get("wikiUrl") and not (decision and decision["decision"] == "remove"):
            record["wikiUrl"] = wd["wikiUrl"]
        if wd.get("wikiImage"):
            record["wikiImage"] = wd["wikiImage"]
        record["degreeLists"] = {d["text"]: (d["flags"][0] if d["flags"] else "") for d in src["degrees"]}
        record["isProcessed"] = True
        set_field(record, "isProcessed", True)
        data[str(person_id)] = record
        changes.append({"change": "person added", "id": person_id, "name": src["name"], "field": "",
                        "old_value": "", "value": "", "source": mgplib.MGP_URL.format(person_id),
                        "date": today})
    for r in rows:
        edge = (r["advisor_id"], r["student_id"])
        if r["status"] == "new" and edge in final_edges:
            student, advisor = data[str(r["student_id"])], data[str(r["advisor_id"])]
            if r["advisor_id"] not in student["advisors"]:
                student["advisors"].append(r["advisor_id"])
            if r["student_id"] not in advisor["students"]:
                advisor["students"].append(r["student_id"])
            changes.append({"change": "connection added", "id": r["student_id"], "name": r["student"],
                            "field": "advisors", "old_value": "",
                            "value": "{} ({})".format(r["advisor"], r["advisor_id"]),
                            "source": r["source"], "date": today})
        elif r["status"] == "withdrawn":
            data[str(r["student_id"])]["advisors"].remove(r["advisor_id"])
            data[str(r["advisor_id"])]["students"].remove(r["student_id"])
            changes.append({"change": "connection removed, the source no longer lists it",
                            "id": r["student_id"], "name": r["student"], "field": "advisors",
                            "old_value": "{} ({})".format(r["advisor"], r["advisor_id"]), "value": "",
                            "source": r["source"], "date": today})
    for person_id in removed_people:
        gone = data.pop(str(person_id))
        changes.append({"change": "person removed, no longer an ancestor of id {}".format(root),
                        "id": person_id, "name": gone["name"], "field": "",
                        "old_value": " | ".join(gone["degreeLists"]), "value": "",
                        "source": mgplib.MGP_URL.format(person_id), "date": today})
    for rec in data.values():
        rec["advisors"] = [a for a in rec["advisors"] if str(a) in data]
        rec["students"] = [x for x in rec["students"] if str(x) in data]
    for person_id in sorted(in_file - mgplib.LOCAL_IDS - set(removed_people)):
        rec, src, wd = data[str(person_id)], source.get(person_id), wikidata.get(str(person_id), {})
        if src and not rec["degreeLists"] and src["degrees"]:
            filled = {d["text"]: (d["flags"][0] if d["flags"] else "") for d in src["degrees"]}
            rec["degreeLists"] = filled
            changes.append({"change": "empty field filled", "id": person_id, "name": rec["name"],
                            "field": "degreeLists", "old_value": "", "value": " | ".join(filled),
                            "source": mgplib.MGP_URL.format(person_id), "date": today})
        def log(change, field, old, new, src_url):
            changes.append({"change": change, "id": person_id, "name": rec["name"], "field": field,
                            "old_value": old or "", "value": new or "", "source": src_url, "date": today})

        # Wikipedia page: must be about this person, English when an English page exists
        current, target, check = rec.get("wikiUrl"), wd.get("wikiUrl"), checks.get(person_id, "")
        page = wd.get("file_page", {})
        new_url, why = current, ""
        decision = manual.get(person_id)
        if decision:
            if decision["decision"] == "remove":
                new_url = None
            elif decision["decision"] == "set":
                new_url = decision["wikiUrl"]
            if new_url != current:
                why = "wikiUrl {}, reviewed by hand: {}".format(
                    "removed" if new_url is None else "set", decision["note"])
        elif not current and target:
            new_url, why = target, "empty field filled"
        elif check in ("no such page", "not a Wikipedia page") or (
                check == "different subject" and target):
            new_url, why = target, "wikiUrl corrected, was {}".format(check)
            if target is None:
                why = "wikiUrl removed, was {}".format(check)
        elif check == "same person":
            if target and wd.get("wikiUrl_language") == "en" and host(current) != "en.wikipedia.org":
                new_url, why = target, "wikiUrl changed to the English page"
            elif plain(current) != plain(page["leads_to"]):
                new_url, why = page["leads_to"], "wikiUrl address corrected"
        if why:
            set_field(rec, "wikiUrl", new_url)
            log(why, "wikiUrl", current, new_url, wd.get("item", ""))

        # portrait: fill when empty, replace when the address is gone
        image, bad = rec.get("wikiImage"), bad_images.get(person_id)
        new_image, why = image, ""
        if not image and wd.get("wikiImage"):
            new_image, why = wd["wikiImage"], "empty field filled"
        elif image and "#/media/" in image and wd.get("wikiImage"):
            new_image, why = wd["wikiImage"], "wikiImage replaced, address was a viewer page and not an image file"
        elif bad and bad["now_at"]:
            new_image, why = bad["now_at"], "wikiImage address corrected, same file"
        elif bad and wd.get("wikiImage"):
            new_image, why = wd["wikiImage"], "wikiImage replaced, address was {}".format(bad["status"])
        elif bad and bad["status"] in ("gone", "404", "410"):
            new_image, why = None, "wikiImage removed, address was {}".format(bad["status"])
        if why:
            set_field(rec, "wikiImage", new_image)
            log(why, "wikiImage", image, new_image, wd.get("item", ""))

    # people outside the project: only a link settled by hand is acted on
    for person_id in sorted(mgplib.LOCAL_IDS):
        decision, rec = manual.get(person_id), data.get(str(person_id))
        if decision and rec and decision["decision"] == "remove" and rec.get("wikiUrl"):
            old_url = rec["wikiUrl"]
            set_field(rec, "wikiUrl", None)
            changes.append({"change": "wikiUrl removed, reviewed by hand: " + decision["note"],
                            "id": person_id, "name": rec["name"], "field": "wikiUrl",
                            "old_value": old_url, "value": "", "source": "", "date": today})

    # ---- the file after the audit must pass every structural check -----------------
    after = []
    for k, r in data.items():
        for a in r["advisors"]:
            if str(a) not in data or int(k) not in data[str(a)]["students"]:
                after.append("{} / advisor {}".format(k, a))
        for x in r["students"]:
            if str(x) not in data or int(k) not in data[str(x)]["advisors"]:
                after.append("{} / student {}".format(k, x))
    up_after = {int(k): set(r["advisors"]) for k, r in data.items()}
    down_after = {int(k): set(r["students"]) for k, r in data.items()}
    loose = {int(k) for k in data} - walk(root, up_after) - walk(root, down_after)
    after.extend("{} is not tied to id {}".format(i, root) for i in sorted(loose))
    edges_after = {(a, int(k)) for k, r in data.items() for a in r["advisors"]}

    # ---- write ---------------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, "connections.csv"), rows, list(rows[0]))
    write_csv(os.path.join(out_dir, "people.csv"), people_rows, list(people_rows[0]))
    snapshot = {str(i): {"name": r["name"], "degrees": r["degrees"], "advisors": r["advisors"],
                         "students_listed": len(r["students"]), "url": mgplib.MGP_URL.format(i)}
                for i, r in sorted(source.items())}
    with open(os.path.join(out_dir, "source_records.json"), "w", encoding="utf-8") as f:
        json.dump({"fetched": today, "source": "Mathematics Genealogy Project", "records": snapshot},
                  f, ensure_ascii=False, indent=1)
    write_csv(os.path.join(out_dir, "changes.csv"), changes,
              ["change", "id", "name", "field", "old_value", "value", "source", "date"])

    count = {s: sum(1 for r in rows if r["status"] == s) for s in
             ("confirmed", "new", "dataset_only", "withdrawn", "local", "not_checked", "wikidata_only")}
    only = [r for r in rows if r["status"] == "dataset_only"]
    md = [
        "# Network audit, {}".format(today), "",
        "Every person and every advisor-student connection in `master.json` was compared with",
        "the live Mathematics Genealogy Project record. The project was then walked upward from",
        "every person in the file to find ancestors the file did not have. Wikidata, joined on",
        "the project id (property P549), is the second source.", "",
        "## Connections", "",
        "- In `master.json` before the audit: {}".format(len(edges_before)),
        "- Confirmed by the project: {}".format(count["confirmed"]),
        "- New, at the project and not in the file: {}".format(count["new"]),
        "- In the file, not at the project today, kept on other evidence: {}".format(count["dataset_only"]),
        "- Withdrawn by the project since the first import, removed: {}".format(count["withdrawn"]),
        "- Local, one end not in the project's database: {}".format(count["local"]),
        "- Not checked, record missing or not fetched: {}".format(count["not_checked"]),
        "- Listed by Wikidata only, not added: {}".format(count["wikidata_only"]),
        "- In `master.json` after the audit: {}".format(len(edges_after)),
        "",
        "## People", "",
        "- In `master.json` before the audit: {}".format(len(in_file)),
        "- Found at the project: {}".format(len(set(source) & in_file)),
        "- No such record at the project: {}".format(len(not_at_source)),
        "- Not fetched: {}".format(len(not_fetched)),
        "- New ancestors found: {}".format(len(new_people)),
        "- Removed, no longer an ancestor once the withdrawn connections are gone: {}".format(len(removed_people)),
        "- In `master.json` after the audit: {}".format(len(data)),
        "- Name differs from the project's: {}".format(len(name_diffs)),
        "- Degree lines differ from the project's: {} ({} of them differ in a year)".format(
            len(degree_diffs), len(year_diffs)),
        "- Wikipedia links in the file that do not lead to the person: {}".format(len(wiki_diffs)),
        "- Wikidata matches set aside because the dates do not fit: {}".format(len(suspects)),
        "",
        "## Structure of the file", "",
        "- Consistency findings (ids, mirrored advisor and student lists, repeats): {}".format(len(findings)),
        "- People not tied to id {} as ancestor or descendant: {}".format(root, len(unreachable)),
        "- Cycles: {}".format(len(cycles)),
        "- Names carried by more than one id: {}".format(len(same_name)),
        "- Wikidata items carried by more than one id: {}".format(len(same_item)),
        "- Connections where the advisor's first date follows the student's last: {}".format(len(out_of_order)),
        "- Structural faults in the file after the audit: {}".format(len(after)),
        "",
    ]
    summary_end = len(md)

    def listing(title, note, items):
        if items:
            md.extend(["## " + title, "", note, ""] + ["- " + i for i in items] + [""])

    def edge_lines(status):
        out = []
        for r in rows:
            if r["status"] != status:
                continue
            notes = [n for n in (("Wikidata: " + r["wikidata"]) if r["wikidata"] else "",
                                 r["origin_in_master"],
                                 r["source"] if status == "local" else "") if n]
            out.append("{} ({}) advised {} ({}){}".format(
                r["advisor"], r["advisor_id"], r["student"], r["student_id"],
                " [" + ", ".join(notes) + "]" if notes else ""))
        return out

    listing("New connections", "At the project today and absent from the file before this audit.",
            edge_lines("new"))
    listing("New people", "Ancestors the new connections bring in.",
            ["{} ({}): {}".format(source[i]["name"], i,
                                  " | ".join(d["text"] for d in source[i]["degrees"]) or "no degree listed")
             for i in new_people])
    listing("Withdrawn by the project",
            "In the first import of 2017, no longer on the student's record at the project, and not "
            "supported by Wikidata. Removed from the file.", edge_lines("withdrawn"))
    listing("People removed",
            "They were ancestors only through a withdrawn connection.",
            ["{} ({})".format(name_of(i), i) for i in removed_people])
    listing("In the file but not at the project",
            "Kept in the file on other evidence, which connections.csv gives in full.",
            edge_lines("dataset_only"))
    listing("Local connections",
            "One end is a person who is not in the project's database. The source is the record named.",
            edge_lines("local"))
    listing("Listed by Wikidata only",
            "Advisors Wikidata gives that neither the file nor the project has. Recorded, not added.",
            edge_lines("wikidata_only"))
    listing("Records not available", "",
            ["id {} ({}): no such record at the project".format(i, name_of(i)) for i in sorted(not_at_source)]
            + ["id {} ({}): not fetched".format(i, name_of(i)) for i in sorted(not_fetched)])
    listing("Years that differ", "The file's years beside the project's.",
            ["{} ({}): file {} , project {}".format(n, i, f, s) for i, n, f, s in year_diffs])
    listing("Names that differ", "The file's name, then the project's.",
            ["{}: {} / {}".format(i, f, s) for i, f, s in name_diffs])
    listing("Degree lines that differ", "The file's lines, then the project's.",
            ["{} ({}): {} / {}".format(n, i, " | ".join(f) or "none", " | ".join(s) or "none")
             for i, n, f, s in degree_diffs])
    listing("Wikipedia links that do not lead to the person",
            "The file's link, what was found, and the person's own page where Wikidata has one.",
            ["{} ({}): {} is {}{}. Person's page: {}".format(
                n, i, f, c, " (" + to + ")" if to else "", page_url or "none")
             for i, n, f, c, to, page_url in wiki_diffs])
    listing("Wikidata matches not used",
            "Wikidata puts this project id on a person whose dates do not fit the degree dates.",
            ["{} ({}): {}".format(name_of(i), i, wikidata[str(i)].get("suspect_item", "")) for i in suspects])
    listing("Consistency findings", "", findings)
    listing("Not tied to the root", "", ["{} ({})".format(name_of(i), i) for i in unreachable])
    listing("Cycles", "", [" -> ".join(str(n) for n in c) for c in cycles])
    listing("Names carried by more than one id", "", ["{}: {}".format(n, ids) for n, ids in same_name.items()])
    listing("Wikidata items carried by more than one id", "",
            ["{}: {}".format(i, ids) for i, ids in same_item.items()])
    listing("Dates out of order", "Advisor's first date, then the student's last.",
            ["{} ({}) {} advised {} ({}) {}".format(an, a, ay, sn, s, sy)
             for a, an, ay, s, sn, sy in out_of_order])
    ranked = sorted((r for r in people_rows if r["wiki_pages"] != ""),
                    key=lambda r: -r["wiki_pages"])[:25]
    listing("Standout people",
            "The 25 people with the most pages across Wikipedia's language editions and sister "
            "projects, as counted by Wikidata. The count is a plain measure of how widely a person is written about.",
            ["{} ({}): {} pages. {}".format(r["name_in_master"] or r["name_in_math_genealogy"], r["id"],
                                            r["wiki_pages"], r["description"]) for r in ranked])
    listing("Changes made to master.json" if apply else "Changes --apply would make",
            "The full list is in changes.csv.",
            ["{} connections removed".format(sum(1 for c in changes if c["change"].startswith("connection removed"))),
             "{} people removed".format(sum(1 for c in changes if c["change"].startswith("person removed"))),
             "{} people added".format(sum(1 for c in changes if c["change"] == "person added")),
             "of them {} not in the project's database: {}".format(
                 len(added_local), ", ".join(mgplib.LOCAL_PEOPLE[i]["name"] for i in added_local) or "none"),
             "{} connections added".format(sum(1 for c in changes if c["change"] == "connection added")),
             "{} empty fields filled".format(sum(1 for c in changes if c["change"] == "empty field filled")),
             "{} Wikipedia links corrected, changed to English or removed".format(
                 sum(1 for c in changes if c["change"].startswith("wikiUrl"))),
             "{} portrait addresses corrected, replaced or removed".format(
                 sum(1 for c in changes if c["change"].startswith("wikiImage")))])
    with open(os.path.join(out_dir, "AUDIT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("\n".join(md[6:summary_end]))
    print("changes: {}".format(len(changes)))

    if apply:
        with open(master_path, "w", encoding="utf-8") as f:
            f.write(mgplib.dump_master(data))
        print("master.json updated: {} people".format(len(data)))


if __name__ == "__main__":
    main()
