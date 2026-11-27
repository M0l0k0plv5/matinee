#!/usr/bin/env python3
"""Matinee's versions as signed packages, for devices that are no git checkout.

A version is a tar.gz of the repository at one commit with a file RELEASE in it ({"commit", "version", "at",
"changes"}). Where the newest is said by manifest.json beside the packages ({"commit", "version", "at", "archive",
"sha256", "size", "commits": the last ones newest first, "changes"}), signed with Matinee's release key
(manifest.json.sig, by ssh-keygen -Y sign: nothing but OpenSSH needed to check it). A device keeps its versions side
by side, the one in use behind a link: ~/kino -> ~/.kino-versions/<commit>. An update unpacks the new one next to the
others and turns the link; going back turns it back.

Only the standard library and no other part of Matinee: kino and deploy/nightly-update run it as a script
("python3 backend/release.py check|newest|install|head|switch <commit>|build …"), the backend imports it.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # the version running (or the checkout)
RELEASE_FILE = "RELEASE"
NAMESPACE = "kino-release"  # what the release key signs, so a signature made for anything else does not count
SIGNER = "kino"
MANIFEST_URL = os.environ.get(
    "KINO_RELEASES", "https://github.com/M0l0k0plv5/matinee-releases/releases/latest/download/manifest.json")
SIGNERS = Path(os.environ.get("KINO_RELEASE_SIGNERS", HERE / "deploy" / "release-signers"))
COMMIT_ID = re.compile(r"[0-9a-f]{40}")
KEEP = 3  # versions kept on the device: the one in use and those before it, to go back to
TIMEOUT = float(os.environ.get("KINO_RELEASE_TIMEOUT", "30"))
MANIFEST_MAX = 1 << 20
PACKAGE_MAX = 512 << 20  # a package bigger than this is no Matinee: never filled into the disk (its size said or not)
COMMITS_LISTED = 500  # in the manifest: how far behind a device can still be counted
CHANGES_LISTED = 30


class ReleaseError(Exception):
    """An update that cannot be had: its code ("fetch", "signature", "package", "older") says why."""


def is_release(root=HERE):
    """Whether this version came as a package (not a checkout): a RELEASE file, inside a versions directory."""
    return (root / RELEASE_FILE).is_file() and root.parent.name.endswith("-versions") and not (root / ".git").exists()


def installed(root=HERE):
    """The RELEASE of the version running: {"commit", "version", "at", "changes"} ({} when it cannot be read)."""
    try:
        data = json.loads((root / RELEASE_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def versions_dir(link):
    return link.parent / f".{link.name}-versions"


def link_of(root=HERE):
    """The link the versions are used through (~/kino for ~/.kino-versions/<commit>)."""
    name = root.parent.name
    return root.parent.parent / name[1:-len("-versions")]


def head(link=None):
    """The commit in use: the version the link points to."""
    link = link or link_of()
    try:
        return Path(os.readlink(link)).name
    except OSError:
        return None


def get(url, limit=None):
    req = urllib.request.Request(url, headers={"User-Agent": "Matinee"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read(limit + 1 if limit else -1)
    if limit and len(data) > limit:
        raise ReleaseError("fetch")
    return data


def verify(data, sig, signers=SIGNERS):
    """Whether `sig` is the release key's signature of `data`."""
    with tempfile.TemporaryDirectory() as d:
        sig_file = Path(d) / "sig"
        sig_file.write_bytes(sig)
        try:
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(signers), "-I", SIGNER, "-n", NAMESPACE,
                                "-s", str(sig_file)], input=data, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
    return r.returncode == 0


def manifest(url=MANIFEST_URL, signers=SIGNERS):
    """The newest version as its signed manifest says it; ReleaseError when it cannot be had or is not signed."""
    try:
        data, sig = get(url, MANIFEST_MAX), get(url + ".sig", 1 << 16)
    except (OSError, ValueError) as e:
        raise ReleaseError("fetch") from e
    if not verify(data, sig, signers):
        raise ReleaseError("signature")
    try:
        m = json.loads(data)
    except ValueError as e:
        raise ReleaseError("signature") from e
    if not (isinstance(m, dict) and COMMIT_ID.fullmatch(str(m.get("commit", ""))) and isinstance(m.get("archive"), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(m.get("sha256", ""))) and isinstance(m.get("at"), int)):
        raise ReleaseError("signature")
    m["commits"] = [c for c in m.get("commits", []) if isinstance(c, str) and COMMIT_ID.fullmatch(c)]
    m["changes"] = [c for c in m.get("changes", []) if isinstance(c, dict) and isinstance(c.get("at"), int)
                    and isinstance(c.get("s"), str)]
    m["url"] = url
    return m


