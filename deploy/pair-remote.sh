#!/bin/sh
# Pairs a Bluetooth remote (e.g. an Amazon Fire TV remote) with the thin client, so Matinee can be used with it.
# Run as a normal user from the Matinee directory: ./deploy/pair-remote.sh
# Before: hold the remote's Home key for about 10 seconds (pairing mode).
# An Xbox controller instead: ./deploy/pair-remote.sh xbox (before: hold its pair button on top until the Xbox
# button flashes fast).
set -eu
if [ "${1:-}" = xbox ]; then
  WHAT=controller
  MATCH='xbox'  # "Xbox Wireless Controller"
  HINT="hold the pair button on top until the Xbox button flashes fast"
else
  WHAT=remote
  MATCH='remote|fire ?tv|amazon|aftv'  # Fire TV remotes call themselves "Amazon Fire TV Remote" or similar
  HINT="hold Home for 10 seconds now if you have not"
fi
command -v bluetoothctl >/dev/null || { echo "bluetoothctl missing: run ./deploy/install.sh first" >&2; exit 1; }
sudo systemctl start bluetooth
sudo bluetoothctl power on >/dev/null

# a remote (or controller) paired before (it may have lost the pairing) starts afresh
sudo bluetoothctl devices | grep -iE "$MATCH" | awk '{print $2}' | while read -r old; do
  sudo bluetoothctl remove "$old" >/dev/null 2>&1 || true
done

echo "==> Looking for the $WHAT for 30 seconds ($HINT)"
sudo bluetoothctl --timeout 30 scan on >/dev/null 2>&1 || true
# the newest device that looks like one
DEV=$(sudo bluetoothctl devices | grep -iE "$MATCH" | tail -1)
if [ -z "$DEV" ]; then
  echo "!! No $WHAT found. Devices seen:" >&2
  sudo bluetoothctl devices >&2
  echo "Pair one by hand: sudo bluetoothctl pair <address>; then trust and connect it." >&2
  exit 1
fi
MAC=$(echo "$DEV" | awk '{print $2}')
# a device chooses its own name: anyone nearby could call theirs "Fire TV Remote". Paired and trusted it would type
# into Matinee like a keyboard, so it is only paired when you say it is yours
printf '==> Found %s\n    Is that your %s? Pair and trust it? [y/N] ' "$DEV" "$WHAT"
read -r answer
case "$answer" in
  [yYjJ]*) ;;
  *) echo "Nothing paired." >&2; exit 1 ;;
esac
echo "==> Pairing $DEV"
# bluetoothctl registers its agent only for commands on stdin, not for one given: without it a device that asks to
# confirm the pairing (an Xbox controller) fails. Its question answered yes, as it is the device you just said is
# yours; pairable first, otherwise no key is kept, and the device is gone after a restart
{ sleep 2; echo "pairable on"; echo "pair $MAC"; sleep 6; echo yes; sleep 10; echo quit; } \
  | sudo bluetoothctl --agent NoInputNoOutput >/dev/null 2>&1 || true
sudo bluetoothctl info "$MAC" | grep -q 'Paired: yes' || {
  echo "!! Pairing failed. Put the $WHAT in pairing mode again and run this once more." >&2
  exit 1
}
sudo bluetoothctl trust "$MAC"   # it reconnects by itself after it slept or the thin client restarted
sudo bluetoothctl connect "$MAC" || true
echo "==> Done. The $WHAT works in Matinee now; it stays paired across restarts."
