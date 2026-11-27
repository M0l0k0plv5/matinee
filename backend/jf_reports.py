"""Playback reports to Jellyfin: those that could not be sent (the end of something played) kept and sent once
the server is there again."""
import json
import re
import sys
import threading
import time
import urllib.error
from datetime import datetime

from .core import FLUSH_SECONDS, PENDING_FILE, PENDING_GIVE_UP, state, write_private
from .jellyfin_api import jf, ready


pending_lock = threading.Lock()


def report(path, body, critical=False):
    """Report playback to Jellyfin. Critical reports (end of playback) are stored on network
    errors and retried later; progress updates are not."""
    try:
        jf(path, "POST", body)
    except Exception as e:  # reporting must never interrupt playback
        print(f"Failed to report {path}: {e}", file=sys.stderr)
        if critical:
            with pending_lock:
                q = load_pending()
                q.append({"path": path, "body": dict(body), "at": time.time()})
                try:
                    save_pending(q)
                except OSError as e:  # the disk full: this report is lost, but not what comes after it
                    print(f"Report {path} not kept for later: {e}", file=sys.stderr)


def load_pending():
    try:
        return json.loads(PENDING_FILE.read_text())
    except (OSError, ValueError):
        return []


def save_pending(q):
    write_private(PENDING_FILE, json.dumps(q))


def parse_jf_date(s):
    # Jellyfin sends up to 7 fractional digits, Python accepts at most 6
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s).timestamp()


def flush_pending_loop():
    """Send stored reports once the server is reachable again."""
    while True:
        ready.wait()
        with pending_lock:
            q = load_pending()
        done = []
        for r in q:
            try:
                item = jf(f"/Items/{r['body']['ItemId']}?userId={state['user_id']}")
                last = item.get("UserData", {}).get("LastPlayedDate")
                # if someone kept watching elsewhere in the meantime, our report is outdated
                if not (last and parse_jf_date(last) > r["at"] + 60):
                    jf(r["path"], "POST", r["body"])
                    print(f"Sent stored report {r['path']} for {item.get('Name')}", file=sys.stderr)
                done.append(r)
            except urllib.error.HTTPError as e:
                # item no longer exists, or the server has been rejecting the report for too long
                if e.code == 404 or time.time() - r["at"] > PENDING_GIVE_UP:
                    print(f"Dropped stored report {r['path']}: server responded with {e.code}", file=sys.stderr)
                    done.append(r)
            except Exception:
                break  # server not reachable yet, try again later
        if done:
            try:
                with pending_lock:
                    save_pending([r for r in load_pending() if r not in done])
                live_events.broadcast("changed")
            except OSError as e:  # the disk full: those sent are tried again next time, the loop goes on
                print(f"Stored reports not updated: {e}", file=sys.stderr)
        time.sleep(FLUSH_SECONDS)


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import trickplay, live_events  # noqa: E402,F401
