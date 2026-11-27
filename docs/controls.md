# Controls

Everything works with the arrow keys, OK and Back, so with any remote that shows up as a keyboard.

The same list, shorter, is in Matinee itself: Settings → Using Matinee, or from any held OK. On the code page of the simple
settings, a remote's digit keys type the digits.

| Key | Browsing | During playback |
|---|---|---|
| Left / Right | Navigate | Move the seek mark 10 s back / forward (held: faster) |
| Up / Down | Navigate | Louder / quieter at once, held on and on; on a channel live: the next channel / the one before |
| OK / Enter | Open, play | Pause; skip the intro; next episode during the credits; go to the seek mark |
| Long-press OK | Options for what is chosen (My List, watched, heard, a star, follow a channel, start over, an episode's show); the settings of where you are; the main menu | – |
| Menu (Tab during a video) | Volume: up / right louder, down / left quieter | Info panel; on a channel live the volume, as when browsing |
| Volume keys | Louder / quieter, the bar shown | Louder / quieter |
| Home held / Play held | Main menu / sleep timer | – / sleep timer |
| Back / Esc / Backspace | Back (from a movie or show: to its library, on that title; then home; on the main menu from the shelf down into the dock) | Close a panel or menu, leave the seek mark, otherwise stop |
| Home | The start of the part you are in, from there the main menu | – |
| Search key / start typing | Open the search of the part you are in (Jellyfin, music, …) | – |
| Play/Pause | Play a movie or episode directly | Pause |
| Fast forward / Rewind | – | 30 s forward / back |
| Next track | – | Next episode right away |
| ⌘F / ⌘, / ⌘[ or ⌘← (Ctrl on a PC) | The search of the part you are in / the settings / back | – |
| ⌘1 … ⌘9 | The apps set up, in the main menu's order (Back: the main menu) | – |
| ⌘+ / ⌘− / ⌘0 | Size: a television's / a desk's / automatic (Settings → General → Size) | – |

The volume is the TV computer's own, so it reaches Bluetooth headphones and speakers too. A Fire TV remote's volume
keys send infrared to the TV set and never reach Matinee: they change the TV's loudspeakers only. With headphones or a
speaker on Bluetooth, use Menu (browsing and music) or Up / Down (during a video).

A Bluetooth speaker's or headphones' own buttons work as well: play/pause, next and previous (in music the next or
previous song, in a video the next episode or rewinding as Rewind does) and stop.

## An Xbox controller

An Xbox controller works as a remote, by USB cable or Bluetooth (`./deploy/pair-remote.sh xbox`); so does any
controller Linux takes as a gamepad, with its buttons in the same places. Matinee reads it itself, so it works in the
menus and during playback alike; a held button acts as a held key.

| Button | Is |
|---|---|
| D-pad, left stick | Up / Down / Left / Right |
| A | OK (held: OK held) |
| B | Back |
| X | Play/Pause |
| Y | Search |
| Menu ☰, View ⧉ | Menu |
| Xbox button | Home (held: the main menu) |
| LB / RB | Rewind / Fast forward |
| LT / RT | Quieter / louder |

On the TV computer only (under `kino --session`): on a desktop the controller may be meant for a game.
Its USB wireless adapter needs a driver Linux does not bring along
([xone](https://github.com/dlundqvist/xone)); by cable and Bluetooth it needs nothing.

## Jellyfin's remote control

Every Matinee signed in to Jellyfin shows up there as a device that can be controlled: in Jellyfin's web app under the
cast button, or in a phone app that speaks Jellyfin's remote control. Its commands work as the remote's keys would,
in the menus as during playback:

- **Arrows, select, back, home, menu:** as the keys of the same names; the menu key opens the info panel during a
  video.
- **Play/pause, pause, stop, next, previous, fast forward, rewind:** as the media keys, with music too.
- **Seek:** straight to that place.
- **Volume, mute:** as the volume keys (on the TV computer the device's sound).
- **Audio and subtitles:** picked in the player directly, while a film or episode plays as the file itself (not
  converted by Jellyfin).
- **Text:** typed into the search that is open, or opens the search of the part you are in with it.
- **A message:** shown on the screen for a few seconds.
- **Play on:** starts a film, episode or video here, where it was left or from where it says.
- **Matinee Remote** ([remote/](../remote/README.md)), a page for the phone, also starts what Jellyfin does not know: an
  album, an audiobook or a podcast's episode. For the parts the phone is not signed in to, Matinee puts short lists with
  the Jellyfin account (the music played last and new, the books and episodes begun and new), only while "Same on
  Every Device" is on.

Any command counts as someone watching, so "Still watching?" does not come. Who may control which device is
Jellyfin's to say: a user can control their own sessions, others' only with the right to (Dashboard → Users).

## A mouse or trackpad

On a Mac (or any computer with a mouse) Matinee can be used with the mouse too. The pointer shows once the mouse moves
and hides again after three seconds still (ten over something to click) or at any key.

| Mouse | Does |
|---|---|
| Moving over something | Gives it the focus, without the page moving under the pointer; the picture and text at the top follow once the pointer rests there a moment, so passing over a row does not flicker through it |
| Resting on a title cut short | Shows all of it |
| Click | OK, held or not |
| Right click on a tile | Its options, as OK held |
| Wheel, two-finger swipe | Moves the page as far as the fingers go, sideways the row under the pointer; a mouse wheel's notch glides. Once it stands, what the pointer is over has the focus. On the main menu, in the programme guide and on the keyboard of a search it steps like the arrow keys instead (a trackpad's coasting not counted) |
| ‹ and › at the ends of a row | A page of the row further or back; they show over a row longer than the screen |
| Right click elsewhere, the mouse's back button, the ‹ button top left | Back (the ‹ button shows with the pointer, except on the main menu) |

In the player:

| Mouse | Does |
|---|---|
| Moving it | Shows the bar, and the pointer for a second; over the bar's line the time there, with its picture |
| Click | Pause and play; on the bar's line: goes there; on the offer at the bottom right (skip the intro, the next episode): takes it |
| Double click | Full screen on and off |
| Wheel up and down | Louder and quieter; with natural scrolling (macOS, niri, GNOME, KDE) turned round, so a swipe up is louder all the same |
| Sideways swipe | 10 seconds back and on, as left and right |
| Right click | The info panel (on a channel live: the volume); again: closes it |
| In the info panel | The pointer picks the row it is over, a click takes it, a click on a tab shows that tab, a click beside the panel closes it; the wheel goes up and down the list |

## The player's bar

The bar at the bottom shows the title, when the video ends ("Ends at 23:41"), the progress with the chapters as small
gaps, the time played and the time left. On a computer it also says, between the times, how to reach the info panel
(Tab or a right click), as a laptop's keyboard has no menu key.

Left and right move a mark along the bar, its time above it. Playback jumps there once, a moment after the last press,
instead of loading the stream again for every press. While paused, it waits for OK or Play; Back leaves the mark.

If Jellyfin made trickplay pictures (Dashboard → Libraries → a library → "Enable trickplay image extraction"), the
picture at the mark shows above it. Matinee fetches them a tile at a time while seeking, and ffmpeg cuts them apart.

## The info panel

Menu (on a keyboard Tab, or a right click) opens the info panel at the top while the video plays on. It has four tabs:

- **Info:** the name, year, genres and rating (or the show and episode), the description, when it ends
- **Chapters**
- **Audio** and **Subtitles:** each track with its language, its sound (e.g. Dolby Digital 5.1) or "Forced"
- **Speed**
- **Continue on:** the other Matinees signed in to the same Jellyfin user that are on now. Picking one ends playback here
  and it goes on there, from the same place (a film or episode, or one of one's own channels
  live). Matinee tells the other device through Jellyfin, so they need not be on the same network. The other device
  says it goes on; if it does not within a few seconds (a laptop closed meanwhile is still among Jellyfin's
  sessions for a while), it goes on here instead, from where it was, and says so.

Left and right switch tabs, down goes into a list, OK picks. Up from the tabs, Back, or 10 seconds without a key close
it. The chapters key (or C) opens it on Chapters.

## Next episode and "Still watching?"

At the end of an episode the next one starts after a 5 second countdown. After three episodes in a row without a key
press, Matinee asks "Still watching?" instead: OK plays on, Back stops. Without an answer, playback stops after a minute.
How many episodes (2, 3 or 5), or never: `"ask"` in `config.json` or on the settings page.
