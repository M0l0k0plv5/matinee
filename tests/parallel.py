#!/usr/bin/env python3
"""All tests, several at once: each test class (a big one in parts) in a process of its own. The UI's classes each
start their own backend and Chromium and mostly wait for them, so side by side the whole run takes a fraction of
the time. With modules' names (parallel.py test_ui test_tv): only theirs. KINO_TEST_JOBS: how many at once
(default: two thirds of the cores, at least 2: more, and the tests that go by the clock fail now and then).
The longest parts first, by how long each took last time (kept in tests/.durations.json, not in git): ordered by their
number of tests, the few long ones (a walk over every page, minutes long) came last and the run waited for them alone."""
import json
import os
import re
import signal
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
PART = 25  # tests of a class run together at most (each part starts the class's backend anew)
DURATIONS = HERE / ".durations.json"
running = set()  # the parts' processes, to end them with this one


def part_name(ids):
    return ids[0].rsplit(".", 1)[0] + (f" ({len(ids)} of a part)" if len(ids) == PART else "")


def durations():
    try:
        d = json.loads(DURATIONS.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def parts(modules=()):
    """[[test ids]]: a class's tests (all, or those of the modules named), in parts of PART; the longest first (as
    they took last time; one not run before by its number of tests, before those known), so none is left for the end."""
    by_class = {}

    def walk(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                if not modules or type(t).__module__ in modules:
                    by_class.setdefault(f"{type(t).__module__}.{type(t).__name__}", []).append(t.id())
    walk(unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py"))
    chunks = [ids[i:i + PART] for ids in by_class.values() for i in range(0, len(ids), PART)]
    took = durations()
    # by the part's first test too: two parts of one class share a name
    return sorted(chunks, key=lambda ids: (took.get(ids[0]) is not None, -(took.get(ids[0]) or len(ids))))


def run(ids):
    start = time.time()
    p = subprocess.Popen([sys.executable, "-W", "ignore::ResourceWarning", "-m", "unittest", *ids],
                         cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    running.add(p)
    p.stderr = p.communicate()[1]  # unittest's report
    running.discard(p)
    return ids, p, time.time() - start


def stop(signum=None, frame=None):
    """Stopped (Ctrl+C, the terminal closed): the parts end too, each tidying up after itself."""
    for p in list(running):
        p.terminate()
    os._exit(130)


def main():
    jobs = int(os.environ.get("KINO_TEST_JOBS", max(2, (os.cpu_count() or 4) * 2 // 3)))
    modules = sys.argv[1:]
    unknown = [m for m in modules if not (HERE / f"{m}.py").is_file()]
    if unknown:
        sys.exit(f"no such test module: {', '.join(unknown)}")
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    todo, failed, ran, skipped, start = parts(modules), [], 0, 0, time.time()
    known = durations()
    with ThreadPoolExecutor(jobs) as pool:
        for f in as_completed([pool.submit(run, ids) for ids in todo]):
            ids, p, took = f.result()
            known[ids[0]] = round(took, 1)
            n = int((re.search(r"Ran (\d+) test", p.stderr) or [0, 0])[1])
            ran += n
            skipped += int((re.search(r"skipped=(\d+)", p.stderr) or [0, 0])[1])
            name = part_name(ids)
            print(f"{'ok  ' if p.returncode == 0 else 'FAIL'} {name}: {n} tests, {took:.0f} s", flush=True)
            if p.returncode:
                failed.append(name)
                print(p.stderr, flush=True)
    try:
        DURATIONS.write_text(json.dumps(known, indent=0, sort_keys=True))
    except OSError:
        pass  # only the order of the next run
    print(f"\n{ran} tests in {time.time() - start:.0f} s, {jobs} at once"
          + (f", {skipped} skipped" if skipped else "") + (f"; FAILED: {', '.join(failed)}" if failed else "; OK"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
