"""Jellyfin on the TV computer itself (backend/local_server.py): seen while fresh, set up from the TV, signed in."""
import json
import unittest
from pathlib import Path

from helpers import Backend, temp_dir
from mockjf import MockJellyfin


class LocalServerTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.addCleanup(self.mock.stop)
        self.mock.fresh = True
        self.media = Path(temp_dir("kino-media-"))
        self.backend = Backend("", password=None, env={"KINO_LOCAL_JELLYFIN": self.mock.url, "KINO_MEDIA": str(self.media),
                                                       }, language="de").start(wait_ready=False)
        self.addCleanup(self.backend.stop)

    def test_set_up_from_the_tv(self):
        d = self.backend.request("/jellyfin/local")[1]
        self.assertEqual((d["there"], d["fresh"], d["share"], d["folders"]), (True, True, None, ["Filme", "Serien"]))
        self.assertEqual(self.backend.request("/jellyfin/local/setup", {"username": "anna", "password": "abc"}),
                         (400, {"error": "local_password"}), "the server's administrator: not without a password")
        code, r = self.backend.request("/jellyfin/local/setup", {"username": "anna", "password": "geheim1"})
        self.assertEqual(code, 200, r)
        paths = [p for p, _ in self.mock.startup]
        self.assertEqual(paths, ["/Startup/Configuration", "/Startup/User", "/Startup/RemoteAccess", "/Startup/Complete"])
        self.assertEqual(self.mock.startup[0][1]["UICulture"], "de-DE", "in Matinee's language")
        self.assertEqual(self.mock.startup[1][1], {"Name": "anna", "Password": "geheim1"})
        self.assertEqual(self.mock.folders, [("Filme", "movies", ["/media/Filme"]), ("Serien", "tvshows", ["/media/Serien"])])
        self.assertEqual({o.get("EnableRealtimeMonitor") for o in self.mock.folder_options}, {True},
                         "new files found by themselves")
        self.assertEqual(self.mock.refreshes, 1, "a whole scan once: Jellyfin watches the folders only after one")
        self.assertTrue((self.media / "Filme").is_dir() and (self.media / "Serien").is_dir(), "its folders made")
        self.assertTrue((self.media / "Filme" / ".kino").is_file() and (self.media / "Serien" / ".kino").is_file(),
                        "not empty: Jellyfin watches no empty folder")
        s = self.backend.request("/session")[1]
        self.assertTrue(s.get("userId"), s)
        conf = json.loads((self.backend.conf / "config.json").read_text())
        self.assertEqual((conf["server"], conf["username"]), (self.mock.url, "anna"))
        self.assertFalse(self.backend.request("/jellyfin/local")[1]["fresh"])
        self.assertEqual(self.backend.request("/jellyfin/local/setup", {"username": "x", "password": "geheim1"}),
                         (400, {"error": "local_done"}), "once only")

    def test_first_user_of_server_sh_made_the_persons_own(self):
        """deploy/server.sh did the startup wizard at once (nobody else on the network to be first at it): its user
        becomes the person's, by the name and password given on the TV."""
        self.mock.fresh = False
        self.mock.accounts["kino-setup"] = "made-up-1"
        admin_file = self.backend.conf / "local-admin.json"
        admin_file.write_text(json.dumps({"user": "kino-setup", "password": "made-up-1"}))
        self.assertTrue(self.backend.request("/jellyfin/local")[1]["fresh"], "the TV's part not done yet")
        self.mock.busy_paths.add("/Sessions/Logout")  # the setup's own session cannot be ended: no matter
        code, r = self.backend.request("/jellyfin/local/setup", {"username": "anna", "password": "geheim1"})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.mock.startup, [], "the wizard done before")
        self.assertEqual(self.mock.accounts, {"anna": "geheim1"}, "no other user left, nor its password")
        # the password first, each step written down at once: a setup broken off between them can be tried again
        self.assertEqual([c[0] for c in self.mock.user_changes], ["password", "name"])
        self.assertEqual((self.mock.system_conf["UICulture"], self.mock.system_conf["PreferredMetadataLanguage"]),
                         ("de-DE", "de"), "in Matinee's language")
        self.assertEqual(self.mock.system_conf["ServerName"], "Mock", "the rest of its configuration as it was")
        self.assertEqual([f[0] for f in self.mock.folders], ["Filme", "Serien"])
        self.assertFalse(admin_file.exists(), "its password is no longer the server's")
        conf = json.loads((self.backend.conf / "config.json").read_text())
        self.assertEqual((conf["server"], conf["username"]), (self.mock.url, "anna"))
        self.assertFalse(self.backend.request("/jellyfin/local")[1]["fresh"])
        self.assertEqual(self.backend.request("/jellyfin/local/setup", {"username": "x", "password": "geheim1"}),
                         (400, {"error": "local_done"}))

    def test_tried_again_after_breaking_off(self):
        """A library made before the setup broke off: not made twice, the first user still taken over."""
        self.mock.fresh = False
        self.mock.accounts["kino-setup"] = "made-up-1"
        self.mock.folders.append(("Filme", "movies", ["/media/Filme"]))
        (self.backend.conf / "local-admin.json").write_text(json.dumps({"user": "kino-setup", "password": "made-up-1"}))
        self.assertEqual(self.backend.request("/jellyfin/local/setup", {"username": "anna", "password": "geheim1"})[0], 200)
        self.assertEqual([f[0] for f in self.mock.folders], ["Filme", "Serien"])
        self.assertEqual(self.mock.accounts, {"anna": "geheim1"})

    def test_server_sh_broken_off_before_the_wizards_end(self):
        """Its user's file there, the wizard not done: done here, as without deploy/server.sh's part."""
        (self.backend.conf / "local-admin.json").write_text(json.dumps({"user": "kino-setup", "password": "made-up-1"}))
        self.assertEqual(self.backend.request("/jellyfin/local/setup", {"username": "anna", "password": "geheim1"})[0], 200)
        self.assertEqual(self.mock.startup[1][1], {"Name": "anna", "Password": "geheim1"})

    def test_nothing_changed_without_its_folders(self):
        """The media folder not writable (Matinee's service without it, say): Jellyfin left as it was, to try again."""
        self.media.chmod(0o500)
        self.addCleanup(self.media.chmod, 0o700)
        self.assertEqual(self.backend.request("/jellyfin/local/setup", {"username": "anna", "password": "geheim1"}),
                         (400, {"error": "local_folders"}))
        self.assertEqual(self.mock.startup, [], "its startup wizard not begun")
        self.assertTrue(self.backend.request("/jellyfin/local")[1]["fresh"])

    def test_jellyfin_still_starting_not_taken_for_set_up(self):
        self.mock.stand_in = 1
        d = self.backend.request("/jellyfin/local")[1]
        self.assertEqual((d["there"], d["fresh"]), (False, False), "not there yet, rather than done")
        self.assertEqual(self.backend.request("/jellyfin/local")[1]["fresh"], True)

    def test_the_share_and_no_server(self):
        (self.backend.conf / "share.json").write_text(json.dumps({"name": "Matinee", "user": "me", "password": "ab12-cd34"}))
        share = self.backend.request("/jellyfin/local")[1]["share"]
        self.assertEqual((share["name"], share["user"], share["password"]), ("Matinee", "me", "ab12-cd34"))
        self.assertTrue(share["host"])
        self.mock.stop()
        d = self.backend.request("/jellyfin/local")[1]
        self.assertEqual((d["there"], d["fresh"]), (False, False))


if __name__ == "__main__":
    unittest.main()
