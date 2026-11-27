"""mpv's JSON IPC: the client the players talk to mpv with, and texts for its on-screen messages."""
import json
import socket
import time


class Mpv:
    """Minimal client for mpv's JSON IPC; keeps any events that arrive in between
    and the latest value of every observed property."""

    previews = None  # the pictures for seeking of what plays in this mpv (trickplay.Previews)

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(str(path))
        self.sock.settimeout(5)
        self.buf = b""
        self.rid = 0
        self.events = []
        self.props = {}

    def _message(self):
        """Read the next message from mpv; events are kept, everything else is returned."""
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("mpv exited")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        msg = json.loads(line)
        if msg.get("event") == "property-change":
            self.props[msg["name"]] = msg.get("data")
        if "event" in msg:
            if msg.get("event") == "client-message" and (msg.get("args") or [None])[0] == "kino-active":
                system.note_active()  # a key in the player (input.conf)
            self.events.append(msg)
            return None
        return msg

    def command(self, *args):
        self.rid += 1
        self.sock.sendall(json.dumps({"command": list(args), "request_id": self.rid}).encode() + b"\n")
        while True:
            msg = self._message()
            if msg and msg.get("request_id") == self.rid:
                return msg.get("data")

    def get(self, prop):
        return self.command("get_property", prop)

    def observe(self, *props):
        """Only the events Matinee needs: changes of these properties and messages from kino.lua."""
        self.command("disable_event", "all")
        self.command("enable_event", "property-change")
        self.command("enable_event", "client-message")
        for i, prop in enumerate(props, 1):
            self.command("observe_property", i, prop)

    def wait(self, timeout):
        """Wait up to `timeout` seconds for an event, so the end of a file or a key press is handled at once."""
        end = time.monotonic() + timeout
        try:
            while not self.events:
                left = end - time.monotonic()
                if left <= 0:
                    return
                self.sock.settimeout(left)
                self._message()
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self.sock.settimeout(5)

    def wait_event(self, name, timeout):
        """The first event of this name among those kept or arriving within `timeout` seconds, taken out of them;
        None if none comes."""
        end = time.monotonic() + timeout
        try:
            while True:
                for i, e in enumerate(self.events):
                    if e.get("event") == name:
                        return self.events.pop(i)
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self.sock.settimeout(left)
                self._message()
        except (socket.timeout, TimeoutError):
            return None
        finally:
            self.sock.settimeout(5)

    def close(self):
        self.sock.close()


def osd_text(text):
    """A text for mpv's on-screen messages: mpv expands ${...} in them, so an episode's name like
    "${stream-open-filename}" would put the stream's address with its key on the screen. "$$" is a plain "$"."""
    return text.replace("$", "$$")


# The parts after this one, loaded now that all of it is there: the next first, which loads the one after
# it the same way, so each finds the ones before it complete as it loads
from . import sleeping, system  # noqa: E402,F401
