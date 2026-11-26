"""Audit every advisor-student connection in master.json against the source.

    python3 audit/audit_connections.py master.json audit/cache audit            # report only
    python3 audit/audit_connections.py master.json audit/cache audit --apply    # also update master.json

Run audit/fetch_mgp.py (with --closure) first, and audit/fetch_wikidata.py for the
second source. Writes three files into the output folder:

    connections.csv   one row per connection, in the file or at the source
    people.csv        one row per person, in the file or newly found
    AUDIT.md          the summary

Status of a connection:
    confirmed      in master.json and in the Mathematics Genealogy Project
    new            in the project, not in master.json before this audit
    dataset_only   in master.json, not in the project today
    local          involves a person who is not in the project (see mgplib.LOCAL_IDS)

Nothing is ever removed from master.json. --apply only adds the new connections
and the people they bring in.
"""
import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mgplib  # noqa: E402


def main():
    master_path, cache_dir, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    apply = "--apply" in sys.argv[4:]
    today = datetime.date.today().isoformat()
    data = mgplib.load_master(master_path)
    in_file = {int(k) for k in data}

    wikidata = {}
    wd_path = os.path.join(cache_dir, "wikidata.json")
    if os.path.exists(wd_path):
        with open(wd_path, encoding="utf-8") as f:
            wikidata = json.load(f)

    # ---- walk the source upward from everyone in the file -------------------
    source, not_fetched, not_at_source = {}, [], []
    frontier = set(in_file - mgplib.LOCAL_IDS)
    while frontier:
        person_id = frontier.pop()
        record = mgplib.read_cached(cache_dir, person_id)
        if record is None:
            not_fetched.append(person_id)
            continue
        if not record["exists"]:
            not_at_source.append(person_id)
            continue
        source[person_id] = record
        for advisor in record["advisors"]:
            if advisor["id"] not in source:
                frontier.add(advisor["id"])
    frontier_names = {a["id"]: a["name"] for r in source.values() for a in r["advisors"]}

    def name_of(person_id):
        if str(person_id) in data:
            return data[str(person_id)]["name"]
        if person_id in source:
            return source[person_id]["name"]
        return frontier_names.get(person_id, "")

    file_edges = {(a, int(k)) for k, r in data.items() for a in r.get("advisors", [])}
    source_edges = {(a["id"], s) for s, r in source.items() for a in r["advisors"]}

    def wikidata_says(advisor_id, student_id):
        entry = wikidata.get(str(student_id), {})
        return sorted({a["relation"] for a in entry.get("advisors", []) if a.get("mgp") == advisor_id})

    rows = []
    for advisor_id, student_id in sorted(file_edges | source_edges, key=lambda e: (e[1], e[0])):
        in_f, in_s = (advisor_id, student_id) in file_edges, (advisor_id, student_id) in source_edges
        if student_id in mgplib.LOCAL_IDS or advisor_id in mgplib.LOCAL_IDS:
            status = "local"
        elif in_f and in_s:
            status = "confirmed"
        elif in_s:
            status = "new"
        elif student_id in not_fetched or student_id in not_at_source:
            status = "not_checked"
        else:
            status = "dataset_only"
        rows.append({
            "advisor_id": advisor_id, "advisor": name_of(advisor_id),
            "student_id": student_id, "student": name_of(student_id),
            "status": status,
            "in_master_before_audit": "yes" if in_f else "no",
            "in_math_genealogy": "yes" if in_s else "no",
            "wikidata": "; ".join(wikidata_says(advisor_id, student_id)),
            "advisor_was_in_master": "yes" if advisor_id in in_file else "no",
            "checked": today,
            "source": "" if student_id in mgplib.LOCAL_IDS else mgplib.MGP_URL.format(student_id),
        })

    # ---- the file's own consistency: advisors and students must mirror -------
    mirror_errors = []
    for k, r in data.items():
        for a in r.get("advisors", []):
            if str(a) not in data or int(k) not in data[str(a)].get("students", []):
                mirror_errors.append("{} lists advisor {} but is not in that advisor's students".format(k, a))
        for s in r.get("students", []):
            if str(s) not in data or int(k) not in data[str(s)].get("advisors", []):
                mirror_errors.append("{} lists student {} but is not in that student's advisors".format(k, s))

    # ---- people ---------------------------------------------------------------
    new_people = sorted(set(source) - in_file)
    people_rows = []
    for person_id in sorted(in_file | set(source)):
        rec, src = data.get(str(person_id)), source.get(person_id)
        wd = wikidata.get(str(person_id), {})
        people_rows.append({
            "id": person_id,
            "status": "local" if person_id in mgplib.LOCAL_IDS else "existing" if rec else "new",
            "name_in_master": rec["name"] if rec else "",
            "name_in_math_genealogy": src["name"] if src else "",
            "degrees_in_master": " | ".join(rec["degreeLists"]) if rec else "",
            "degrees_in_math_genealogy": " | ".join(d["text"] for d in src["degrees"]) if src else "",
            "advisors_in_master": len(rec["advisors"]) if rec else "",
            "advisors_in_math_genealogy": len(src["advisors"]) if src else "",
            "wikipedia": wd.get("wikiUrl", ""),
            "checked": today,
        })

    # ---- write ----------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    for name, table in (("connections.csv", rows), ("people.csv", people_rows)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)

    count = {s: sum(1 for r in rows if r["status"] == s)
             for s in ("confirmed", "new", "dataset_only", "local", "not_checked")}
    supported = sum(1 for r in rows if r["status"] == "dataset_only" and r["wikidata"])
    lines = [
        "# Connection audit, {}".format(today), "",
        "Every advisor-student connection in `master.json` was compared with the live",
        "Mathematics Genealogy Project record of the student, and the project was walked",
        "upward from every person in the file to find ancestors the file did not have.",
        "Wikidata (joined on the project id, property P549) is the second source.", "",
        "## Totals", "",
        "- People in `master.json` before the audit: {}".format(len(in_file)),
        "- Connections in `master.json` before the audit: {}".format(len(file_edges)),
        "- Confirmed by the project: {}".format(count["confirmed"]),
        "- New, found at the project and not in the file: {}".format(count["new"]),
        "- New people those connections bring in: {}".format(len(new_people)),
        "- In the file but not at the project today: {} ({} of them supported by Wikidata)".format(
            count["dataset_only"], supported),
        "- Local, not checkable at the project: {}".format(count["local"]),
        "- Not checked (record missing or not fetched): {}".format(count["not_checked"]),
        "- Mirror errors inside the file (advisors versus students lists): {}".format(len(mirror_errors)),
        "",
    ]

    def section(title, status, note):
        chosen = [r for r in rows if r["status"] == status]
        if not chosen:
            return
        lines.extend(["## " + title, "", note, ""])
        for r in chosen:
            extra = " (Wikidata: {})".format(r["wikidata"]) if r["wikidata"] else ""
            lines.append("- {} ({}) advised {} ({}){}".format(
                r["advisor"], r["advisor_id"], r["student"], r["student_id"], extra))
        lines.append("")

    section("New connections", "new",
            "At the project today and absent from the file before this audit.")
    section("In the file but not at the project", "dataset_only",
            "Kept in the file. These were either added by hand or have since been removed at the source.")
    section("Local connections", "local",
            "One end is a person who is not in the project's database.")
    if not_at_source or not_fetched:
        lines.extend(["## Records not available", ""])
        lines.extend("- id {} ({}): no such record at the project".format(i, name_of(i)) for i in sorted(not_at_source))
        lines.extend("- id {} ({}): not fetched".format(i, name_of(i)) for i in sorted(not_fetched))
        lines.append("")
    if mirror_errors:
        lines.extend(["## Mirror errors", ""] + ["- " + e for e in mirror_errors] + [""])
    with open(os.path.join(out_dir, "AUDIT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[8:18]))

    # ---- apply ------------------------------------------------------------------
    if not apply:
        return
    for person_id in new_people:
        src, wd = source[person_id], wikidata.get(str(person_id), {})
        record = {"id": person_id, "name": src["name"], "students": [], "advisors": []}
        if wd.get("wikiUrl"):
            record["wikiUrl"] = wd["wikiUrl"]
        if wd.get("wikiImage"):
            record["wikiImage"] = wd["wikiImage"]
        record["degreeLists"] = {d["text"]: (d["flags"][0] if d["flags"] else "") for d in src["degrees"]}
        record["isProcessed"] = True
        data[str(person_id)] = record
    for r in rows:
        if r["status"] != "new":
            continue
        student, advisor = data[str(r["student_id"])], data.get(str(r["advisor_id"]))
        if advisor is None:
            continue  # the advisor's own record could not be fetched
        if r["advisor_id"] not in student["advisors"]:
            student["advisors"].append(r["advisor_id"])
        if r["student_id"] not in advisor["students"]:
            advisor["students"].append(r["student_id"])
    with open(master_path, "w", encoding="utf-8") as f:
        f.write(mgplib.dump_master(data))
    print("master.json updated: {} people".format(len(data)))


if __name__ == "__main__":
    main()
