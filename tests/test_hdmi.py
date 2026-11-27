"""The TV's other inputs (backend/hdmi.py): switched to through a fake cec-ctl, and back."""
import json
import unittest
from pathlib import Path

from helpers import ROOT, Backend, temp_dir
from mockjf import MockJellyfin


def fake_cec(tmp, **state):
    tmp = Path(tmp)
    (tmp / "cec.json").write_text(json.dumps({"pa": "1.0.0.0", "claimed": False, "shown": "1.0.0.0", **state}))
    (tmp / "cec0").touch()  # the adapter's device
    return {"KINO_CEC_CTL": str(ROOT / "tests" / "fake-cec-ctl"), "KINO_CEC_DEV": str(tmp / "cec0"),
            "FAKE_CEC_STATE": str(tmp / "cec.json"), "FAKE_CEC_LOG": str(tmp / "cec.log")}


class HdmiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(temp_dir("kino-hdmi-"))
        self.mock = MockJellyfin().start()
        self.addCleanup(self.mock.stop)
        self.backend = Backend(self.mock.url, env=fake_cec(self.tmp)).start()
        self.addCleanup(self.backend.stop)

    def state(self):
        return json.loads((self.tmp / "cec.json").read_text())

    def test_inputs_kept(self):
        self.assertEqual(self.backend.request("/hdmi")[1], {"available": True, "inputs": []})
        code, r = self.backend.request("/hdmi/inputs", {"inputs": [{"name": " Fire TV ", "port": 2}, {"name": "Console", "port": 3}]})
        self.assertEqual(code, 200)
        self.assertEqual(r["inputs"], [{"name": "Fire TV", "port": 2}, {"name": "Console", "port": 3}])
        self.assertEqual(self.backend.request("/settings")[1]["hdmi"]["inputs"][0]["name"], "Fire TV", "the settings show them")
        for bad in ([{"name": "A", "port": 2}, {"name": "B", "port": 2}], [{"name": "", "port": 2}], [{"name": "A", "port": 0}],
                    [{"name": "A", "port": "2"}], [{"name": "x" * 31, "port": 2}], "no list"):
            self.assertEqual(self.backend.request("/hdmi/inputs", {"inputs": bad})[0], 400, bad)
        self.assertEqual(len(self.backend.request("/hdmi")[1]["inputs"]), 2, "kept as they were")
        self.assertEqual(json.loads((self.backend.conf / "config.json").read_text())["hdmi_inputs"][1]["port"], 3)

    def test_switched_there_and_back(self):
        self.backend.request("/hdmi/inputs", {"inputs": [{"name": "Fire TV", "port": 2}]})
        self.assertEqual(self.backend.request("/hdmi/switch", {"port": 3})[0], 400, "only an input set up")
        self.assertEqual(self.backend.request("/hdmi/switch", {"port": 2}), (200, {}))
        self.assertTrue(self.state()["claimed"], "the adapter set up as a player first")
        self.assertEqual(self.state()["shown"], "2.0.0.0")
        self.assertEqual(self.backend.request("/hdmi/back", {}), (200, {}))
        self.assertEqual(self.state()["shown"], "1.0.0.0", "Matinee shown again")
        self.assertIn('"--image-view-on"', (self.tmp / "cec.log").read_text(), "the TV woken for it")

    def test_kino_s_own_input_and_no_tv(self):
        self.backend.request("/hdmi/inputs", {"inputs": [{"name": "Matinee", "port": 1}, {"name": "Fire TV", "port": 2}]})
        self.assertEqual(self.backend.request("/hdmi/switch", {"port": 1}), (502, {"error": "hdmi_same_port"}))
        (self.tmp / "cec.json").write_text(json.dumps({"pa": "f.f.f.f", "claimed": True}))
        self.assertEqual(self.backend.request("/hdmi/switch", {"port": 2}), (502, {"error": "hdmi_no_tv"}))
        self.assertEqual(self.backend.request("/hdmi/back", {}), (502, {"error": "hdmi_no_tv"}))

    def test_none_without_an_adapter(self):
        b = Backend(self.mock.url, env={"KINO_CEC_CTL": str(ROOT / "tests" / "fake-cec-ctl"),
                                        "KINO_CEC_DEV": str(self.tmp / "none")}).start()
        try:
            self.assertFalse(b.request("/hdmi")[1]["available"])
            self.assertFalse(b.request("/settings")[1]["hdmi"]["available"])
        finally:
            b.stop()


if __name__ == "__main__":
    unittest.main()