def behind(m, commit):
    """How many versions a commit is behind the manifest's newest (None if it is not among those listed)."""
    if commit == m["commit"]:
        return 0
    return m["commits"].index(commit) if commit in m["commits"] else None


def older(m, here):
    """Whether the manifest's version is older than the one installed (its RELEASE): signed, but one from before,
    as whoever has the releases' place but not the key could serve again, with what was fixed since."""
    return bool(here.get("commit")) and here["commit"] != m["commit"] and here["commit"] not in m["commits"] \
        and isinstance(here.get("at"), int) and m["at"] < here["at"]


def check(root=HERE, m=None):
    """What the settings show of an update, as for a checkout: {"behind", "version", "date", "at", "installed",
    "changes", "newest"}."""
    m = m or manifest()
    here = installed(root)
    n = 0 if older(m, here) else behind(m, here.get("commit"))  # an older one offered: nothing to update to
    since = here.get("at") or 0
    return {"behind": (len(m["commits"]) or 1) if n is None else n, "version": m.get("version") or m["commit"][:9],
            "date": time.strftime("%Y-%m-%d", time.gmtime(m["at"])), "at": m["at"], "installed": here.get("at"),
            "changes": [c for c in m["changes"] if c["at"] > since][:CHANGES_LISTED] if n != 0 else [],
            "newest": m["commit"]}


def unpack(m, into):
    """The manifest's package, checked against its sha256, unpacked as versions/<commit>; that directory."""
    target = into / m["commit"]
    if (target / RELEASE_FILE).is_file():
        return target  # there already (a version gone back from, or installed before)
    into.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=into) as d:
        archive = Path(d) / "kino.tar.gz"
        # as much as the signed manifest says, never more: whoever has the releases' place but not the key cannot
        # fill the disk with an endless one before its sha256 is seen to be wrong
        size = m.get("size")
        limit = size if isinstance(size, int) and 0 < size <= PACKAGE_MAX else PACKAGE_MAX
        got = 0
        try:
            req = urllib.request.Request(urllib.parse.urljoin(m["url"], m["archive"]), headers={"User-Agent": "Matinee"})
            sha = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(archive, "wb") as out:
                while chunk := resp.read(1 << 16):
                    got += len(chunk)
                    if got > limit:
                        raise ReleaseError("package")
                    sha.update(chunk)
                    out.write(chunk)
        except (OSError, ValueError) as e:
            raise ReleaseError("fetch") from e
        if sha.hexdigest() != m["sha256"]:
            raise ReleaseError("package")
        part = Path(d) / "kino"
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(part, filter="data")  # nothing outside it, no devices, no setuid
        except (OSError, tarfile.TarError) as e:
            raise ReleaseError("package") from e
        if installed(part).get("commit") != m["commit"]:
            raise ReleaseError("package")
        os.replace(part, target)
    return target


def switch(link, target):
    """The link to another version, in one step (a new link put in its place: never none there)."""
    tmp = link.with_name(f".{link.name}.new")
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(target)
    os.replace(tmp, link)


