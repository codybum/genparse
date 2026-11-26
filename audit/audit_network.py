"""Full audit of master.json: every person and every connection, against the source.

    python3 audit/audit_network.py master.json audit/cache audit            # report only
    python3 audit/audit_network.py master.json audit/cache audit --apply    # also update master.json

Optional: --import-2017 edges.csv names the first import (columns id,Source,Target),
which tells a connection added by hand from one the source has since dropped.

Run audit/fetch_mgp.py --closure and audit/fetch_wikidata.py first.

Files written to the output folder:
    connections.csv   one row per connection, in the file or at a source
    people.csv        one row per person, the file's values beside the source's
    changes.csv       every change --apply makes to master.json
    AUDIT.md          the summary and every finding

Status of a connection:
    confirmed      in master.json and in the Mathematics Genealogy Project
    new            in the project, not in master.json before this audit
    dataset_only   in master.json, not in the project today
    local          one end is a person who is not in the project (mgplib.LOCAL_IDS)
    wikidata_only  an advisor Wikidata lists that neither the file nor the project has

--apply only adds. It adds the new connections and the people they bring in, and
fills fields that are empty in the file (degrees, Wikipedia page, portrait).
It never changes or removes a value that is already there.
"""
import csv
import datetime
import json
import os
import re
import sys
import unicodedata

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


def write_csv(path, table, fields):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(table)


