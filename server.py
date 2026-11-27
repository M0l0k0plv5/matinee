#!/usr/bin/env python3
"""Matinee backend: Jellyfin, Navidrome, Audiobookshelf and Podfetch behind one UI; mpv control and
playback reporting.

The code is in backend/, one module per part; this starts it."""
import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from backend import display
from backend.core import BOOT_CLOCK, PORT
from backend.jellyfin_api import login_loop
from backend.sound import bt_reconnect_loop, bt_stereo_only
from backend.jf_reports import flush_pending_loop
from backend.library import similar_loop
from backend.live_events import ws_loop
from backend.tv import own_warm_loop
from backend.syncing import sync
from backend import remote_shelf, phone_remote
from backend.system import (autooff_loop, exit_with_parent, menu_key_loop, players_left_over, sockets_left_over,
    stop_players, ui_watchdog)
from backend.updates import autoupdate_loop, check_probation, DESKTOP_UPDATES, UPDATES
from backend.http_api import Handler


if __name__ == "__main__":
    for pid in players_left_over():
        print(f"Stopping a player left over by an earlier Matinee ({pid})", file=sys.stderr)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for path in sockets_left_over():
        path.unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, stop_players)
    signal.signal(signal.SIGINT, stop_players)
    if os.environ.get("KINO_EXIT_WITH_PARENT"):  # the tests: a test run killed (Ctrl+C) leaves no backend behind
        threading.Thread(target=exit_with_parent, args=(os.getppid(),), daemon=True).start()
    if UPDATES:
        check_probation()  # before the watchdog looks at it
    threading.Thread(target=login_loop, daemon=True).start()
    threading.Thread(target=ws_loop, daemon=True).start()
    threading.Thread(target=flush_pending_loop, daemon=True).start()
    threading.Thread(target=similar_loop, daemon=True).start()
    threading.Thread(target=sync.loop, daemon=True).start()
    threading.Thread(target=remote_shelf.loop, daemon=True).start()
    phone_remote.apply()  # the phone remote in the home network, if turned on (phone_remote.py)
    threading.Thread(target=lambda: (display.IN_CAGE and bt_stereo_only(), bt_reconnect_loop()), daemon=True).start()
    threading.Thread(target=own_warm_loop, daemon=True).start()
    if os.environ.get("KINO_SESSION") == "1":  # cage only: on a desktop a hung Chromium is the user's to close
        threading.Thread(target=ui_watchdog, daemon=True).start()
    if display.IN_CAGE:  # a refresh rate left from a film whose end was not seen (Matinee ended while it played)
        threading.Thread(target=display.display_back, daemon=True).start()
    if display.IN_CAGE:  # on a desktop the computer is not Matinee's to switch off
        threading.Thread(target=autooff_loop, daemon=True).start()
        threading.Thread(target=menu_key_loop, daemon=True).start()  # a remote's Menu key, which Chromium drops
    if UPDATES or DESKTOP_UPDATES:
        threading.Thread(target=autoupdate_loop, daemon=True).start()
    print(f"Matinee running on http://127.0.0.1:{PORT} ({time.clock_gettime(BOOT_CLOCK):.1f} s after boot)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
