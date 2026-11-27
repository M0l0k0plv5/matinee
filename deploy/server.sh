#!/bin/sh
# Jellyfin on the TV computer itself, for whoever has no media server yet: Matinee and its server in one box.
# Run as Matinee's user from the Matinee directory (install.sh offers it): ./deploy/server.sh
#   - Jellyfin in a container (Podman, started by systemd through a Quadlet file, updated by podman-auto-update),
#     with the graphics chip for converting videos, on port 8096 of this computer and the home network
#   - the media in /srv/matinee-media (KINO_MEDIA), Jellyfin's libraries made from the TV (backend/local_server.py)
#   - that folder shared on the home network by Samba ("Matinee"), for copying films onto it from a computer, under
#     this user's name and a password of its own (shown on the TV: Settings → Movies & Shows → Put Films on This Device)
#   - Jellyfin's startup wizard done at once, with a first user of its own: until done, whoever on the home network
#     does it first would be the server's administrator. Its name and password (made up here) are kept in
#     ~/.config/kino/local-admin.json until the TV makes that user the person's own.
# The rest of Jellyfin's setup is done on the TV, in the first start's Jellyfin step: "Set Up on This Device".
set -eu
# the system's own folders (/etc, /var, /sys, /dev) under this, for the tests (tests/test_server_sh.py); else the system's
SYS="${KINO_SYSROOT:-}"
USER_NAME="$(id -un)"
MEDIA="${KINO_MEDIA:-/srv/matinee-media}"
STATE="$SYS/var/lib/kino-jellyfin"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/kino"
DISTRO="$(. "$SYS/etc/os-release" 2>/dev/null; echo "${ID:-}")"
IMAGE="docker.io/jellyfin/jellyfin:latest"
JELLYFIN="${KINO_LOCAL_JELLYFIN:-http://127.0.0.1:8096}"

echo "==> Installing Podman and Samba"
if command -v pacman >/dev/null 2>&1; then
  sudo pacman -S --needed podman crun samba  # crun: else pacman asks which runtime
  SMB=smb
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get install podman samba
  SMB=smbd
elif [ "$DISTRO" = fedora ]; then
  sudo dnf install podman samba
  SMB=smb
elif command -v zypper >/dev/null 2>&1; then
  sudo zypper install podman samba
  SMB=smb
else
  echo "!! Not a distribution this knows (Arch, Debian, Fedora, openSUSE)" >&2
  exit 1
fi

echo "==> The media folder: $MEDIA"
sudo mkdir -p "$MEDIA" "$STATE/config" "$STATE/cache"
sudo chown "$USER_NAME": "$MEDIA"
sudo chmod 755 "$MEDIA"
# Matinee's service sees the system read only (ProtectSystem=strict): the media folder too, unless let in here. Matinee
# makes the libraries' folders in it when Jellyfin is set up from the TV.
sudo mkdir -p "$SYS/etc/systemd/system/kino.service.d"
printf '%s\n' '[Service]' "ReadWritePaths=-$MEDIA" | sudo tee "$SYS/etc/systemd/system/kino.service.d/media.conf" >/dev/null

echo "==> Jellyfin (container $IMAGE)"
sudo podman pull "$IMAGE"
# the graphics chip, where there is one: Jellyfin converts what the TV computer cannot play with it
DRI=""
[ -d "$SYS/dev/dri" ] && DRI="AddDevice=/dev/dri"
sudo mkdir -p "$SYS/etc/containers/systemd"
printf '%s\n' '[Unit]' 'Description=Jellyfin for Matinee' 'After=network-online.target' 'Wants=network-online.target' '' \
  '[Container]' "Image=$IMAGE" 'ContainerName=kino-jellyfin' 'AutoUpdate=registry' \
  '# the host network: port 8096, and the discovery Matinee finds it by (UDP 7359)' 'Network=host' \
  "Volume=$STATE/config:/config" "Volume=$STATE/cache:/cache" "Volume=$MEDIA:/media:ro" 'SecurityLabelDisable=true' \
  ${DRI:+"$DRI"} '' \
  '[Service]' 'Restart=always' 'TimeoutStartSec=900' '' \
  '[Install]' 'WantedBy=multi-user.target' \
  | sudo tee "$SYS/etc/containers/systemd/kino-jellyfin.container" >/dev/null
sudo systemctl daemon-reload
sudo systemctl start kino-jellyfin.service
sudo systemctl enable --now podman-auto-update.timer  # a new Jellyfin image once a day, restarted with it

