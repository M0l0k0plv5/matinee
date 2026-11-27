# A media server for Matinee

Matinee plays from your own servers. Without one yet, there are two ways to one.

## On the TV computer itself (all in one)

`install.sh` asks at the end ("No Jellyfin server yet?"), or later: `~/kino/deploy/server.sh`. It sets up:

- **Jellyfin in a container** (Podman, started by systemd from `/etc/containers/systemd/kino-jellyfin.container`,
  a new image once a day by `podman-auto-update.timer`), on port 8096, with the graphics chip for converting videos.
  Its data is in `/var/lib/kino-jellyfin`.
- **The media folder** `/srv/kino-media` (or `KINO_MEDIA`), Jellyfin sees it as `/media`, read only.
- **A Samba share** "Matinee" of that folder on the home network, for this user with a password of its own (not the
  login's), kept in `~/.config/kino/share.json` and shown on the TV.
- On Fedora and openSUSE, firewalld opened for Samba, 8096/tcp and Jellyfin's discovery (7359/udp).
- **Jellyfin's startup wizard done at once**, with a first user of its own (`kino-setup`, a password made up there,
  kept in `~/.config/kino/local-admin.json`, mode 600): until it is done, anyone on the home network could do it and
  be the server's administrator. If Jellyfin does not answer within ten minutes, `server.sh` says so; run it again.

Then on the TV: the first start's Jellyfin step (or Movies & Shows) shows **Jellyfin on This Device**: a user name,
a password (Jellyfin's first user, its administrator) and **Set Up on This Device**. Matinee gives `server.sh`'s first
user that name and password (and removes `local-admin.json`), sets Jellyfin's language, makes the libraries Movies and
Shows (Filme and Serien in German) as folders in the media folder, and signs in. Jellyfin's apps on phones and tablets find the same server with the same
user.

Films go onto it from a computer: **Settings → Movies & Shows → Put Films on This Device** shows the address
(`\\192.168.1.20\Matinee` on Windows, `smb://192.168.1.20/Matinee` on a Mac), the user name and the password. Films into
the films' folder, each show in a folder of its own in the shows' folder (`Show Name/Season 01/…`). Jellyfin notices
new files by itself.

What the computer needs: room for the films (an internal SSD or disk; a USB disk works when mounted at the media
folder, e.g. by an fstab line with `nofail`), and a graphics chip that converts videos for phones and tablets
(Intel's Quick Sync from the 7th generation on handles 4K HEVC). The TV itself plays almost everything directly.

## On a NAS or another computer

`deploy/nas/docker-compose.yml` has Jellyfin, and optionally Navidrome (music) and Audiobookshelf (audiobooks):

1. Copy the folder `deploy/nas` onto the NAS, e.g. `/volume1/docker/kino`.
2. Beside it a file `.env` with the folder of the media: `MEDIA=/volume1/media` (and `MUSIC=…`, `BOOKS=…`
   for the subfolders of music and audiobooks, if not `Music` and `Audiobooks`).
3. Start it:
   - **Synology** (DSM 7.2, Container Manager): Project → Create → the folder, "use existing docker-compose.yml".
   - **QNAP** (Container Station 3): Applications → Create → paste the file.
   - **Unraid** (Compose Manager plugin): Add New Stack, paste the file.
   - **Any computer:** `docker compose up -d` (with music and audiobooks: `--profile music --profile books`).
4. Open `http://<the NAS>:8096` once in a browser for Jellyfin's own setup, and add the libraries there.

Jellyfin runs on the host network, so Matinee's sign-in finds it by itself. An Intel NAS can convert videos with its
graphics chip: uncomment the `devices` lines.

## Where the films come from

Matinee plays what is on your server: your own recordings, films bought without copy protection, discs without it.
Getting around a copy protection (most bought DVDs and Blu-rays) is not allowed in Germany (§ 95a UrhG) and many
other countries; Matinee does not help with it.
