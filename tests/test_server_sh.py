"""deploy/server.sh (Jellyfin and its share on the TV computer) run through on each distribution, against a folder in
place of the system (KINO_SYSROOT) and with the system's tools played by small scripts: the package managers, Podman,
systemd, Samba's smbpasswd, firewalld, SELinux's chcon. Each acts as the real one does where server.sh once went wrong
on it (pacman asking which runtime, firewall-cmd refusing a service and ports in one call)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mockjf import MockJellyfin

ROOT = Path(__file__).resolve().parent.parent
SERVER_SH = ROOT / "deploy" / "server.sh"
# the real tools server.sh needs besides the played ones; nothing else is on its PATH (a Linux machine running the
# tests has its own pacman or apt-get, which would pick the distribution)
REAL_TOOLS = ("sh", "grep", "tee", "cp", "mkdir", "chown", "chmod", "cat", "id", "uname", "printf", "env", "rm", "sleep")
OS_RELEASE = {"arch": "ID=arch", "debian": "ID=debian", "fedora": "ID=fedora", "opensuse": 'ID="opensuse-tumbleweed"'}
PACKAGE_MANAGER = {"arch": "pacman", "debian": "apt-get", "fedora": "dnf", "opensuse": "zypper"}

LOG = 'echo "$(basename "$0") $*" >> "$FAKE_LOG"\n'
FAKES = {
    "sudo": 'exec "$@"\n',
    "timeout": 'shift\nexec "$@"\n',
    # pacman asks which of three runtimes Podman gets unless one is named, and a script cannot answer
    "pacman": LOG + 'case " $* " in *" podman "*) case " $* " in *" crun "*) ;; *)\n'
              '  echo ":: There are 3 providers available for oci-runtime:"; echo "Enter a number (default=1): "\n'
              '  echo "error: invalid number" >&2; exit 1;; esac;; esac\n',
    "apt-get": LOG, "dnf": LOG, "zypper": LOG, "podman": LOG, "chcon": LOG,
    "systemctl": LOG + 'case "$1" in is-active) [ -n "$FAKE_KINO_ACTIVE" ];; esac\n',
    "smbpasswd": 'echo "smbpasswd $* $(cat | tr "\\n" " ")" >> "$FAKE_LOG"\n',
    "getent": '[ -n "$FAKE_NAME_KNOWN" ]\n',
    # as firewalld's own: one call opens services or ports, not both
    "firewall-cmd": '[ -n "$FAKE_FIREWALL" ] || exit 252\n'
                    'case " $* " in *" --add-service="*" --add-port="*|*" --add-port="*" --add-service="*)\n'
                    '  echo "firewall-cmd: error: argument --add-port: not allowed with argument --add-service" >&2; exit 2;; esac\n'
                    + LOG,
}


class ServerShTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kino-server-sh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sys = self.tmp / "sys"
        self.media = self.tmp / "media"
        self.home = self.tmp / "home"
        self.log = self.tmp / "calls"
        (self.sys / "etc").mkdir(parents=True)
        self.home.mkdir()
        self.log.touch()

    def run_server_sh(self, distro, env=None, **fake):
        (self.sys / "etc" / "os-release").write_text(OS_RELEASE[distro] + "\n")
        bin_ = self.tmp / f"bin-{distro}"
        bin_.mkdir(exist_ok=True)
        for name in [*REAL_TOOLS, "tr", "basename"]:
            if not (bin_ / name).exists() and (real := shutil.which(name)):
                (bin_ / name).symlink_to(real)
        if not (bin_ / "python3").exists():
            (bin_ / "python3").symlink_to(sys.executable)
        for name, body in FAKES.items():
            if name in PACKAGE_MANAGER.values() and name != PACKAGE_MANAGER[distro]:
                continue
            (bin_ / name).write_text("#!/bin/sh\n" + body)
            (bin_ / name).chmod(0o755)
        env = {"PATH": str(bin_), "HOME": str(self.home), "KINO_SYSROOT": str(self.sys), "KINO_MEDIA": str(self.media),
               "FAKE_LOG": str(self.log), "FAKE_KINO_ACTIVE": "", "FAKE_NAME_KNOWN": "1", "FAKE_FIREWALL": "",
               # no Jellyfin answering, unless a test starts one: not waited for
               "KINO_LOCAL_JELLYFIN": "http://127.0.0.1:9", "KINO_JELLYFIN_WAIT": "0", **(env or {})}
        env.update({f"FAKE_{k.upper()}": v for k, v in fake.items()})
        return subprocess.run([str(bin_ / "sh"), str(SERVER_SH)], env=env, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)

    def calls(self, tool):
        return [line.split(" ", 1)[1] if " " in line else "" for line in self.log.read_text().splitlines()
                if line.split(" ", 1)[0] == tool]

    def share(self):
        return json.loads((self.home / ".config" / "kino" / "share.json").read_text())

    def test_runs_through_on_each_distribution(self):
        for distro, smb in (("arch", "smb"), ("debian", "smbd"), ("fedora", "smb"), ("opensuse", "smb")):
            with self.subTest(distro):
                self.log.write_text("")
                r = self.run_server_sh(distro)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("==> Done.", r.stdout)
                self.assertTrue(any("podman" in c and "samba" in c for c in self.calls(PACKAGE_MANAGER[distro])))
                self.assertIn(f"enable {smb}", self.calls("systemctl"))
                self.assertIn(f"restart {smb}", self.calls("systemctl"), "with Matinee's configuration")
                self.assertIn("start kino-jellyfin.service", self.calls("systemctl"))

    def test_arch_names_podmans_runtime(self):
        r = self.run_server_sh("arch")
        self.assertEqual(r.returncode, 0, "pacman would ask which runtime, and nobody answers: " + r.stdout + r.stderr)

    def test_share_is_the_media_folder_alone(self):
        smb_conf = self.sys / "etc" / "samba" / "smb.conf"
        smb_conf.parent.mkdir(parents=True)
        smb_conf.write_text("[global]\n[homes]\n  browseable = no\n")  # as Fedora's: every user's home
        self.assertEqual(self.run_server_sh("fedora").returncode, 0)
        conf = smb_conf.read_text()
        self.assertTrue(conf.startswith("# Matinee (deploy/server.sh)"))
        self.assertNotIn("[homes]", conf, "no home folder, with Matinee's sign-ins, on the network")
        self.assertIn(f"path = {self.media}", conf)
        self.assertIn(f"valid users = {self.share()['user']}", conf)
        self.assertIn("fruit:metadata = stream", conf, "a Mac's extras inside the files, no ._ files")
        self.assertIn("[homes]", (smb_conf.parent / "smb.conf.kino-backup").read_text(), "the one before kept")
        self.assertEqual(self.run_server_sh("fedora").returncode, 0)
        self.assertIn("[homes]", (smb_conf.parent / "smb.conf.kino-backup").read_text(), "not overwritten by Matinee's own")

    def test_share_password_written_for_the_tv_and_given_to_samba(self):
        r = self.run_server_sh("debian")
        share = self.share()
        self.assertRegex(share["password"], r"^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$")
        self.assertEqual(self.calls("smbpasswd"), [f"-s -a {share['user']} {share['password']} {share['password']} "])
        self.assertIn(share["password"], r.stdout)
        self.assertEqual((self.home / ".config" / "kino" / "share.json").stat().st_mode & 0o777, 0o600)

    def test_kinos_service_may_write_into_the_media_folder(self):
        r = self.run_server_sh("debian", kino_active="1")
        self.assertEqual(r.returncode, 0)
        conf = (self.sys / "etc" / "systemd" / "system" / "kino.service.d" / "media.conf").read_text()
        self.assertIn(f"ReadWritePaths=-{self.media}", conf)
        self.assertTrue(self.media.is_dir())
        self.assertEqual(self.calls("systemctl")[-1], "restart kino", "running: started again, to be let in")

    def test_kino_not_started_when_it_was_not_running(self):
        self.run_server_sh("debian")
        self.assertNotIn("restart kino", self.calls("systemctl"))

    def test_jellyfin_container_reads_the_media_and_gets_the_graphics_chip(self):
        container = self.sys / "etc" / "containers" / "systemd" / "kino-jellyfin.container"
        self.run_server_sh("arch")
        text = container.read_text()
        self.assertIn(f"Volume={self.media}:/media:ro", text)
        self.assertIn("Network=host", text, "port 8096 and the discovery on the home network")
        self.assertNotIn("AddDevice", text, "no graphics chip here")
        (self.sys / "dev" / "dri").mkdir(parents=True)
        self.run_server_sh("arch")
        self.assertIn("AddDevice=/dev/dri", container.read_text())

    def test_jellyfins_startup_wizard_done_before_anyone_else(self):
        jf = MockJellyfin().start()
        self.addCleanup(jf.stop)
        jf.fresh = True
        jf.stand_in = 2  # Jellyfin 12 starting: its stand-in's answer is not the server's
        r = self.run_server_sh("debian", env={"KINO_LOCAL_JELLYFIN": jf.url, "KINO_JELLYFIN_WAIT": "20"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(jf.fresh, "nobody else on the home network can be its first user now")
        kept = self.home / ".config" / "kino" / "local-admin.json"
        admin = json.loads(kept.read_text())
        self.assertEqual(kept.stat().st_mode & 0o777, 0o600)
        self.assertEqual(jf.accounts, {admin["user"]: admin["password"]}, "for the TV to take over")
        self.assertGreaterEqual(len(admin["password"]), 24)
        self.assertEqual([p for p, _ in jf.startup][-1], "/Startup/Complete")
        kept.unlink()
        self.assertEqual(self.run_server_sh("debian", env={"KINO_LOCAL_JELLYFIN": jf.url})
                         .returncode, 0)
        self.assertFalse(kept.exists(), "done before: nothing made up again")

    def test_jellyfin_not_answering_said_and_gone_on(self):
        r = self.run_server_sh("debian")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("startup wizard is still open", r.stderr)
        self.assertFalse((self.home / ".config" / "kino" / "local-admin.json").exists())

    def test_firewall_opened_where_firewalld_runs(self):
        r = self.run_server_sh("opensuse", firewall="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls("firewall-cmd")
        self.assertIn("--permanent --add-service=samba", calls)
        self.assertIn("--permanent --add-port=8096/tcp --add-port=7359/udp", calls, "Jellyfin and its discovery")
        self.assertEqual(calls[-1], "--reload")

    def test_firewall_left_alone_where_firewalld_does_not_run(self):
        self.assertEqual(self.run_server_sh("fedora").returncode, 0)
        self.assertEqual(self.calls("firewall-cmd"), [])

    def test_media_folder_marked_for_samba_under_selinux(self):
        self.run_server_sh("fedora")
        self.assertEqual(self.calls("chcon"), [], "no SELinux, nothing marked")
        (self.sys / "sys" / "fs" / "selinux").mkdir(parents=True)
        (self.sys / "sys" / "fs" / "selinux" / "enforce").write_text("1")
        self.run_server_sh("opensuse")  # openSUSE has no selinuxenabled: the kernel's own file tells
        self.assertEqual(self.calls("chcon"), [f"-R -t samba_share_t {self.media}"])

    def test_own_name_made_known_when_nothing_answers_for_it(self):
        hosts = self.sys / "etc" / "hosts"
        hosts.write_text("127.0.0.1 localhost\n")
        self.run_server_sh("opensuse")
        self.assertEqual(hosts.read_text(), "127.0.0.1 localhost\n", "known already: left alone")
        self.run_server_sh("opensuse", name_known="")
        name = subprocess.run(["uname", "-n"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(hosts.read_text(), f"127.0.0.1 localhost\n127.0.1.1 {name}\n",
                         "else Samba waits on the DNS server at each connection")


if __name__ == "__main__":
    unittest.main()
