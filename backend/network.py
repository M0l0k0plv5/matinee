"""The network from the sofa: Wi-Fi through NetworkManager (nmcli), and the Jellyfin servers on the local network
(Jellyfin's own discovery: a broadcast on UDP port 7359), so a TV computer is set up without a terminal."""
import json
import os
import re
import shutil
import socket
import subprocess
import time

NMCLI = os.environ.get("KINO_NMCLI", "nmcli")  # the tests: a fake
WIFI_WAIT = 45  # s: joining a network (asking for its address too) takes a while
SSID_MAX = 32  # bytes, as Wi-Fi allows
WIFI_PSK = re.compile(r"[\x20-\x7e]{8,63}")  # WPA's passphrase: 8 to 63 printable characters


def nmcli(*args, timeout=15):
    """nmcli's answer in its terse form, and whether it went well."""
    try:
        r = subprocess.run([NMCLI, "-t", *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    return r.stdout if not r.returncode else r.stderr, not r.returncode


def nm_fields(line):
    """One line of nmcli -t: its fields, split at the colons not escaped with a backslash."""
    fields, cur, escaped = [], "", False
    for ch in line:
        if escaped:
            cur += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    fields.append(cur)
    return fields


def wifi_there():
    """Whether Matinee can set up Wi-Fi here: NetworkManager running, with a Wi-Fi device."""
    if not shutil.which(NMCLI):
        return False
    out, ok = nmcli("-f", "TYPE", "device", timeout=5)
    return ok and "wifi" in out.split()


def wifi_known():
    out, ok = nmcli("-f", "NAME,TYPE", "connection", "show")
    if not ok:
        return set()
    return {f[0] for f in map(nm_fields, out.splitlines()) if len(f) >= 2 and f[1] == "802-11-wireless"}


def wifi_networks(rescan=False):
    """{"available", "networks": [{"ssid", "signal", "secure", "active", "known"}]}: the one in use first, then the
    strongest; each name once (its strongest)."""
    if not wifi_there():
        return {"available": False, "networks": []}
    out, ok = nmcli("-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes" if rescan else "auto",
                    timeout=30)
    known = wifi_known()
    best = {}
    for f in map(nm_fields, out.splitlines() if ok else []):
        if len(f) < 4 or not f[1]:
            continue  # a hidden network: no name to choose it by
        n = {"ssid": f[1], "signal": int(f[2]) if f[2].isdigit() else 0, "secure": f[3] not in ("", "--"),
             "active": f[0] == "*", "known": f[1] in known}
        was = best.setdefault(n["ssid"], n)  # several access points of one network: its strongest, in use if one is
        was.update(signal=max(was["signal"], n["signal"]), active=was["active"] or n["active"])
    nets = sorted(best.values(), key=lambda n: (not n["active"], -n["signal"], n["ssid"].lower()))
    return {"available": True, "networks": nets[:30]}


def wifi_ok_ssid(ssid):
    return isinstance(ssid, str) and 0 < len(ssid.encode()) <= SSID_MAX and "\n" not in ssid


def wifi_connect(ssid, password=None):
    """Join a network: a known one as it is kept, another with its password (kept by NetworkManager from then on).
    None when joined, else what went wrong: "wifi_password" (refused), "wifi_failed"."""
    if ssid in wifi_known() and password is None:
        out, ok = nmcli("--wait", str(WIFI_WAIT), "connection", "up", "id", ssid, timeout=WIFI_WAIT + 10)
    else:
        if ssid in wifi_known():  # a new password for a network known: the old one goes
            nmcli("connection", "delete", "id", ssid)
        args = ["--wait", str(WIFI_WAIT), "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        out, ok = nmcli(*args, timeout=WIFI_WAIT + 10)
    if ok:
        return None
    low = out.lower()
    if "secrets were required" in low or "802-11-wireless-security.psk" in low or "property is invalid" in low:
        nmcli("connection", "delete", "id", ssid)  # not kept with a wrong password, asked anew the next time
        return "wifi_password"
    print(f"Wi-Fi: joining failed ({out.strip()[-200:]})", flush=True)
    return "wifi_failed"


def wifi_forget(ssid):
    return nmcli("connection", "delete", "id", ssid)[1]


# Jellyfin answers "who is JellyfinServer?" on UDP port 7359 with {"Address", "Id", "Name", "EndpointAddress"}
JF_DISCOVER = os.environ.get("KINO_JF_DISCOVER", "255.255.255.255:7359")  # the tests: a fake server's address
JF_DISCOVER_WAIT = float(os.environ.get("KINO_JF_DISCOVER_WAIT", "1.5"))


def jf_discover():
    """The Jellyfin servers that answer on the local network: [{"name", "address"}], each once."""
    host, _, port = JF_DISCOVER.rpartition(":")
    found = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(b"who is JellyfinServer?", (host, int(port)))
            end = time.monotonic() + JF_DISCOVER_WAIT
            while (left := end - time.monotonic()) > 0:
                s.settimeout(left)
                try:
                    data, _ = s.recvfrom(4096)
                except socket.timeout:
                    break
                try:
                    d = json.loads(data)
                except ValueError:
                    continue
                address = d.get("Address") if isinstance(d, dict) else None
                if isinstance(address, str) and re.fullmatch(r"https?://[^\s/]+(/\S*)?", address):
                    found.setdefault(str(d.get("Id") or address), {"name": str(d.get("Name") or address)[:80],
                                                                   "address": address.rstrip("/")})
    except OSError as e:  # no network at all
        print(f"Jellyfin not looked for on the network: {e}", flush=True)
    return sorted(found.values(), key=lambda x: x["name"].lower())
