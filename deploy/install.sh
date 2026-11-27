#!/bin/sh
# Sets up an Arch Linux, Debian 13 (trixie, or newer), Ubuntu 26.04 (or newer), Fedora, openSUSE Tumbleweed or Leap 16
# machine, e.g. a thin client, to boot straight into Matinee; also those made from them (LMDE 7, Raspberry Pi OS, Manjaro,
# EndeavourOS, CachyOS; Linux Mint, Pop!_OS, Zorin OS and elementary OS once on Ubuntu 26.04).
# Run as a normal user from the Matinee directory: ./deploy/install.sh
set -eu
KINO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"

# A question answered yes unless it is no: $1 the question
ask() {
  printf "%s [Y/n] " "$1"
  read -r answer || answer=""
  case "$answer" in [nN]*) return 1;; esac
}
# The same, answered no unless it is yes
ask_no() {
  printf "%s [y/N] " "$1"
  read -r answer || answer=""
  case "$answer" in [yYjJ]*) return 0;; esac
  return 1
}
DISTRO="$(. /etc/os-release 2>/dev/null; echo "${ID:-}")"

echo "==> Installing packages"
# NetworkManager lets Matinee set up Wi-Fi from the sofa (backend/network.py). git is for kino --update in a checkout, ssh-keygen (OpenSSH) checks the signature of a package's update,
# wlr-randr sets the display mode (display-mode), bluez the Bluetooth remotes (deploy/pair-remote.sh), vainfo
# (libva-utils) tells which videos the graphics chip decodes: the others Jellyfin converts
INTEL="" TIME=systemd-timesyncd
for d in /sys/bus/pci/devices/*; do  # an Intel graphics chip (PCI class 03, vendor 8086), before lspci is installed
  case "$(cat "$d/class" 2>/dev/null)" in 0x03*) [ "$(cat "$d/vendor")" = 0x8086 ] && INTEL=1;; esac
done
if command -v pacman >/dev/null 2>&1; then
  POOL=arch
  sudo pacman -S --needed cage chromium mpv python curl pciutils mesa inter-font noto-fonts noto-fonts-emoji adwaita-cursors \
    pipewire pipewire-pulse pipewire-jack wireplumber git openssh networkmanager wlr-randr bluez bluez-utils libva-utils \
    v4l-utils linuxconsole
  # Hardware decoding: Intel needs its own VA-API drivers, AMD gets them with mesa
  [ -z "$INTEL" ] || sudo pacman -S --needed intel-media-driver libva-intel-driver
elif command -v apt-get >/dev/null 2>&1; then
  POOL=debian
  sudo apt-get update
  candidate() { LC_ALL=C apt-cache policy "$1" | awk '/Candidate/ {print $2}'; }  # in English whatever the language
  # Ubuntu has Chromium as a snap only: that one then (Linux Mint and Debian have a package)
  SNAP_CHROMIUM="" CHROMIUM_PACKAGE=chromium
  case "$(candidate chromium)" in
    "" | "(none)")
      # Pop!_OS and elementary OS come without snapd, which Ubuntu's own sources have for them
      command -v snap >/dev/null 2>&1 || case "$(candidate snapd)" in "" | "(none)") false;; esac ||
        { echo "!! No Chromium here, neither as a package nor as a snap: Debian 13 or newer is needed" >&2; exit 1; }
      SNAP_CHROMIUM=1 CHROMIUM_PACKAGE="";;
  esac
  # mpv 0.38 or newer: Matinee's mpv.conf has what 0.37 does not know (the OSD's box, forced subtitles by language, more
  # than one GPU context), and mpv stops at once on one it does not know. Ubuntu 24.04 and those made from it (Linux
  # Mint 22, Zorin OS 18, Pop!_OS and elementary OS on 24.04) have 0.37.
  mpv_version="$(candidate mpv)"
  dpkg --compare-versions "${mpv_version#*:}" ge 0.38 ||
    { echo "!! mpv ${mpv_version:-none} here, Matinee needs 0.38 or newer: Debian 13 (or LMDE 7) or Ubuntu 26.04 (or one made from it) is needed" >&2; exit 1; }
  py="$(candidate python3)"
  dpkg --compare-versions "$py" ge 3.12 ||
    { echo "!! Python $py here, Matinee needs 3.12 or newer: Debian 13 (trixie) or newer is needed" >&2; exit 1; }
  # systemd-timesyncd for the exact time below, unless another time service is there already
  TIME=systemd-timesyncd
  dpkg-query -W -f '${Status}\n' chrony ntpsec ntp 2>/dev/null | grep -q "install ok installed" && TIME=""
  # Hardware decoding: Intel needs its own VA-API drivers, AMD gets them with mesa-va-drivers
  sudo apt-get install cage $CHROMIUM_PACKAGE mpv python3 curl pciutils mesa-va-drivers libgl1-mesa-dri fonts-inter \
    fonts-noto-core fonts-noto-color-emoji pipewire pipewire-pulse wireplumber git openssh-client network-manager wlr-randr bluez vainfo adwaita-icon-theme $TIME \
    v4l-utils inputattach \
    ${INTEL:+intel-media-va-driver i965-va-driver}
  if [ -n "$SNAP_CHROMIUM" ]; then
    command -v snap >/dev/null 2>&1 || sudo apt-get install snapd
    # a container (KINO_PACKAGES_ONLY in CI) has no snapd running: that snapd is there is all it can tell
    [ -n "${KINO_PACKAGES_ONLY:-}" ] || sudo snap install chromium
  fi
elif [ "$DISTRO" = fedora ]; then
  POOL=fedora TIME=""  # chrony keeps the time, as Fedora sets it up
  # Fedora leaves out H.264 and HEVC for their patents: its ffmpeg (mpv's) plays no HEVC at all, its graphics drivers
  # decode neither. RPM Fusion has them, as most Fedora machines that play videos do.
  FUSION=""
  if ask "==> RPM Fusion for H.264 and HEVC videos (what Fedora leaves out)?"; then
    fv="$(rpm -E %fedora)"
    sudo dnf install "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$fv.noarch.rpm" \
      "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$fv.noarch.rpm"
    FUSION=1
  else
    echo "   (without it, Jellyfin converts most videos, and some do not play)"
  fi
  sudo dnf install cage chromium mpv python3 curl pciutils git openssh-clients NetworkManager NetworkManager-wifi wlr-randr bluez libva-utils pipewire \
    v4l-utils linuxconsoletools \
    pipewire-pulseaudio wireplumber rsms-inter-fonts google-noto-sans-fonts google-noto-color-emoji-fonts mesa-dri-drivers \
    adwaita-cursor-theme
  if [ -n "$FUSION" ]; then
    # the whole ffmpeg in place of Fedora's, and the graphics drivers' decoders: Intel's own, AMD's in mesa's
    sudo dnf install --allowerasing ffmpeg mesa-va-drivers-freeworld ${INTEL:+intel-media-driver}
  elif [ -n "$INTEL" ]; then
    sudo dnf install libva-intel-media-driver
  fi
elif [ "$DISTRO" = opensuse-tumbleweed ] || [ "$DISTRO" = opensuse-slowroll ] || [ "$DISTRO" = opensuse-leap ]; then
  POOL=opensuse TIME=""  # chrony keeps the time, as openSUSE sets it up
  # openSUSE leaves out H.264 and HEVC as Fedora does; Packman has them
  PACKMAN=""
  if ask "==> Packman for H.264 and HEVC videos (what openSUSE leaves out)?"; then
    # once: a second run of this would have zypper say "error" for the repository already there
    # Packman's own for each: Tumbleweed, Slowroll, Leap by its version (16.0)
    case "$DISTRO" in
      opensuse-leap) PACKMAN_DIR="openSUSE_Leap_$(. /etc/os-release; echo "$VERSION_ID")";;
      opensuse-slowroll) PACKMAN_DIR=openSUSE_Slowroll;;
      *) PACKMAN_DIR=openSUSE_Tumbleweed;;
    esac
    zypper -q lr packman >/dev/null 2>&1 || sudo zypper addrepo --refresh --priority 90 --name Packman \
      "https://ftp.gwdg.de/pub/linux/misc/packman/suse/$PACKMAN_DIR/" packman
    sudo zypper --gpg-auto-import-keys refresh packman
    PACKMAN=1
  else
    echo "   (without it, Jellyfin converts most videos, and some do not play)"
  fi
  # qt6-wayland: openSUSE's Chromium is built with Qt only, which stops it at once on Wayland without this
  sudo zypper install cage chromium mpv /usr/bin/python3 curl pciutils git openssh-clients NetworkManager wlr-randr bluez libva-utils \
    v4l-utils \
    pipewire pipewire-pulseaudio wireplumber inter-fonts google-noto-sans-fonts google-noto-coloremoji-fonts Mesa-dri \
    Mesa-libva adwaita-icon-theme qt6-wayland ${INTEL:+intel-media-driver}
  # Packman's ffmpeg and the rest in place of openSUSE's
  [ -z "$PACKMAN" ] || sudo zypper dist-upgrade --from packman --allow-vendor-change
else
  echo "!! Matinee installs on Arch Linux, Debian 13 (or newer), Ubuntu, Fedora and openSUSE: not on ${DISTRO:-this system}" >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
  { echo "!! $(python3 --version 2>&1) here, Matinee needs Python 3.12 or newer" >&2; exit 1; }
# the packages only, nothing set up: CI (.github/workflows/distros.yml) checks every distribution still has them all
[ -z "${KINO_PACKAGES_ONLY:-}" ] || { echo "==> Packages installed (KINO_PACKAGES_ONLY: nothing set up)"; exit 0; }

echo "==> Bluetooth for remotes"
# Fire TV remotes pair without storing a key ("not bonded"); BlueZ only lets such a keyboard in with
# ClassicBondedOnly=false. The price: someone within Bluetooth range pretending to be the remote could type.
if [ -f /etc/bluetooth/input.conf ] && ! grep -q '^ClassicBondedOnly=false' /etc/bluetooth/input.conf; then
  sudo cp /etc/bluetooth/input.conf /etc/bluetooth/input.conf.kino-backup
  sudo sed -i -E 's/^#?ClassicBondedOnly=.*/ClassicBondedOnly=false/' /etc/bluetooth/input.conf
  grep -q '^ClassicBondedOnly=false' /etc/bluetooth/input.conf || echo 'ClassicBondedOnly=false' | sudo tee -a /etc/bluetooth/input.conf >/dev/null
