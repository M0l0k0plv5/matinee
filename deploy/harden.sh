#!/bin/sh
# Optional, for the thin client: so that turning it off any time, an update gone wrong or a hang does not leave a
# TV that no longer works. Run as a normal user from the Matinee directory: ./deploy/harden.sh
# Arch Linux, Debian, Fedora and openSUSE (Tumbleweed, Slowroll):
# - a snapshot before and after every update: a bad update is undone by starting the snapshot from before it in the
#   GRUB menu (hold Esc while it starts). Arch: snap-pac, with snapper's "root" config; openSUSE does this by itself
#   (snapper with zypper's plugin, on btrfs), which is only checked here. Debian and Fedora get none: their GRUB cannot
#   start a snapshot
# - the hardware watchdog: if the whole computer hangs, it restarts by itself after 30 seconds
# - after a kernel panic: a restart after 10 seconds instead of a frozen screen
# - the power button shuts down cleanly (also when held), the SSD is trimmed weekly, no old packages are kept
#   (Arch: pacman's cache cleared weekly; Debian: apt keeps none it installed; Fedora and openSUSE keep none anyway)
#   and the system log stays below 200 MB, so the disk never fills up
# Running it again changes nothing more.
set -eu
DISTRO="$(. /etc/os-release 2>/dev/null; echo "${ID:-}")"
if command -v pacman >/dev/null 2>&1; then OS=arch
elif command -v apt-get >/dev/null 2>&1; then OS=debian
elif [ "$DISTRO" = fedora ]; then OS=fedora
elif [ "${DISTRO#opensuse-}" != "$DISTRO" ]; then OS=opensuse  # Tumbleweed, Slowroll, Leap
else
  echo "Arch Linux, Debian, Fedora or openSUSE only" >&2
  exit 1
fi
has_root_config() { sudo snapper list-configs 2>/dev/null | grep -Eq '^root[[:space:]]'; }

echo "==> Snapshots before and after every update"
case $OS in
  arch)
    sudo pacman -S --needed snap-pac pacman-contrib
    has_root_config ||
      echo "!! snapper has no \"root\" config: snap-pac makes no snapshots until there is one (snapper -c root create-config /)" >&2;;
  opensuse)
    if has_root_config && rpm -q snapper-zypp-plugin >/dev/null 2>&1; then
      echo "openSUSE's own: snapper takes one with every zypper run"
    else
      echo "!! No snapshots: openSUSE takes them only on btrfs with snapper's \"root\" config and snapper-zypp-plugin" >&2
    fi;;
  *) echo "-- None on $DISTRO: the rest still helps";;
esac

echo "==> Watchdog: restart when the computer hangs"
sudo install -d /etc/systemd/system.conf.d
printf '[Manager]\n# the hardware watchdog restarts a computer that hangs (deploy/harden.sh)\nRuntimeWatchdogSec=30s\nRebootWatchdogSec=2min\n' |
  sudo tee /etc/systemd/system.conf.d/kino-watchdog.conf >/dev/null

echo "==> Kernel panic: restart after 10 seconds"
printf '# restart instead of a frozen screen (deploy/harden.sh)\nkernel.panic = 10\n' | sudo tee /etc/sysctl.d/90-kino.conf >/dev/null
sudo sysctl -q --system

echo "==> Power button: shut down cleanly"
sudo install -d /etc/systemd/logind.conf.d
printf '[Login]\n# a press shuts the thin client down cleanly, held too (deploy/harden.sh)\nHandlePowerKey=poweroff\nHandlePowerKeyLongPress=poweroff\n' |
  sudo tee /etc/systemd/logind.conf.d/kino-power.conf >/dev/null

echo "==> System log: at most 200 MB"
sudo install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/kino.conf >/dev/null
sudo systemctl restart systemd-journald

echo "==> Weekly: trim the SSD; no old packages kept"
sudo systemctl enable --now fstrim.timer
case $OS in
  arch) sudo systemctl enable --now paccache.timer;;
  debian)
    # apt (but not apt-get) already drops what it installed; this makes apt-get, and the updates, do the same
    printf '// no downloaded packages kept once installed (deploy/harden.sh)\nAPT::Keep-Downloaded-Packages "false";\n' |
      sudo tee /etc/apt/apt.conf.d/90kino >/dev/null
    sudo apt-get clean;;
esac

sudo systemctl daemon-reexec  # the watchdog takes effect now; logind reads its settings at the next start
echo "==> Done. Check the watchdog with: systemctl show -p RuntimeWatchdogUSec"
