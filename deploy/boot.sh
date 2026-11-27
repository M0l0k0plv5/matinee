#!/bin/sh
# Optional, for the thin client: Matinee's logo instead of boot messages, and GRUB without its 5 second wait.
# Run as a normal user from the Matinee directory: ./deploy/boot.sh
# Arch Linux, Debian, Fedora and openSUSE (Tumbleweed, Slowroll), each booting with GRUB:
# - Plymouth with a theme of its own: the logo on black (deploy/plymouth/watermark.png: web/icon.svg drawn at 360 px,
#   where the UI shows it again when it starts), its cards opening and closing a little while it starts (the
#   pictures deploy/plymouth/breath-NN.png, from tests/make-splash, in place of the usual spinner), and the dialog
#   for the disk password should unlocking it with the TPM ever fail
# - Plymouth in the initramfs (Arch: the plymouth hook; Debian's initramfs-tools and the dracut of Fedora and openSUSE
#   take it by themselves once it is installed), "quiet splash" on the kernel command line
# - GRUB starts at once with its menu hidden; hold Esc (or Shift) while it starts to get the menu
# Every file it changes is copied to <file>.kino-backup first. If the initramfs cannot be built, GRUB is left alone
# (on Arch the old mkinitcpio.conf comes back too).
set -eu
KINO="$(cd "$(dirname "$0")/.." && pwd)"
THEMES=/usr/share/plymouth/themes
DISTRO="$(. /etc/os-release 2>/dev/null; echo "${ID:-}")"
if command -v pacman >/dev/null 2>&1; then OS=arch
elif command -v apt-get >/dev/null 2>&1; then OS=debian
elif [ "$DISTRO" = fedora ]; then OS=fedora
elif [ "${DISTRO#opensuse-}" != "$DISTRO" ]; then OS=opensuse  # Tumbleweed, Slowroll, Leap
else
  echo "Arch Linux, Debian, Fedora or openSUSE only" >&2
  exit 1
fi
GRUB=/etc/default/grub
[ -f "$GRUB" ] || { echo "!! No $GRUB: only a computer starting with GRUB is set up here" >&2; exit 1; }

echo "==> Installing Plymouth"
case $OS in
  arch) sudo pacman -S --needed plymouth;;
  debian) sudo apt-get install plymouth plymouth-themes;;
  fedora) sudo dnf install plymouth plymouth-theme-spinner;;
  opensuse) sudo zypper install plymouth plymouth-theme-spinner plymouth-dracut;;
esac

echo "==> Theme: Matinee"
sudo rm -rf "$THEMES/kino"
sudo cp -r "$THEMES/spinner" "$THEMES/kino"
sudo mv "$THEMES/kino/spinner.plymouth" "$THEMES/kino/kino.plymouth"
sudo install -m 644 "$KINO/deploy/plymouth/watermark.png" "$THEMES/kino/watermark.png"
# the logo breathing in place of the spinner: Plymouth plays throbber-NNNN.png over the logo, 30 a second. The
# pictures from closed to open, each shown twice, then back through those between: one breath in about 3 s
sudo rm -f "$THEMES"/kino/throbber-*.png "$THEMES"/kino/animation-*.png
sudo install -m 644 "$KINO"/deploy/plymouth/breath-*.png "$THEMES/kino/"
n=0
for k in $(seq 0 23) $(seq 22 -1 1); do
  for twice in 1 2; do
    n=$((n + 1))
    sudo ln -s "$(printf 'breath-%02d.png' "$k")" "$THEMES/kino/$(printf 'throbber-%04d.png' "$n")"
  done
done
sudo sed -i -e 's|^Name=.*|Name=Matinee|' -e "s|^ImageDir=.*|ImageDir=$THEMES/kino|" \
  -e 's|^WatermarkVerticalAlignment=.*|WatermarkVerticalAlignment=.45|' \
  -e 's|^VerticalAlignment=.*|VerticalAlignment=.45|' \
  -e 's|^BackgroundStartColor=.*|BackgroundStartColor=0x000000|' \
  -e 's|^BackgroundEndColor=.*|BackgroundEndColor=0x000000|' "$THEMES/kino/kino.plymouth"
