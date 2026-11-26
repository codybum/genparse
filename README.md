# genparse

An academic genealogy stored as a graph. People are nodes, and an edge runs from an advisor to a student. The graph starts at V. K. Cody Bumgardner (Ph.D., University of Kentucky, 2017, advisor Victor W. Marek) and follows every advisor back as far as the records go.

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

The ids are [Mathematics Genealogy Project](https://www.mathgenealogy.org) ids. Person `489` is the record at `https://www.mathgenealogy.org/id.php?id=489`. The one exception is id `1`, V. K. Cody Bumgardner, who is not in the project's database.

The graph was first pulled from the project in 2017 and 2018. Wikipedia links, portraits, and some names and degree lines were then added or edited by hand. The flag names in `degreeLists` are the project's own (`img/flags/...`).

## Auditing against the source

The `audit` folder checks every connection in `master.json` against the project's live records and finds ancestors the file does not have yet. It needs Python 3 and nothing else.

```
python3 audit/fetch_mgp.py master.json audit/cache --closure
python3 audit/fetch_wikidata.py audit/cache
python3 audit/audit_connections.py master.json audit/cache audit
```

1. `fetch_mgp.py` saves the project's record page for each person, then for each ancestor the project lists who is not in the file. It waits 10 seconds between requests, as the site's robots.txt asks, so a first run takes about 45 minutes. It can be stopped and restarted.
2. `fetch_wikidata.py` gets a second opinion. Wikidata stores each person's project id, so the match is exact.
3. `audit_connections.py` writes the record of the audit.

| File | Contents |
|---|---|
| `audit/connections.csv` | One row per connection with its status, the date checked and the source record |
| `audit/people.csv` | One row per person, with the name and degrees in the file beside those at the project |
| `audit/AUDIT.md` | The summary |

A connection is `confirmed` (in the file and at the project), `new` (at the project and not yet in the file), `dataset_only` (in the file and not at the project today) or `local` (one end is not in the project's database).

Adding `--apply` to the last command writes the new connections and the people they bring in to `master.json`. Nothing is ever removed.

## Loading the graph into OrientDB

`Launcher.py` and `DBTools.py` load `master.json` into an OrientDB graph database and run shortest-path queries.

Assuming you are using Ubuntu, dependencies can be installed as follows.

1. Python 3 should be preinstalled. If not, run `sudo apt-get install python3`
2. Make sure pip3 is installed: `sudo apt-get install python3-pip`
3. Install the OrientDB driver: `pip3 install pyorient`

OrientDB version 2.2 must be running. Version 3.x does not work with this driver. `Launcher.py` has the Docker command.

To run: `python3 Launcher.py`