fi
# Xbox controllers (Bluetooth LE): Privacy=device lets one find the TV computer again by the address it paired with,
# JustWorksRepairing=always lets one that lost its key pair again by itself instead of being turned away
# ("authentication failure"), as they do after pairing with another device. The price: a device pretending to be one
# paired before could pair in its place without being asked.
if [ -f /etc/bluetooth/main.conf ]; then
  changed=""
  for setting in "Privacy = device" "JustWorksRepairing = always"; do
    key=${setting%% *}
    grep -qE "^$key *= *${setting##* }\$" /etc/bluetooth/main.conf && continue
    [ -n "$changed" ] || sudo cp /etc/bluetooth/main.conf /etc/bluetooth/main.conf.kino-backup
    changed=1
    if grep -qE "^#? *$key *=" /etc/bluetooth/main.conf; then
      sudo sed -i -E "0,/^#? *$key *=.*/s//$setting/" /etc/bluetooth/main.conf
    else
      sudo sed -i -E "0,/^\[General\]/s//[General]\n$setting/" /etc/bluetooth/main.conf
    fi
  done
  [ -z "$changed" ] || sudo systemctl try-restart bluetooth.service
fi
sudo systemctl enable --now bluetooth.service 2>/dev/null || echo "   (no Bluetooth adapter yet: plug one in, then run this again)"
# speakers and headphones in their stereo sound only: Matinee puts deploy/wireplumber-bluetooth.conf in place itself
# UPower asks a Bluetooth remote for its battery every 30 seconds (a HID GET_REPORT): the remote is woken from its
# power saving each time, and the Bluetooth chip, busy with that, drops a moment of the sound going to a Bluetooth
# speaker: a crackle every 30 seconds. A TV computer has no battery of its own for UPower to watch, so it is switched
# off there; masked, as Chromium would start it again over D-Bus. On a computer with a battery (a laptop) it stays.
own_battery=""
for supply in /sys/class/power_supply/*; do
  [ "$(cat "$supply/type" 2>/dev/null)" = Battery ] && [ "$(cat "$supply/scope" 2>/dev/null)" != Device ] && own_battery=1
done
if [ -z "$own_battery" ] && systemctl cat upower.service >/dev/null 2>&1; then
  sudo systemctl mask --now upower.service >/dev/null 2>&1 && echo "   UPower switched off (it woke the remote every 30 s: crackles on a Bluetooth speaker)"
fi

echo "==> Wi-Fi from the TV (NetworkManager)"
# Matinee joins a wireless network from its settings and its first sign-in through NetworkManager. Another service that
# runs the network now is replaced only when asked: the connection drops for a moment, and one over Wi-Fi is gone
# until the network is chosen on the TV (over SSH: connect again then).
if ! systemctl is-active --quiet NetworkManager; then
  others=""
  for unit in systemd-networkd iwd dhcpcd netctl wpa_supplicant@wlan0; do
    systemctl is-active --quiet "$unit" 2>/dev/null && others="$others $unit"
  done
  if [ -z "$others" ]; then
    sudo systemctl enable --now NetworkManager
  elif ask "    The network runs on$others now. Switch it to NetworkManager, so Wi-Fi can be set up on the TV?"; then
    # shellcheck disable=SC2086  # the list of units, split into words on purpose
    sudo systemctl disable --now $others
    sudo systemctl enable --now NetworkManager
  else
    echo "   (left as it is: Matinee offers no Wi-Fi settings without NetworkManager)"
  fi
fi
# what Matinee's user may change of the network: anything NetworkManager offers, also while its session is not the
# active one (polkit lets only the active one by itself)
sudo mkdir -p /etc/polkit-1/rules.d
printf '%s\n' "polkit.addRule(function (action, subject) {" \
  "  if (action.id.indexOf(\"org.freedesktop.NetworkManager.\") === 0 && subject.user === \"$USER_NAME\") return polkit.Result.YES;" \
  "});" | sudo tee /etc/polkit-1/rules.d/50-kino-network.rules >/dev/null

echo "==> Switching the TV's input over HDMI-CEC"
# cec-ctl (v4l-utils) speaks to /dev/cec*, which the video group may use. A Pulse-Eight USB-CEC adapter (also the one
# built into some mini PCs) becomes one only once inputattach hands its serial line to the kernel: done by a service
# started whenever it is plugged in. DisplayPort to HDMI adapters that pass CEC on need nothing.
if INPUTATTACH="$(command -v inputattach)"; then
  printf '%s\n' '[Unit]' 'Description=Pulse-Eight USB-CEC adapter on %I' 'BindsTo=dev-%i.device' 'After=dev-%i.device' \
    '[Service]' "ExecStart=$INPUTATTACH --pulse8-cec /dev/%I" 'Restart=on-failure' \
    | sudo tee /etc/systemd/system/kino-pulse8@.service >/dev/null
  printf '%s\n' 'SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="2548", ATTRS{idProduct}=="100[12]", TAG+="systemd", ENV{SYSTEMD_WANTS}+="kino-pulse8@%k.service"' \
    | sudo tee /etc/udev/rules.d/70-kino-pulse8.rules >/dev/null
  sudo systemctl daemon-reload
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty || true
else
  echo "   (no inputattach here: a Pulse-Eight USB adapter is not used; DisplayPort adapters with CEC work)"
fi

echo "==> Exact time from atomic clocks"
# the clock on the main menu and the screensaver, the guide's "now", the switching off at night: systemd-timesyncd
# asks the time servers of Germany's PTB, set by its atomic clocks (within milliseconds over the internet); the
# usual pool when they cannot be reached
if [ -n "$TIME" ]; then
  sudo mkdir -p /etc/systemd/timesyncd.conf.d
  printf '[Time]\nNTP=ptbtime1.ptb.de ptbtime2.ptb.de ptbtime3.ptb.de\nFallbackNTP=0.%s.pool.ntp.org 1.%s.pool.ntp.org 2.%s.pool.ntp.org\n' \
    "$POOL" "$POOL" "$POOL" | sudo tee /etc/systemd/timesyncd.conf.d/kino.conf >/dev/null
  sudo timedatectl set-ntp true
  sudo systemctl restart systemd-timesyncd.service
else
  echo "   (another time service is set up here: left as it is)"
fi

# systemd-journal: Matinee's log (journalctl -t kino) readable without sudo, also over SSH
# Fedora and openSUSE: firewalld lets nobody in by itself; the phone remote, when turned on in the settings, is
# served on 8767 (backend/phone_remote.py). Nothing listens there while it is off
if command -v firewall-cmd >/dev/null 2>&1 && sudo firewall-cmd --state >/dev/null 2>&1; then
  echo "==> Opening the firewall for the phone remote (8767/tcp)"
  sudo firewall-cmd --permanent --add-port=8767/tcp >/dev/null
  sudo firewall-cmd --reload >/dev/null
fi

echo "==> Giving $USER_NAME access to graphics, input devices, audio and the system log"
sudo usermod -aG video,input,audio,systemd-journal "$USER_NAME"

if [ ! -f "$HOME/.config/kino/config.json" ]; then
  # no config.json: Matinee asks for the Jellyfin server and the sign-in on the TV and writes it itself
  echo "!! On the first start, Matinee walks through language, network and the Jellyfin server on the TV"
fi

# Matinee draws on the graphics chip itself (cage): without its device nothing shows on the TV. openSUSE's smallest kernel
# (kernel-default-base, in its minimal images) comes without the drivers for one.
if ! ls /dev/dri/card* >/dev/null 2>&1; then
  if command -v rpm >/dev/null 2>&1 && rpm -q kernel-default-base >/dev/null 2>&1; then
    echo "!! No graphics device here: openSUSE's kernel-default-base has no drivers for it. Install kernel-default"
    echo "   (sudo zypper install kernel-default) and restart the computer."
  else
    echo "!! No graphics device here (/dev/dri): Matinee shows nothing on the TV without one"
  fi
fi

echo "==> Setting up the systemd service"
# the units kept as .service.in: on a Mac (a checkout to work on) macOS takes a .service for an app and lists it among them
sed -e "s|@USER@|$USER_NAME|" -e "s|@KINO@|$KINO|" "$KINO/deploy/kino.service.in" | sudo tee /etc/systemd/system/kino.service >/dev/null
# Chromium as a snap (Ubuntu): snap-confine, which starts it, needs privileges NoNewPrivileges would keep from it,
# writes its mount namespaces under /run/snapd (read-only with ProtectSystem=strict, which leaves only /run/user) and
# puts the snap in a cgroup of its own (ProtectControlGroups). The rest of the hardening stays, /usr, /boot and /etc
# read-only among it (ProtectSystem=full), and the snap confines Chromium itself (AppArmor, seccomp).
if [ -n "${SNAP_CHROMIUM:-}" ]; then
  sudo mkdir -p /etc/systemd/system/kino.service.d
  printf '%s\n' '# deploy/install.sh: Chromium is a snap here, which snap-confine cannot start without this' '[Service]' \
    'NoNewPrivileges=no' 'ProtectSystem=full' 'ProtectControlGroups=no' | sudo tee /etc/systemd/system/kino.service.d/snap.conf >/dev/null
else
  sudo rm -f /etc/systemd/system/kino.service.d/snap.conf
fi
sudo systemctl daemon-reload
sudo systemctl disable getty@tty1.service 2>/dev/null || true
sudo systemctl disable kmsconvt@tty1.service 2>/dev/null || true  # Fedora's console on tty1
sudo systemctl enable kino.service
sudo systemctl set-default graphical.target

# a machine looked after from afar (e.g. one given to someone else): it updates itself at night
printf "==> Update Matinee by itself every night? [y/N] "
read -r nightly || nightly=""
case "$nightly" in
  [yYjJ]*)
    # a copy owned by root: the one in the checkout is the user's, and it runs as root
    sudo install -D -o root -g root -m 755 "$KINO/deploy/nightly-update" /usr/local/libexec/kino-nightly-update
    sed -e "s|@KINO@|$KINO|" "$KINO/deploy/kino-update.service.in" | sudo tee /etc/systemd/system/kino-update.service >/dev/null
    sudo cp "$KINO/deploy/kino-update.timer" /etc/systemd/system/kino-update.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now kino-update.timer
    echo "    Nightly updates on (journalctl -u kino-update)";;
esac

# no media server yet: Jellyfin on this computer too, the films shared on the home network (deploy/server.sh)
if ask_no "==> No Jellyfin server yet? Set one up on this computer (Matinee and its server in one box)?"; then
  "$KINO/deploy/server.sh"
fi

echo "==> Done. Matinee starts automatically after a reboot."
echo "    Logs:      journalctl -t kino -f"
echo "    Restart:   sudo systemctl restart kino"
echo "    Console:   Ctrl+Alt+F2"
