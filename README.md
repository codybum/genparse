# genparse

An academic genealogy stored as a graph. People are nodes, and an edge runs from an advisor to a student. The graph starts at V. K. Cody Bumgardner (Ph.D., University of Kentucky, 2017, advisor Victor W. Marek) and follows every advisor back as far as the records go.

As audited on 2026-11-25 the graph holds 303 people and 380 connections: 301 ancestors across 35 generations, back to the tenth century, and one academic descendant. The audit is in [`audit/AUDIT.md`](audit/AUDIT.md). The data as it stood before the audit is the tag `data-2018-08-16`.

## The data

`master.json` is one JSON object keyed by person id. Each person has these fields.

| Field | Meaning |
|---|---|
| `id` | The person's id |
| `name` | Name |
| `students` | Ids of this person's students who are in the graph |
| `advisors` | Ids of this person's advisors |
| `degreeLists` | Each degree (degree, institution, year) with the flag of the country that awarded it |
| `wikiUrl` | Optional. Wikipedia page |
| `wikiImage` | Optional. Portrait |
| `isProcessed` | True once the record has been filled in |

## Where the data comes from

The ids are [Mathematics Genealogy Project](https://www.mathgenealogy.org) ids. Person `489` is the record at `https://www.mathgenealogy.org/id.php?id=489`.

People who are not in the project's database have small ids of this file's own. They are listed in `audit/local_people.json`, each with the source that records the connection.

| Id | Person | Advisor | Source |
|---|---|---|---|
| `1` | V. K. Cody Bumgardner, Ph.D. University of Kentucky 2017 | Victor W. Marek (`22583`) | [Wikipedia, Victor W. Marek](https://en.wikipedia.org/wiki/Victor_W._Marek) |
| `2` | Mahmut Selman Gokmen, Ph.D. University of Kentucky 2026 | V. K. Cody Bumgardner (`1`) | [University of Kentucky dissertation record](https://uknowledge.uky.edu/cs_etds/159) |

The graph was first pulled from the project in 2017 and 2018. Wikipedia links, portraits, and some names and degree lines were then added or edited by hand. The flag names in `degreeLists` are the project's own (`img/flags/...`).

## Auditing against the source

The `audit` folder checks every person and every connection in `master.json` against the project's live records and finds ancestors the file does not have yet. It needs Python 3 and nothing else.

```
python3 audit/fetch_mgp.py master.json audit/cache --closure
python3 audit/fetch_wikidata.py audit/cache master.json
python3 audit/check_links.py master.json audit/links.csv
python3 audit/audit_network.py master.json audit/cache audit
python3 audit/verify.py master.json audit/cache
python3 audit/check_links.py master.json audit/links_after.csv
```

1. `fetch_mgp.py` saves the project's record page for each person, then for each ancestor the project lists who is not in the file. It waits 10 seconds between requests, as the site's robots.txt asks, so a first run takes about 45 minutes. It can be stopped and restarted.
2. `fetch_wikidata.py` gets a second opinion. Wikidata stores each person's project id, so the match is exact.
3. `check_links.py` tests every Wikipedia page and portrait address in the file.
4. `audit_network.py` writes the record of the audit.
5. `verify.py` checks `master.json` against the saved source pages with its own, separate reading of them, and fails if the file and the source disagree anywhere.

| File | Contents |
|---|---|
| `audit/AUDIT.md` | The summary and every finding |
| `audit/connections.csv` | One row per connection with its status, the date checked and the source record |
| `audit/people.csv` | One row per person, with the name, degrees and Wikipedia page in the file beside those at the sources |
| `audit/changes.csv` | Every change the audit made to `master.json` |
| `audit/links.csv` | One row per Wikipedia page or portrait address in the file before the audit, with the answer the server gave |
| `audit/links_after.csv` | The same check on the file after the audit. Every address answers |
| `audit/source_records.json` | What the project's record of each person said when it was fetched |
| `audit/wikidata.json` | What Wikidata and Wikipedia returned for each person |

A connection is `confirmed` (in the file and at the project), `new` (at the project and not yet in the file), `dataset_only` (in the file, not at the project today, and kept on other evidence), `withdrawn` (in the first import and since dropped by the project, with no other source), `local` (one end is not in the project's database) or `wikidata_only` (an advisor only Wikidata lists, recorded and not added). The project labels an advisor in several ways, such as "Advisor 1", "Doctoral advisor" and "Mentor". All of them count, and `connections.csv` records which label each connection carries.

The audit also checks the file against itself. Every advisor entry must have a matching student entry, every person must be tied to id `1` as an ancestor or a descendant, there must be no cycles and no person entered twice, and an advisor's dates must not follow the student's.

Adding `--apply` to the audit command updates `master.json`. It adds the new connections and the people they bring in, and fills fields that were empty. It removes a connection only when the project has withdrawn it and no other source supports it, and a person only when that leaves them no longer an ancestor. A Wikipedia link is corrected only when it does not lead to the person, when its address is malformed, or when an English page now exists and the file points to another language. A portrait address is corrected when it no longer answers. Names and degree lines already in the file are never changed. Every change, with the old value, is written to `audit/changes.csv`.

Three more files feed the audit. `audit/import_2017_edges.csv` is the first import, of September 2017, which tells a connection added by hand from one the source has since dropped. `audit/wikipedia_manual.csv` holds the Wikipedia links settled by hand, each with its reason. `audit/keep_connections.csv` holds connections kept although the project no longer lists them, each with the evidence.

## Loading the graph into OrientDB

`Launcher.py` and `DBTools.py` load `master.json` into an OrientDB graph database and run shortest-path queries.

Assuming you are using Ubuntu, dependencies can be installed as follows.

1. Python 3 should be preinstalled. If not, run `sudo apt-get install python3`
2. Make sure pip3 is installed: `sudo apt-get install python3-pip`
3. Install the OrientDB driver: `pip3 install pyorient`

OrientDB version 2.2 must be running. Version 3.x does not work with this driver. `Launcher.py` has the Docker command.

To run: `python3 Launcher.py`
