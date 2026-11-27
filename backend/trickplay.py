"""Pictures for seeking: Jellyfin's trickplay tiles cut into single pictures for mpv's overlay."""
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
from pathlib import Path

from .core import CUT_SHORT, RUNTIME
from .jellyfin_api import jf


def jpeg_size(data):
    """A JPEG's width and height, from its frame header."""
    i = 2
    while i + 9 < len(data):
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    raise ValueError("no JPEG frame header")


class Previews:
    """Pictures for seeking (Jellyfin's trickplay, if it made them): kino.lua asks for the one where the mark is
    (kino-preview index height); its tile of pictures is fetched once, and each picture written as raw BGRA in that
    height (`{height}-{index}.bgra` in a folder of this playback), for mpv's overlay-add. Raw pictures are big (a
    tile's hundred about 33 MB on a 1080p screen, 130 MB in 4K) and the folder is in memory (XDG_RUNTIME_DIR): only
    those of the tiles asked for last are kept, an older one's are cut out again when the mark comes back."""
    KEEP = 3

    def __init__(self, item):
        source = (item.get("MediaSources") or [{}])[0].get("Id")
        sizes = (item.get("Trickplay") or {}).get(source) or {}
        width = min(sizes, key=lambda w: abs(int(w) - 320))  # enough for a picture over the bar
        self.info = sizes[width]
        self.path = f"/Videos/{item['Id']}/Trickplay/{width}/{{tile}}.jpg?MediaSourceId={source}"
        self.dir = Path(tempfile.mkdtemp(prefix="kino-preview-", dir=RUNTIME))
        self.asked, self.lock = [], threading.Lock()  # the tiles cut apart, the one asked for last at the end

    @staticmethod
    def of(item):
        """Previews for an item, or None (no trickplay pictures, or no ffmpeg to cut them apart)."""
        source = (item.get("MediaSources") or [{}])[0].get("Id")
        if not (item.get("Trickplay") or {}).get(source) or not shutil.which("ffmpeg"):
            return None
        return Previews(item)

    def for_mpv(self):
        i = self.info
        return {"dir": str(self.dir), "interval": i["Interval"], "count": i["ThumbnailCount"],
                "w": i["Width"], "h": i["Height"]}

    def want(self, index, height):
        per = self.info["TileWidth"] * self.info["TileHeight"]
        key = (index // per, height)
        with self.lock:
            if not 0 <= index < self.info["ThumbnailCount"] or not 40 <= height <= 1000:
                return
            if key in self.asked:
                self.asked.remove(key)
                self.asked.append(key)
                return
            self.asked.append(key)
            old = self.asked[:-self.KEEP]
            del self.asked[:-self.KEEP]
        for tile, h in old:
            self.drop(tile, h)
        threading.Thread(target=self.make, args=key, daemon=True).start()

    def drop(self, tile, height):
        per = self.info["TileWidth"] * self.info["TileHeight"]
        for index in range(tile * per, (tile + 1) * per):
            (self.dir / f"{height}-{index}.bgra").unlink(missing_ok=True)

    def make(self, tile, height):
        i = self.info
        try:
            jpg = jf(self.path.format(tile=tile), raw=True).body
            w, h = jpeg_size(jpg)  # the last tile may hold fewer rows
            part = self.dir / f"part-{tile}-{height}"
            part.mkdir()
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "jpeg_pipe", "-i", "pipe:0", "-vf",
                            f"untile={w // i['Width']}x{max(1, h // i['Height'])},"
                            f"scale={int(height * i['Width'] / i['Height'] + 0.5)}:{height}",
                            "-pix_fmt", "bgra", "-c:v", "rawvideo", "-f", "image2",
                            "-start_number", str(tile * i["TileWidth"] * i["TileHeight"]), str(part / f"{height}-%d.bgra")],
                           input=jpg, check=True, timeout=30, capture_output=True)
            for f in part.iterdir():  # whole files only: kino.lua shows a picture as soon as its file is there
                f.rename(self.dir / f.name)
            part.rmdir()
            with self.lock:
                kept = (tile, height) in self.asked
            if not kept:  # dropped while it was made
                self.drop(tile, height)
        except (OSError, ValueError, subprocess.SubprocessError, urllib.error.URLError, CUT_SHORT) as e:
            print(f"Trickplay pictures {tile} not made: {e}", file=sys.stderr)
            shutil.rmtree(self.dir / f"part-{tile}-{height}", ignore_errors=True)
            with self.lock:  # asked for again the next time the mark is there
                if (tile, height) in self.asked:
                    self.asked.remove((tile, height))

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import handoff  # noqa: E402,F401