def main():
    args = sys.argv[1:]
    master_path, cache_dir, out_dir = args[0], args[1], args[2]
    apply = "--apply" in args
    import_path = args[args.index("--import-2017") + 1] if "--import-2017" in args else None
    today = datetime.date.today().isoformat()
    data = mgplib.load_master(master_path)
    in_file = {int(k) for k in data}

    wikidata = {}
    wd_path = os.path.join(cache_dir, "wikidata.json")
    if os.path.exists(wd_path):
        with open(wd_path, encoding="utf-8") as f:
            wikidata = json.load(f)

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
    source_names = {a["id"]: a["name"] for r in source.values() for a in r["advisors"]}

    def name_of(person_id):
        if str(person_id) in data:
            return data[str(person_id)]["name"]
        if person_id in source:
            return source[person_id]["name"]
        return source_names.get(person_id, "")

    file_edges = {(a, int(k)) for k, r in data.items() for a in r.get("advisors", [])}
    source_edges = {(a["id"], s) for s, r in source.items() for a in r["advisors"]}
    everyone = in_file | set(source)

    def wikidata_relations(advisor_id, student_id):
        entry = wikidata.get(str(student_id), {})
        return sorted({a["relation"] for a in entry.get("advisors", []) if a.get("mgp") == advisor_id})

    wikidata_edges = {(a["mgp"], int(s)) for s, e in wikidata.items() if int(s) in everyone
                      for a in e.get("advisors", []) if a.get("mgp")}

    # ---- connections -------------------------------------------------------------
    rows = []
    for advisor_id, student_id in sorted(file_edges | source_edges | wikidata_edges,
                                         key=lambda e: (e[1], e[0])):
        edge = (advisor_id, student_id)
        in_f, in_s = edge in file_edges, edge in source_edges
        if in_f and (student_id in mgplib.LOCAL_IDS or advisor_id in mgplib.LOCAL_IDS):
            status = "local"
        elif in_f and in_s:
            status = "confirmed"
        elif in_s:
            status = "new"
        elif in_f and (student_id in not_fetched or student_id in not_at_source):
            status = "not_checked"
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
            "in_master_before_audit": "yes" if in_f else "no",
            "in_math_genealogy": "yes" if in_s else "no",
            "wikidata": "; ".join(wikidata_relations(advisor_id, student_id)),
            "origin_in_master": origin,
            "advisor_was_in_master": "yes" if advisor_id in in_file else "no",
            "checked": today,
            "source": "" if student_id in mgplib.LOCAL_IDS else mgplib.MGP_URL.format(student_id),
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

    # ancestors of the root, and cycles
    root = min(mgplib.LOCAL_IDS)
    reached, stack = set(), [root]
    while stack:
        n = stack.pop()
        if n in reached or str(n) not in data:
            continue
        reached.add(n)
        stack.extend(data[str(n)].get("advisors", []))
    unreachable = sorted(in_file - reached)
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

    # ---- people ------------------------------------------------------------------
    new_people = sorted(set(source) - in_file)
    people_rows, name_diffs, degree_diffs, year_diffs, wiki_diffs = [], [], [], [], []
    for person_id in sorted(everyone):
        rec, src = data.get(str(person_id)), source.get(person_id)
        wd = wikidata.get(str(person_id), {})
        file_degrees = list(rec["degreeLists"]) if rec else []
        src_degrees = [d["text"] for d in src["degrees"]] if src else []
        status = ("local" if person_id in mgplib.LOCAL_IDS else "new" if rec is None
                  else "not at the project" if person_id in not_at_source
                  else "not fetched" if person_id in not_fetched else "existing")
        name_same = degrees_same = years_same = wiki_same = ""
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
        if rec and rec.get("wikiUrl") and wd.get("wikiUrl"):
            wiki_same = "yes" if rec["wikiUrl"].replace("http://", "https://") == wd["wikiUrl"] else "no"
            if wiki_same == "no":
                wiki_diffs.append((person_id, rec["name"], rec["wikiUrl"], wd["wikiUrl"]))
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
            "wikipedia_per_wikidata": wd.get("wikiUrl", ""),
            "wikiUrl_same": wiki_same,
            "has_portrait_in_master": ("yes" if rec.get("wikiImage") else "no") if rec else "",
            "wikidata_item": wd.get("item", ""),
            "checked": today,
            "source": "" if person_id in mgplib.LOCAL_IDS else mgplib.MGP_URL.format(person_id),
        })

    # ---- what --apply does ---------------------------------------------------------
    changes = []
    for person_id in new_people:
        src, wd = source[person_id], wikidata.get(str(person_id), {})
        record = {"id": person_id, "name": src["name"], "students": [], "advisors": []}
        if wd.get("wikiUrl"):
            record["wikiUrl"] = wd["wikiUrl"]
        if wd.get("wikiImage"):
            record["wikiImage"] = wd["wikiImage"]
        record["degreeLists"] = {d["text"]: (d["flags"][0] if d["flags"] else "") for d in src["degrees"]}
        record["isProcessed"] = True
        if apply:
            data[str(person_id)] = record
        changes.append({"change": "person added", "id": person_id, "name": src["name"], "field": "",
                        "value": "", "source": mgplib.MGP_URL.format(person_id), "date": today})
    for r in rows:
        if r["status"] != "new" or (r["advisor_id"] not in in_file and r["advisor_id"] not in source):
            continue
        if apply:
            student, advisor = data[str(r["student_id"])], data[str(r["advisor_id"])]
            if r["advisor_id"] not in student["advisors"]:
                student["advisors"].append(r["advisor_id"])
            if r["student_id"] not in advisor["students"]:
                advisor["students"].append(r["student_id"])
        changes.append({"change": "connection added", "id": r["student_id"], "name": r["student"],
                        "field": "advisors", "value": "{} ({})".format(r["advisor"], r["advisor_id"]),
                        "source": r["source"], "date": today})
    for person_id in sorted(in_file - mgplib.LOCAL_IDS):
        rec, src, wd = data[str(person_id)], source.get(person_id), wikidata.get(str(person_id), {})
        if src and not rec["degreeLists"] and src["degrees"]:
            filled = {d["text"]: (d["flags"][0] if d["flags"] else "") for d in src["degrees"]}
            if apply:
                rec["degreeLists"] = filled
            changes.append({"change": "empty field filled", "id": person_id, "name": rec["name"],
                            "field": "degreeLists", "value": " | ".join(filled),
                            "source": mgplib.MGP_URL.format(person_id), "date": today})
        for field in ("wikiUrl", "wikiImage"):
            if not rec.get(field) and wd.get(field):
                if apply:
                    ordered = {}
                    for key, value in rec.items():
                        if key == "degreeLists" and field not in ordered:
                            if field == "wikiImage" or "wikiImage" not in rec:
                                ordered[field] = wd[field]
                        ordered[key] = value
                        if key == "wikiUrl" and field == "wikiImage":
                            ordered[field] = wd[field]
                    ordered.setdefault(field, wd[field])
                    rec.clear()
                    rec.update(ordered)
                changes.append({"change": "empty field filled", "id": person_id, "name": rec["name"],
                                "field": field, "value": wd[field], "source": wd.get("item", ""),
                                "date": today})

    # ---- write ---------------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, "connections.csv"), rows, list(rows[0]))
    write_csv(os.path.join(out_dir, "people.csv"), people_rows, list(people_rows[0]))
    write_csv(os.path.join(out_dir, "changes.csv"), changes,
              ["change", "id", "name", "field", "value", "source", "date"])

    count = {s: sum(1 for r in rows if r["status"] == s) for s in
             ("confirmed", "new", "dataset_only", "local", "not_checked", "wikidata_only")}
    only = [r for r in rows if r["status"] == "dataset_only"]
    md = [
        "# Network audit, {}".format(today), "",
        "Every person and every advisor-student connection in `master.json` was compared with",
        "the live Mathematics Genealogy Project record. The project was then walked upward from",
        "every person in the file to find ancestors the file did not have. Wikidata, joined on",
        "the project id (property P549), is the second source.", "",
        "## Connections", "",
        "- In `master.json` before the audit: {}".format(len(file_edges)),
        "- Confirmed by the project: {}".format(count["confirmed"]),
        "- New, at the project and not in the file: {}".format(count["new"]),
        "- In the file but not at the project today: {} ({} of them supported by Wikidata)".format(
            count["dataset_only"], sum(1 for r in only if r["wikidata"])),
        "- Local, one end not in the project's database: {}".format(count["local"]),
        "- Not checked, record missing or not fetched: {}".format(count["not_checked"]),
        "- Listed by Wikidata only, not added: {}".format(count["wikidata_only"]),
        "",
        "## People", "",
        "- In `master.json` before the audit: {}".format(len(in_file)),
        "- Found at the project: {}".format(len(set(source) & in_file)),
        "- No such record at the project: {}".format(len(not_at_source)),
        "- Not fetched: {}".format(len(not_fetched)),
        "- New ancestors found: {}".format(len(new_people)),
        "- Name differs from the project's: {}".format(len(name_diffs)),
        "- Degree lines differ from the project's: {} ({} of them differ in a year)".format(
            len(degree_diffs), len(year_diffs)),
        "- Wikipedia page differs from the one Wikidata gives: {}".format(len(wiki_diffs)),
        "",
        "## Structure of the file", "",
        "- Consistency findings (ids, mirrored advisor and student lists, repeats): {}".format(len(findings)),
        "- People who are not ancestors of id {}: {}".format(root, len(unreachable)),
        "- Cycles: {}".format(len(cycles)),
        "- Names carried by more than one id: {}".format(len(same_name)),
        "- Wikidata items carried by more than one id: {}".format(len(same_item)),
        "- Connections where the advisor's first date follows the student's last: {}".format(len(out_of_order)),
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
                                 r["origin_in_master"]) if n]
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
    listing("In the file but not at the project", "Kept in the file.", edge_lines("dataset_only"))
    listing("Local connections", "One end is a person who is not in the project's database.",
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
    listing("Wikipedia pages that differ", "The file's page, then the page Wikidata gives.",
            ["{} ({}): {} / {}".format(n, i, f, s) for i, n, f, s in wiki_diffs])
    listing("Consistency findings", "", findings)
    listing("Not an ancestor of the root", "", ["{} ({})".format(name_of(i), i) for i in unreachable])
    listing("Cycles", "", [" -> ".join(str(n) for n in c) for c in cycles])
    listing("Names carried by more than one id", "", ["{}: {}".format(n, ids) for n, ids in same_name.items()])
    listing("Wikidata items carried by more than one id", "",
            ["{}: {}".format(i, ids) for i, ids in same_item.items()])
    listing("Dates out of order", "Advisor's first date, then the student's last.",
            ["{} ({}) {} advised {} ({}) {}".format(an, a, ay, sn, s, sy)
             for a, an, ay, s, sn, sy in out_of_order])
    listing("Changes made to master.json" if apply else "Changes --apply would make",
            "The full list is in changes.csv.",
            ["{} people added".format(sum(1 for c in changes if c["change"] == "person added")),
             "{} connections added".format(sum(1 for c in changes if c["change"] == "connection added")),
             "{} empty fields filled".format(sum(1 for c in changes if c["change"] == "empty field filled"))])
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
