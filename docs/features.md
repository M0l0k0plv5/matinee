# Features

## Everywhere

- **Main menu:** a dock of tiles (Up Next, Movies & Shows, Music, Audiobooks, Search, Settings, and
  Podcasts once a Podfetch is set up). Above it, the shelf shows what the focused tile holds: Jellyfin what is being
  watched (otherwise what is new), Navidrome the albums played last, Audiobookshelf the books begun (otherwise what is
  new), Podfetch the episodes begun (otherwise the newest). Up goes into the shelf, OK opens or plays, Back comes back.
  Each shelf shows what it showed last at once (Matinee switched on, the servers not answering yet) and swaps in theirs
  as soon as they answer; kept per Jellyfin user, forgotten on signing out, another account or language.
- **Up Next:** everything begun anywhere (movies and shows, audiobooks, podcasts), the latest
  first, what is watched before what is listened to: audiobooks and podcasts come after the videos (three of them
  kept on the row however many videos were begun). Matinee starts on it.
- **Search everything:** the Search tile searches Jellyfin (movies, shows, collections), Navidrome (artists, albums,
  songs), Audiobookshelf (books, also by their author and series) and Podfetch at once. Each part
  shows up as soon as it is there, a slow one holding up no other (the titles a typo away are waited for 3 seconds, searched again once they are there), two lines of it at most (the music's artists first,
  then albums, then songs; a single's song not beside its album), so one part cannot push the others off the page;
  "More in …" goes on in that part's own search with the same words. A letter grid for the remote; on a keyboard, just
  start typing, or paste with ⌘V or Ctrl+V.
- **Remove from a row:** OK held on a tile in Continue Watching, Continue Listening, Recently or Most Played offers
  to take it off. Continue Watching forgets the position on the Jellyfin server. Shows whose next episode stands there, books and Navidrome's
  albums are only hidden in Matinee, because neither server can forget them; the general settings show them again.
- **Continue Watching** holds what is begun, then the next episode of each show being watched. A show counts once
  an episode of it has been seen: one whose first minutes were only looked into does not stand there with episode 1,
  as Jellyfin's own Next Up would have it. What is stopped in its credits leaves it: a film or episode in the credits
  Jellyfin knows (e.g. from the Intro Skipper plugin) is marked watched, else Jellyfin's own 90 % count.
- **Account, moved or signed out** (Settings → Account): everything of yours (the servers and sign-ins, what was hidden,
  the tracks chosen, the settings) goes once to another Matinee in your tailnet. The
  device that is to have it waits with a six-digit code on its screen ("Take Over from Another Device", also on the
  welcome screen of a fresh install); on the one that has it, "Move to Another Device" lists the devices waiting,
  then asks for that code and Jellyfin's password, with which it signs the other device in with a session of its
  own (the password is kept nowhere). What belongs to a device stays (see [configuration](configuration.md#account)).
  "Sign Out and Reset" ends the device's session on Jellyfin and removes everything of yours from it, as freshly installed.
- **Sign In Everywhere** (Settings → Account): one user name and password for Jellyfin, Navidrome, Audiobookshelf
  and Podfetch at once, on the servers set up on the device: for another person on their own TV computer (moved
  over from yours, say, then signed in as them). Audiobookshelf gives Matinee its own token for them, so no API key has
  to be made. A service refusing them stays signed in as before; the page says how it went at each.
- **The same on every device** (Settings → Account → "Same on Every Device", on by itself): your settings, the audio
  and subtitles chosen by hand for a show or film, the screensaver's weather place and what was taken off a row are kept with your
  Jellyfin user (among its display preferences, for the client "kino"), so every Matinee signed in
  to it has the same, without an account of its own. Each device looks every two minutes and right after a change of
  its own; when two devices changed the same thing, the later change wins. A device that syncs for the first time
  keeps what only it has and adds it; of what both have, what the other devices have wins. Off, a device keeps its
  own. The settings show when it last synced. Settings of the device itself stay its own: its screen and display
  mode, effects, size (a television's or a desk's), text size, picture quality, frame rate, updates, turning off, the sound output's delay and volume.
  A setting changed on another device is used at once (another language loads the page anew, once nothing plays).
- **Your devices** (Settings → Account, below "Same on Every Device"): every Matinee signed in to the account, this one
  first, with how many versions it is behind the newest ("Up to date", "7 versions behind") and when it last synced,
  to see at a glance which one did not update.
- **Simple settings** (Settings → System), for someone who should not have to see servers, accounts or the network:
  only everyday settings show (language, start, effects, sounds, screensaver, video and audio, devices, restart and
  shut down), and the dock only the services set up. A four-digit code turns them on, chosen twice; "All Settings"
  asks for it again. Forgotten: remove `simple` from `config.json`.
- **Help:** Settings → Using Matinee lists every key of the remote and what it does, also reached from any held OK.
  The first few times a tile has the focus, a tip on its picture says that holding OK has more; once the options have
  been opened, it does not come again.
- **Status** (Settings → System → Status): how the device is doing, checked when it opens: Jellyfin with its address
  and version, Navidrome, Audiobookshelf and Podfetch if set up, the last playback, the nightly update (when it ran,
  and whether it went well), the disk's free space and whether the clock is set. A dot for each: fine, to look at, or
  broken. The servers are asked at once, none holding the page up for more than a few seconds: handy beside the
  report to support.
- **When something does not answer** (Jellyfin, Navidrome, Audiobookshelf or Podfetch asleep or restarting), the page
  says so in plain words and tries again by itself every ten seconds; OK tries at once. A service set up before
  shows this, not its sign-in form.
- **When a sign-in is needed again** (a password changed, the device signed out by the server, a server now asking for
  a sign-in): the main menu's shelf above the tile says so ("Podcasts needs you to sign in again"), and OK on the tile
  opens the sign-in, rather than an empty shelf.
- **Starting** a movie, an episode or a video: the screen goes black, and after a moment shows what starts, until the
  player is in front.
- **Screensaver:** after 10 minutes without a key, black with a dim clock that moves now and then (cage never turns
  the screen off). With music, the song; any key wakes it. A movie paused for 15 minutes stops, and the position
  stays in Jellyfin. `"screensaver"`: 5, 10 or 30 minutes, or `"off"`.
- **Weather:** with a place chosen in the general settings ("Choose a place": typed like a search, then picked from
  the places of that name), the screensaver shows the weather under the clock: a symbol, the temperature and the
  day's highest and lowest, from [Open-Meteo](https://open-meteo.com/) every quarter of an hour. No account and no
  key; only the town's coordinates go out.
- **Sleep timer:** S in the player (or a remote's sleep key), the moon button on "Now Playing": 30, 60 or 90 minutes,
  at the end of the movie, episode or queue (with a book also at the end of the chapter), or off. When it is up, the
  sound fades out and playback stops; the volume stays as it was. On the thin client it can shut down afterwards.
- **Bluetooth:** Settings → "Speakers and headphones" looks for devices nearby. OK on one pairs, trusts and connects
  it (or disconnects it), OK held forgets it. The sound then plays on it, and on it alone: the speaker or headphones
  connected before let go once the new one is there (they stay paired). A remote or keyboard is never disconnected
  from there, since it is how Matinee is used.
- **Audio sync:** Settings → Video and Audio → "Audio Sync" plays the sound 50 to 300 ms earlier, for a Bluetooth
  speaker or headphones that lag behind the picture. It is kept for each output, so headphones, a soundbar and the
  TV's own speakers each have their own value.
- **niri:** on a niri desktop, while a video plays, Matinee's own window (black behind the player) waits on the empty
  workspace after the others, so the windows next to the player are the desktop's; it comes back at the end.

## Other devices on the TV

A streaming stick (for Netflix, Prime Video and the like, in their full quality), a console or a player at another
HDMI input of the TV gets a tile on the main menu: Settings → Other Devices on the TV → Add a Device, with the TV's HDMI
input it is plugged into and its name. OK on the tile pauses Matinee's music and switches the TV to that input over
HDMI-CEC; the next key on Matinee's remote switches it back (and does nothing else). The TV needs CEC turned on (Samsung
calls it Anynet+, Sony Bravia Sync, LG SimpLink), and the computer a way to speak it: most graphics chips do not, so a
Pulse-Eight USB-CEC adapter or a DisplayPort to HDMI adapter that passes CEC on (see [setup](setup.md#hdmi-cec)).
Without one the settings say so and there are no tiles.

## Movies and shows (Jellyfin)

- **Libraries:** sort (A–Z, recently added, release year, rating), show unwatched only, filter by genre. The choice is
  kept when you come back.
- **Collections:** their own library. A collection shows its movies in release order and plays the first unwatched one.
- **My List:** the options of a movie, show or collection (OK held), or the button on its page, add it to Jellyfin's
  favorites: a row on the home screen and a heart on the tile.
- **Complete seasons:** a show's poster says whether all its aired episodes are there: `✓ 13` in green, or `11/13` in
  red when some are missing (specials left out). Each season on the show's page says the same, and a season's missing
  episodes stand dimmed in their place; a season with none of its episodes in the library stands dimmed among the
  others (`0/10`), so a poster's `10/20` shows where the missing ones are. Episodes not aired yet do not count.
  Jellyfin has to know the missing ones:
  a metadata provider's placeholders (e.g. TheTVDB's Missing Episode Fetcher) and *Display missing episodes* in the
  user's profile; without any, there are no badges. The setting `badges` shows only the incomplete ones, or none.
- **Cast:** a movie's, show's or episode's cast stands as faces in a row; OK on one opens their page: who they are
  (born, where, Jellyfin's text about them) and the movies and shows with them in the library, the newest first.
- **Trailer:** the button on a movie or show page plays its trailer, when the library has one.
- **More Like This:** movie and show pages end with similar titles. Jellyfin needs a second or more to find them, so
  Matinee works through the library in the background (one title at a time, gently on the server) and keeps the result
  in `~/.cache/kino/similar.json`. The row is there as soon as the page opens. With a big library the first pass takes
  a while; new titles are looked up as soon as Jellyfin reports them.
- **End time:** detail pages say when a movie or episode would end if started now, and how much of a started one is
  left.
- **Skip intro:** uses Jellyfin's media segments (e.g. from the Intro Skipper plugin). During an intro or recap the
  player shows "OK ▸ Skip Intro"; during an episode's credits, "OK ▸ Next Episode". Settings → "Skip Intros": Offer,
  Automatic (skipped right away, once) or Off.
- **Audio and subtitles per show and per movie:** a track chosen in the player's info panel (subtitles also "Off")
  carries over to the next episode and is remembered for the show, also after a restart (`~/.config/kino/tracks.json`);
  a movie goes on with the tracks chosen for it. Other shows and movies keep the default languages. With Settings →
  Subtitles "Off" (the default) no subtitles come by themselves, not even forced ones, and subtitles turned on in the
  player are there for that playback only; with "Automatic" and "Always" the subtitle choice is remembered too. Then
  forced subtitles (a sign, a letter, a line in another language) are shown even when the audio is in their language,
  the full ones in that language only with "Always", also when Jellyfin converts the video.
- **Find Subtitles:** for a movie or an episode without subtitles in the right language, the last row of the info
  panel's subtitles tab ("Find Subtitles…") lists subtitles from the internet, the subtitle language set first; OK
  has Jellyfin download them, keeps them with the video (so they are there on every device) and turns them on. The
  same search is among a movie's or an episode's options (OK held). Jellyfin finds them through a subtitle plugin on
  the server, such as OpenSubtitles (Dashboard → Plugins); without one the search finds nothing and says so.
- **Volume:** the volume set in the player is kept for the next movie.
- **Videos the computer cannot decode:** Matinee asks the graphics chip (`vainfo`) which videos it decodes itself. One it
  cannot, such as 4K HEVC with 10 bits and HDR on an older Intel chip, would stutter in software: Jellyfin converts it
  to 1080p H.264 (at most 12 Mbit/s, HDR mapped to SDR on the server, stereo) and the player says so.
- **Frame rate:** in cage, while a movie or episode plays whose frame rate does not fit the screen (25 fps on 120 Hz),
  the screen switches to one that does (50 Hz), so the picture moves evenly. The menus get theirs back afterwards.
  "Match the frame rate" in the settings turns it off.
- **The library as channels:** "Channels", the button after the libraries on Jellyfin's start: a channel for each of the library's
  larger genres (their films, and the episodes of their series) and for each decade of its films, with a dozen films or
  episodes at least. Each runs its programme without end, whether anyone watches or not: tuning in joins the film or
  episode where it is by now, the next one follows, up and down zap. A series' episodes come in their order, spread
  over the round between the others. The programme guide, the first tile on their page, shows what runs on each; OK on a programme that
  ran or is still to come plays that film or episode itself. A genre the library has under two names ("Crime" and
  "Krimi") is one channel. Each can be left out in Jellyfin's settings ("Channels"). The languages and subtitles are
  those chosen for the series or in the settings, as when it is played by itself. Nothing watched on a channel is
  told to Jellyfin: no position kept, nothing marked as watched.

### Checking the library

Settings → Movies & Shows → Check Library looks through every film and lists those that do not match their file:

- the file names another film (a `[tmdbid-…]` or `[imdbid-…]` tag in the file's or the folder's name) than the one
  Jellyfin took it for,
- the year in the file's name is another one (a year that is part of the title, as in *Blade Runner 2049*, does not
  count),
- Jellyfin did not identify it at all,
- or its picture is a still from the film (wider than high) instead of a poster.

Each line shows the film as it is and what would fix it: the film behind the tag, TheMovieDb's film of the file's
title and year, or an upright poster. Nothing changes until you confirm, film by film or all at once; Matinee then uses
Jellyfin's own Identify and replaces the pictures, so the details and images are fetched anew. It needs a Jellyfin
administrator's account. Mix-ups of this kind usually come from TheTVDB's matches for films, which have no year: in
Jellyfin's library settings, put TheMovieDb above TheTVDB for films.

### Staying in sync with Jellyfin

- Start, the position every 10 seconds and the end are reported, so what you watch in Matinee shows up in other Jellyfin
  apps. Jellyfin's dashboard sees which tracks play, and Matinee's git commit as its version.
- Matinee keeps a WebSocket connection to Jellyfin. If you keep watching elsewhere or mark something, the UI updates by
  itself (not while you are navigating).
- Jellyfin keeps no position in the first 5 % of a film (its MinResumePct). Stopped there after the first minute, Matinee
  keeps the position itself (synced between your Matinees), so it stands in Continue Watching and the film's page offers
  to go on from there; once you are
  further in, or have watched it to the end, it is Jellyfin's again. Marked watched or unwatched, the position kept is
  forgotten too, so it leaves Continue Watching.
- If the final position cannot be sent when playback ends, it is kept in `~/.config/kino/pending.json` and sent once
  the server is back, unless someone kept watching elsewhere meanwhile, so nothing newer is overwritten. A report the
  server keeps rejecting is given up after a day.

## Music (Navidrome)

- **Browsing:** albums new, recently and often played, and at random; all albums (by artist, A–Z, newest, random),
  artists, playlists, search and "Shuffle All". OK on a song plays the album or playlist from there.
- **Now Playing:** the cover, the progress and the queue (OK pauses, the buttons skip). Elsewhere in Matinee a bar at the
  bottom shows what plays, and the play key pauses it. Down from a page's last row reaches the bar: OK opens Now
  Playing, OK held stars the song, up or Back go back. A movie or an episode stops the music.
- **Go to Artist, Go to Album:** in the options (OK held) of a song or an album, also of the song playing in the
  music's bar, as from a podcast's episode to its show. Every part's options begin with where a thing belongs: an
  episode to its show (Jellyfin's and a podcast's).
- **Stop following from the tile:** a podcast's options (OK held) offer to stop following it.
- **Play next, add to queue:** while music plays, the options of a song, album or playlist (OK held) put it right after
  the current song or at the end of the queue.
- **Continue on:** the screen button on "Now Playing" lists the other Matinees of the account that are on; one picked,
  the queue goes on there in the same song and place, and stops here. Like a video's (see controls.md), only with
  that Matinee's answer; one without a Navidrome of its own does not take it, and the music goes on here again.
- **Favorites, lyrics, radio:** OK held on a song, album or artist stars it; "Favorites" lists them. The lyrics button
  on "Now Playing" shows the song's lyrics: with times, the line being sung lights up; without, the text scrolls along.
  "Radio" plays the internet radio stations set up in Navidrome.
- **Scrobbling:** Navidrome is told what plays; a song counts as played after half of it (or four minutes). "Tell
  Navidrome what plays" off keeps Navidrome from recording any history.

## Audiobooks (Audiobookshelf)

- **Browsing:** the libraries, the books begun ("Continue Listening") and what is new in each. A library lists its
  episodes in their order ("Part 2" before "Part 10"). A book's page shows who wrote and reads it, how much is left,
  and its chapters. The search finds books by their title, their author ("Orwell" finds 1984) and their series.
- **Listening:** a book plays in the background like music. "Now Playing" lists its chapters (OK goes to one); the
  buttons and the remote's fast forward and rewind go 30 seconds ahead and back; up and down go to the chapter before
  and the next one. The speed button steps from 0.8× to 2× and is kept for the next book.
- **Progress:** Audiobookshelf hears where the book is every 15 seconds and when it stops, so its own app carries on
  from there. Heard to the end, a book is marked as finished. A book's page marks it as heard, or as not heard (from the
  start again), a begun one either way, as a begun movie can be marked as watched or not; OK held on a book's tile
  does the same, and starts a begun one over.

## Podcasts (Podfetch)

- **Setting up:** Settings → Podcasts, with the server's address and, if Podfetch asks for one, its user name and
  password (both empty for a Podfetch without sign-in). Podfetch's OpenID Connect sign-in is not supported. Only
  then does the Podcasts tile join the dock.
- **Browsing:** All Podcasts at the top, as a library on Jellyfin's start: a grid of them with the categories the feeds
  give above it (such as Technology or News, the one with the most podcasts first) to narrow it down, as a library's
  genres; then the episodes begun ("Continue Listening"), the newest episodes of all podcasts, and the podcasts, those
  heard first (the last one first). All Podcasts goes by name, with the focus on the podcast heard last. A podcast's
  page shows who makes it, its categories and its episodes, the newest first, with the day each came out and how much is
  left; one heard to the end is dimmed. The search (top left) finds podcasts by name or maker, and the newest episodes
  by title.
- **An episode's options** (OK held): Go to Show, Mark as Heard, Mark as Not Heard (a begun
  one has both), Start Over; on the tiles, in a show's list of episodes and on the player's bar while one plays.
- **Listening:** an episode plays like an audiobook: in the background, 30 seconds back and ahead, the speed button,
  the sleep timer, and its chapters where Podfetch has them. One begun carries on where it was left, one heard to the
  end starts again.
- **Following new podcasts:** the podcasts' search also looks in the public podcast directory (through Podfetch's own
  search) once typing pauses, below your own: "New Podcasts to Follow", without those already followed. OK on one asks,
  then Podfetch adds it and its page opens; its episodes come in as Podfetch fetches them.
- **Stop Following:** on a podcast's page, asked first: Podfetch forgets the podcast, its episodes and where they
  were heard, and keeps the episodes it downloaded or deletes them too.
- **Progress:** Podfetch hears where the episode is every 15 seconds and when it stops, so its web interface and app
  carry on from there. Heard to the end, it is told the whole length, which marks it as heard.
- **The start at once:** opened again, the podcasts' start shows what it showed last and asks Podfetch behind it (it
  takes a second or two for what was heard); what changed meanwhile is put in where the page stands.