def tidy(link, keep=KEEP):
    """Only the newest versions kept (by when they came), the one in use always."""
    vers = versions_dir(link)
    current = head(link)
    dirs = sorted((p for p in vers.iterdir() if p.is_dir() and COMMIT_ID.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for p in dirs[keep:]:
        if p.name != current:
            shutil.rmtree(p, ignore_errors=True)


def install(link=None, m=None):
    """The newest version unpacked and in use: (the commit before, the one now)."""
    link = link or link_of()
    m = m or manifest()
    before = head(link)
    if before and older(m, installed(versions_dir(link) / before)):
        raise ReleaseError("older")
    target = unpack(m, versions_dir(link))
    os.utime(target)  # the newest, for tidy()
    switch(link, target)
    tidy(link)
    return before, m["commit"]


def set_to(commit, link=None):
    """Back to a version still on the device; False if it is not."""
    link = link or link_of()
    target = versions_dir(link) / commit
    if not (COMMIT_ID.fullmatch(commit) and (target / RELEASE_FILE).is_file()):
        return False
    switch(link, target)
    return True


# ---------- Making a release (on the developer's computer) ----------

LOG_FORMAT = "--format=%ct%x09%s%x09%(trailers:key=Note-de,valueonly=true,unfold=true,separator=%x20)"


def git_of(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def changes(repo, ref="HEAD", n=CHANGES_LISTED):
    out = []
    for line in git_of(repo, "log", f"-n{n}", "--no-merges", LOG_FORMAT, ref).splitlines():
        when, _, rest = line.partition("\t")
        subject, _, de = rest.partition("\t")
        if when.isdigit():
            out.append({"at": int(when), "s": subject, "de": de.strip()})
    return out


def build(repo, out, key, base_url="", manifest_url=""):
    """The package of the repository's HEAD and its signed manifest in `out`: kino-<commit>.tar.gz, manifest.json and
    manifest.json.sig. The archive's address in the manifest is base_url + its name (relative without one). With
    manifest_url also get-matinee, the installer, with that address and the release key's signers in it."""
    out.mkdir(parents=True, exist_ok=True)
    commit = git_of(repo, "rev-parse", "HEAD").strip()
    at = int(git_of(repo, "log", "-1", "--format=%ct", "HEAD").strip())
    version = git_of(repo, "describe", "--tags", "--always").strip()
    release = {"commit": commit, "version": version, "at": at, "changes": changes(repo)}
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d) / "tree"
        tree.mkdir()
        tar_of_head = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "HEAD"], capture_output=True,
                                     check=True).stdout
        (Path(d) / "head.tar").write_bytes(tar_of_head)
        with tarfile.open(Path(d) / "head.tar") as tar:
            tar.extractall(tree, filter="data")
        (tree / ".from-kino").unlink(missing_ok=True)  # which Matinee commit it was made of (deploy/make-pro): not the devices'
        (tree / RELEASE_FILE).write_text(json.dumps(release, indent=1) + "\n")
        name = f"matinee-{commit[:12]}.tar.gz"
        with tarfile.open(out / name, "w:gz") as tar:
            for p in sorted(tree.iterdir()):
                tar.add(p, arcname=p.name)
    sha = hashlib.sha256((out / name).read_bytes()).hexdigest()
    m = {**release, "archive": base_url + name, "sha256": sha, "size": (out / name).stat().st_size,
         "commits": git_of(repo, "rev-list", f"-n{COMMITS_LISTED}", "HEAD").split()}
    (out / "manifest.json").write_text(json.dumps(m, indent=1) + "\n")
    (out / "manifest.json.sig").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key), "-n", NAMESPACE, str(out / "manifest.json")],
                   check=True, capture_output=True)
    if manifest_url:
        signers = git_of(repo, "show", "HEAD:deploy/release-signers").strip()
        get_url = manifest_url.rsplit("/", 1)[0] + "/get-matinee"
        text = git_of(repo, "show", "HEAD:deploy/get-matinee")
        for k, v in (("@MANIFEST_URL@", manifest_url), ("@SIGNERS@", signers), ("@GET_URL@", get_url)):
            text = text.replace(k, v)
        (out / "get-matinee").write_text(text)
        (out / "get-matinee").chmod(0o755)
    return out / name


def main(args):
    cmd = args[0] if args else ""
    try:
        if cmd == "check":  # what an update would bring, as JSON
            print(json.dumps(check()))
        elif cmd == "newest":  # the newest commit
            print(manifest()["commit"])
        elif cmd == "head":
            print(head() or "")
        elif cmd == "install":
            before, after = install()
            print(f"{before or '-'} {after}")
        elif cmd == "switch" and len(args) == 2:
            if not set_to(args[1]):
                print(f"Version {args[1][:12]} is not on this device", file=sys.stderr)
                return 1
            print(link_of())
        elif cmd == "build" and len(args) >= 4:  # build <repo> <out dir> <key> [base url [manifest url]]
            print(build(Path(args[1]), Path(args[2]), Path(args[3]), *args[4:6]))
        else:
            print(__doc__.strip().splitlines()[0], file=sys.stderr)
            return 2
    except ReleaseError as e:
        print(f"Update failed ({e}), nothing changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