sudo plymouth-set-default-theme kino

echo "==> Initramfs with Plymouth"
case $OS in
  arch)
    CONF=/etc/mkinitcpio.conf
    if ! grep -Eq '^HOOKS=.*\bplymouth\b' "$CONF"; then
      sudo cp "$CONF" "$CONF.kino-backup"
      # right after systemd (or udev), before the disk is unlocked, so the password dialog can be drawn
      sudo sed -i -E '/^HOOKS=/ s/\b(systemd|udev)\b/\1 plymouth/' "$CONF"
    fi
    grep -E '^HOOKS=' "$CONF"
    if ! sudo mkinitcpio -P; then
      echo "!! Building the initramfs failed: putting the old $CONF back" >&2
      [ -f "$CONF.kino-backup" ] && sudo cp "$CONF.kino-backup" "$CONF" && sudo mkinitcpio -P
      exit 1
    fi;;
  debian) sudo update-initramfs -u -k all || { echo "!! Building the initramfs failed" >&2; exit 1; };;
  *) sudo dracut --force --regenerate-all || { echo "!! Building the initramfs failed" >&2; exit 1; };;
esac

echo "==> GRUB: no waiting, \"quiet splash\""
[ -e "$GRUB.kino-backup" ] || sudo cp "$GRUB" "$GRUB.kino-backup"  # run again: the first one stays
sudo sed -i -E -e 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=1/' -e 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=hidden/' "$GRUB"
grep -q '^GRUB_TIMEOUT=' "$GRUB" || echo 'GRUB_TIMEOUT=1' | sudo tee -a "$GRUB" >/dev/null
grep -q '^GRUB_TIMEOUT_STYLE=' "$GRUB" || echo 'GRUB_TIMEOUT_STYLE=hidden' | sudo tee -a "$GRUB" >/dev/null
grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' "$GRUB" || echo 'GRUB_CMDLINE_LINUX_DEFAULT=""' | sudo tee -a "$GRUB" >/dev/null
for word in quiet splash; do
  grep -Eq "^GRUB_CMDLINE_LINUX_DEFAULT=.*\\b$word\\b" "$GRUB" ||
    sudo sed -i -E "s/^GRUB_CMDLINE_LINUX_DEFAULT=\"([^\"]*)\"/GRUB_CMDLINE_LINUX_DEFAULT=\"\\1 $word\"/" "$GRUB"
done
sudo sed -i -E 's/^GRUB_CMDLINE_LINUX_DEFAULT=" +/GRUB_CMDLINE_LINUX_DEFAULT="/' "$GRUB"
grep -E '^GRUB_(TIMEOUT|TIMEOUT_STYLE|CMDLINE_LINUX_DEFAULT)=' "$GRUB"
case $OS in
  arch) sudo grub-mkconfig -o /boot/grub/grub.cfg;;
  debian) sudo update-grub;;
  fedora)
    # Fedora's kernels are boot entries of their own (BLS), with their command line: grubby changes those there
    # are, /etc/kernel/cmdline the ones of kernels to come
    sudo grubby --update-kernel=ALL --args="quiet splash"
    if [ -f /etc/kernel/cmdline ]; then
      [ -e /etc/kernel/cmdline.kino-backup ] || sudo cp /etc/kernel/cmdline /etc/kernel/cmdline.kino-backup
      for word in quiet splash; do
        grep -Eq "(^| )$word( |\$)" /etc/kernel/cmdline || sudo sed -i -E "1 s/\$/ $word/" /etc/kernel/cmdline
      done
    fi
    sudo grub2-mkconfig -o /boot/grub2/grub.cfg;;
  opensuse) sudo grub2-mkconfig -o /boot/grub2/grub.cfg;;
esac

echo "==> Done. The next boot shows Matinee's logo."
