"""The sleep timer: stop after a while or at the end, for movies, videos, music and books alike."""
import os
import subprocess
import sys
import time

from . import display
from .core import t
from .preferences import setting


class SleepTimer:
    """Stop after a while (30, 60, 90 minutes) or "at the end" (of the movie or episode, of the music's queue),
    for movies, videos and music alike; a book also "at the end of the chapter". When the time is up the sound
    fades out over FADE seconds and playback stops; on the thin client it can shut down afterwards ("sleepend"
    in the settings)."""
    STEPS = [30, 60, 90, "end", None]
    BOOK_STEPS = [30, 60, 90, "chapter", "end", None]
    FADE = float(os.environ.get("KINO_SLEEP_FADE", "10"))
    UNIT = float(os.environ.get("KINO_SLEEP_UNIT", "60"))  # seconds per minute, shortened in the tests

    def __init__(self):
        self.mode = None   # minutes, "end" or None
        self.until = None

    def set(self, mode):
        self.mode = mode
        self.until = time.time() + mode * self.UNIT if isinstance(mode, (int, float)) else None

    def cycle(self, book=False):
        steps = self.BOOK_STEPS if book else self.STEPS
        self.set(steps[(steps.index(self.mode) + 1) % len(steps)] if self.mode in steps else 30)

    @property
    def at_end(self):
        return self.mode == "end"

    @property
    def at_chapter(self):
        return self.mode == "chapter"

    def due(self):
        return self.until is not None and time.time() >= self.until

    def label(self):
        if self.mode is None:
            return t("sleep_off")
        if self.at_end:
            return t("sleep_end")
        if self.at_chapter:
            return t("sleep_chapter")
        return t("sleep_min", m=max(1, round((self.until - time.time()) / self.UNIT)))

    def state(self):
        if self.mode is None:
            return None
        if self.at_chapter:
            return {"chapter": True}
        return {"end": True} if self.at_end else {"left": max(0, int((self.until - time.time()) * 60 / self.UNIT))}

    def fade(self, mpv):
        """Turn the sound down to nothing; returns the volume it had, which stays the one kept."""
        start = mpv.get("volume") or 0
        steps = max(1, int(self.FADE * 4))
        for i in range(1, steps + 1):
            mpv.command("set_property", "volume", start * (1 - i / steps))
            time.sleep(self.FADE / steps)
        return start

    def done(self):
        """Playback has stopped for the timer."""
        print("Sleep timer: stopped", file=sys.stderr)
        self.set(None)
        if display.IN_CAGE and setting("sleepend") == "poweroff":
            subprocess.Popen(["systemctl", "poweroff"])


sleep_timer = SleepTimer()


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import jf_reports  # noqa: E402,F401
