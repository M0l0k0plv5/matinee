# Getting started

Matinee turns a small computer on your TV into a player for your own media servers: films and shows from Jellyfin, and
optionally music (Navidrome), audiobooks (Audiobookshelf) and podcasts (Podfetch). It needs nothing but those servers:
no account with anyone else, nothing sent anywhere else.

## The first start

Connect the computer to the TV and switch it on. Matinee walks you through a few steps:

1. **Language:** Deutsch or English.
2. **Network:** connected by cable, nothing to do. Otherwise choose **Wi-Fi**, your network, and enter its password
   with the remote's letter grid (or a keyboard).
3. **Jellyfin:** Matinee lists the Jellyfin servers it finds at home: choose yours with OK, or enter its address (for
   instance `http://192.168.1.10:8096`). Then your Jellyfin user name and password, and **Sign In**.
4. **Remote:** a Bluetooth remote or an Xbox controller is paired here. A USB remote needs nothing.

Any step but the language can wait (**Later**); **Settings → System → First-Start Setup** runs them again. Music,
audiobooks and podcasts: open their tile on the main menu and sign in the same way, or leave them out.

## No Jellyfin server yet?

If Matinee was set up with its own server, the Jellyfin step shows **Jellyfin on This Device**: choose a user name and
a password and **Set Up on This Device**. Then copy films onto it from a computer: **Settings → Movies & Shows → Put
Films on This Device** shows the address, user name and password. More: [a media server](server.md).

## Using the remote

- **Arrows** move, **OK** opens and plays, **Back** goes back (during a film: stops it).
- **OK held** on anything: what can be done with it (add to My List, mark as watched, …).
- During a film: left and right seek, up and down change the volume, **Menu** shows what is playing.

Any keyboard-like remote works (USB, radio, air mice), as do Bluetooth remotes and Xbox controllers:
**Settings → Devices** pairs them.

## Your own channels

Matinee makes TV channels out of your library (films by genre, shows), with a guide. They run like television: switch
on and something is on. Find them on Movies & Shows' start.

## Netflix, Prime Video and the like

Those services play in full quality only on certified devices. Plug a streaming stick (Fire TV, Google TV, Apple TV)
into another HDMI input of the TV and add it in **Settings → Other Devices on the TV**: its tile on the main menu then
switches the TV to it, and any key on Matinee's remote switches back. The TV needs HDMI-CEC turned on in its settings,
and the computer a CEC adapter if it has none built in.

## Updates

Matinee updates itself at night, never during a film or a song. **Settings → System** shows the version, what is new, and
updates at once. A new version that does not start properly is replaced by the one before it, by itself.

## When something is wrong

- **"No network"**: check the cable, or choose the Wi-Fi again in Settings → Wi-Fi.
- **"Jellyfin is not reachable"**: the network is fine, the server is not answering. Is it switched on? Matinee goes on
  by itself once it answers.
- **"Signed out by the server"**: the password was changed or the device removed in Jellyfin. Sign in again.
- A film stops with "Connection to the server lost": Matinee waits and goes on where it stopped once the server answers.
- Still stuck: **Settings → System → Report to Support** sends a report about the device (no passwords) and shows a
  code to tell support.

More: [setup in detail](setup.md), [all the keys](controls.md), [everything Matinee does](features.md).