echo "==> Jellyfin's startup wizard, before anyone else on the network"
mkdir -p "$CONF"
( umask 077; python3 - "$JELLYFIN" "$CONF/local-admin.json" "${KINO_JELLYFIN_WAIT:-600}" <<'PY'
import json, secrets, sys, time, urllib.request
url, keep, wait = sys.argv[1], sys.argv[2], float(sys.argv[3])
AUTH = 'MediaBrowser Client="Matinee setup", Device="Matinee setup", DeviceId="kino-server-sh", Version="1"'

def call(path, body=None):
    req = urllib.request.Request(url + path, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": AUTH, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
    return json.loads(raw) if raw else None

until, info = time.monotonic() + wait, None
# Jellyfin 12 answers from a server of its own while starting, with "startupWizardCompleted" (as camelCase, and not
# to be gone by): the real one's "StartupWizardCompleted" waited for
while not isinstance((info or {}).get("StartupWizardCompleted"), bool):
    try:
        info = call("/System/Info/Public")
    except (OSError, ValueError):
        info = None
    if not isinstance((info or {}).get("StartupWizardCompleted"), bool):
        if time.monotonic() > until:
            print(f"!! Jellyfin did not answer at {url}: its startup wizard is still open to the home network. "
                  "Run this again, or set it up on the TV soon.", file=sys.stderr)
            sys.exit(0)
        time.sleep(2)
if info.get("StartupWizardCompleted") is not False:
    print("   done before")
    sys.exit(0)
admin = {"user": "kino-setup", "password": secrets.token_urlsafe(24)}
with open(keep, "w") as f:  # first: kept even if what follows breaks off
    json.dump(admin, f)
call("/Startup/Configuration", call("/Startup/Configuration") or {})
call("/Startup/User")  # Jellyfin makes its first user on this
call("/Startup/User", {"Name": admin["user"], "Password": admin["password"]})
call("/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
call("/Startup/Complete", {})
print("   done; the TV takes its first user over")
PY
)

echo "==> Sharing $MEDIA on the home network (Samba)"
# a configuration of its own: what a distribution ships shares more (Fedora's: every user's home, with the tokens in
# ~/.config/kino); the one before is kept beside it.
if ! sudo grep -q '^# Matinee (deploy/server.sh)' "$SYS/etc/samba/smb.conf" 2>/dev/null; then
  sudo mkdir -p "$SYS/etc/samba"
  [ -f "$SYS/etc/samba/smb.conf" ] && sudo cp "$SYS/etc/samba/smb.conf" "$SYS/etc/samba/smb.conf.kino-backup"
  printf '%s\n' '# Matinee (deploy/server.sh): only the media folder of Jellyfin on this computer' '[global]' \
    '  workgroup = WORKGROUP' '  server min protocol = SMB2' '  map to guest = never' '  load printers = no' \
    '  printing = bsd' '  printcap name = /dev/null' '  disable spoolss = yes' '' \
    '[Matinee]' "  path = $MEDIA" "  valid users = $USER_NAME" "  force user = $USER_NAME" '  read only = no' \
    '  create mask = 0644' '  directory mask = 0755' \
    '  # a Mac keeps what Finder adds to a file inside it, not in ._ files beside it' \
    '  vfs objects = catia fruit streams_xattr' '  fruit:metadata = stream' '  fruit:resource = stream' \
    '  veto files = /._*/.DS_Store/' '  delete veto files = yes' \
    | sudo tee "$SYS/etc/samba/smb.conf" >/dev/null
fi
# a password of its own for the share (not the login's): four groups easy to type on a computer
PASSWORD="$(python3 -c 'import secrets; a = "abcdefghjkmnpqrstuvwxyz23456789"; print("-".join("".join(secrets.choice(a) for _ in range(4)) for _ in range(3)))')"
printf '%s\n%s\n' "$PASSWORD" "$PASSWORD" | sudo smbpasswd -s -a "$USER_NAME" >/dev/null
mkdir -p "$CONF"
( umask 077; printf '{"name": "Matinee", "user": "%s", "password": "%s"}\n' "$USER_NAME" "$PASSWORD" > "$CONF/share.json" )
# Fedora and openSUSE: SELinux lets Samba only at folders marked for it (asked of the kernel: openSUSE has no
# selinuxenabled by itself)
if [ -e "$SYS/sys/fs/selinux/enforce" ]; then
  sudo chcon -R -t samba_share_t "$MEDIA"
fi
# Samba looks up this computer's own name on every connection: without an answer from /etc/hosts or systemd (openSUSE
# has neither for it) it asks the DNS server, and one that does not answer (some routers) holds each connection up
# for longer than a Mac or Windows waits. The name stays on this computer.
HOST="$(uname -n)"
if ! timeout 3 getent hosts "$HOST" >/dev/null 2>&1; then
  echo "127.0.1.1 $HOST" | sudo tee -a "$SYS/etc/hosts" >/dev/null
fi
sudo systemctl enable "$SMB"
sudo systemctl restart "$SMB"  # with the configuration above

# Fedora and openSUSE: firewalld lets nobody in by itself
if command -v firewall-cmd >/dev/null 2>&1 && sudo firewall-cmd --state >/dev/null 2>&1; then
  echo "==> Opening the firewall for Samba and Jellyfin (8096/tcp, discovery 7359/udp)"
  # a service and ports not in one call: firewall-cmd takes either
  sudo firewall-cmd --permanent --add-service=samba >/dev/null
  sudo firewall-cmd --permanent --add-port=8096/tcp --add-port=7359/udp >/dev/null
  sudo firewall-cmd --reload >/dev/null
fi

sudo systemctl daemon-reload
if systemctl is-active --quiet kino; then sudo systemctl restart kino; fi  # to write into the media folder

echo "==> Done. On the TV: Movies & Shows (or the first start's Jellyfin step) → Set Up on This Device."
echo "   Films then go into the share Matinee on this computer, user $USER_NAME, password $PASSWORD (also on the TV)"
