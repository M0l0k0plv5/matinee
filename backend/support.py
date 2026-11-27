"""A report to support, sent from the settings when the person asks for it: deploy/diag's file (logs, the system,
the settings without any secret), sent over HTTPS to whoever sells and supports Matinee (deploy/support-url, or
KINO_SUPPORT_URL), filed there under a short code the person reads out to them. Nothing is sent otherwise."""
import os
import secrets
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

SUPPORT_ROOT = Path(__file__).resolve().parent.parent
SUPPORT_CODE_LETTERS = "ACDEFGHJKLMNPQRTUVWXY3479"  # none mistaken for another when read out (no O/0, I/1, B/8, S/5)
SUPPORT_MAX = 20 * 1024 * 1024
SUPPORT_TIMEOUT = float(os.environ.get("KINO_SUPPORT_TIMEOUT", "60"))


def support_url():
    """Where reports go: KINO_SUPPORT_URL, else the first line of deploy/support-url; "" when nobody receives them.
    Only HTTPS (the report says much about the home it comes from), or HTTP to this computer itself (tests)."""
    url = os.environ.get("KINO_SUPPORT_URL")
    if url is None:
        try:
            url = (SUPPORT_ROOT / "deploy" / "support-url").read_text().splitlines()[0]
        except (OSError, IndexError):
            url = ""
    url = url.strip()
    u = urllib.parse.urlsplit(url)
    ok = u.scheme == "https" and u.hostname or u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")
    return url.rstrip("/") if ok else ""


def support_code():
    code = "".join(secrets.choice(SUPPORT_CODE_LETTERS) for _ in range(6))
    return f"{code[:3]}-{code[3:]}"


def support_send(version=""):
    """The report made and sent: {"code"}, or {"error": "support_off" | "support_diag" | "support_failed"}."""
    url = support_url()
    if not url:
        return {"error": "support_off"}
    with tempfile.TemporaryDirectory(prefix="kino-support-") as tmp:
        report, said = None, ""
        try:
            r = subprocess.run(["sh", str(SUPPORT_ROOT / "deploy" / "diag")], capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL, env=dict(os.environ, KINO_DIAG_DIR=tmp))
            report = Path(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else None
            said = r.stderr
        except (OSError, subprocess.TimeoutExpired) as e:
            said = str(e)
        if not report or not report.is_file() or report.stat().st_size > SUPPORT_MAX:
            print(f"Support report not made: {said.strip()[-300:]}", flush=True)
            return {"error": "support_diag"}
        code = support_code()
        req = urllib.request.Request(f"{url}/{code.replace('-', '')}", data=report.read_bytes(), method="POST",
                                     headers={"Content-Type": "application/gzip", "User-Agent": "Matinee",
                                              "X-Kino-Version": str(version)[:40]})
        try:
            with urllib.request.urlopen(req, timeout=SUPPORT_TIMEOUT) as resp:
                resp.read(1024)
        except OSError as e:  # URLError, HTTPError, a timeout
            print(f"Support report not sent: {e}", flush=True)
            return {"error": "support_failed"}
    print(f"Support report sent as {code}", flush=True)
    return {"code": code}
