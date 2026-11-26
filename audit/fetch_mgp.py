"""Save the Mathematics Genealogy Project record page for every person in master.json.

    python3 audit/fetch_mgp.py master.json audit/cache            # people in the file
    python3 audit/fetch_mgp.py master.json audit/cache --closure  # plus every ancestor
                                                                  # the project lists

The run is resumable: a page already in the cache is not fetched again. Requests
are spaced by the 10 seconds the site's robots.txt asks for, so a first run over
250 people takes about 45 minutes.
"""
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mgplib  # noqa: E402


def fetch(person_id, cache_dir):
    request = urllib.request.Request(
        mgplib.MGP_URL.format(person_id), headers={"User-Agent": mgplib.USER_AGENT})
    with urllib.request.urlopen(request, timeout=40) as response:
        body = response.read()
    with open(mgplib.cached_path(cache_dir, person_id), "wb") as f:
        f.write(body)


def fetch_all(ids, cache_dir):
    """Fetch the ids that are not cached yet. Returns how many were fetched."""
    todo = [i for i in sorted(ids) if mgplib.read_cached(cache_dir, i) is None]
    for n, person_id in enumerate(todo, 1):
        try:
            fetch(person_id, cache_dir)
        except Exception as error:  # keep going; the id is retried on the next run
            print("FAILED id={} {}".format(person_id, error), flush=True)
        if n % 25 == 0 or n == len(todo):
            print("fetched {}/{}".format(n, len(todo)), flush=True)
        time.sleep(mgplib.CRAWL_DELAY)
    return len(todo)


def main():
    master_path, cache_dir = sys.argv[1], sys.argv[2]
    closure = "--closure" in sys.argv[3:]
    os.makedirs(cache_dir, exist_ok=True)
    known = {int(k) for k in mgplib.load_master(master_path)} - mgplib.LOCAL_IDS
    fetch_all(known, cache_dir)
    rounds, attempted = 0, set()
    while closure:
        # every advisor named on a cached page that we have not fetched yet
        wanted = set()
        frontier, seen = set(known), set()
        while frontier:
            person_id = frontier.pop()
            seen.add(person_id)
            record = mgplib.read_cached(cache_dir, person_id)
            if record is None:
                wanted.add(person_id)
                continue
            for advisor in record["advisors"]:
                if advisor["id"] not in seen:
                    frontier.add(advisor["id"])
        wanted -= attempted  # an id that failed once is left for the next run
        if not wanted:
            break
        attempted |= wanted
        rounds += 1
        print("closure round {}: {} new ancestor record(s)".format(rounds, len(wanted)), flush=True)
        fetch_all(wanted, cache_dir)
    print("done", flush=True)


if __name__ == "__main__":
    main()
