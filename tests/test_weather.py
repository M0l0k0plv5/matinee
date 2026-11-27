"""The weather under the screensaver's clock, against a fake Open-Meteo (its geocoding and its forecast)."""
import json
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import Backend
from mockjf import MockJellyfin


class FakeOpenMeteo:
    def __init__(self):
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
                fake.requests.append((u.path, q))
                if u.path == "/v1/search":
                    body = {"results": [{"name": "Testhausen", "latitude": 51.3, "longitude": 12.4, "admin1": "Sachsen", "country": "Deutschland"},
                                        {"name": "Testhausen", "latitude": 48.1, "longitude": 9.2, "country": "Deutschland"}]} \
                        if q.get("name") == "Testhausen" else {}
                elif u.path == "/v1/forecast":
                    body = {"current": {"temperature_2m": 12.4, "weather_code": 61, "is_day": 1},
                            "daily": {"temperature_2m_max": [15.6], "temperature_2m_min": [7.2]}}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class WeatherTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockJellyfin().start()
        self.meteo = FakeOpenMeteo()

    def tearDown(self):
        self.mock.stop()
        self.meteo.stop()

    def backend(self, place):
        env = {"KINO_OPEN_METEO": self.meteo.url, "KINO_GEOCODING": self.meteo.url}
        return Backend(self.mock.url, env=env, conf={"weather": place} if place else None).start()

    def test_weather_for_the_place(self):
        b = self.backend("Testhausen")
        try:
            self.assertEqual(b.request("/weather"), (200, {"place": "Testhausen", "temp": 12, "code": 61, "day": True, "hi": 16, "lo": 7}))
            b.request("/weather")
            self.assertEqual([p for p, _ in self.meteo.requests], ["/v1/search", "/v1/forecast"], "kept for a while")
            forecast = self.meteo.requests[1][1]
            self.assertEqual((forecast["latitude"], forecast["longitude"]), ("51.3", "12.4"), "only the place's coordinates go out")
            self.assertEqual(b.request("/settings")[1]["weather"], "Testhausen")
            b.request("/settings", {"saverweather": "off"})
            self.assertEqual(b.request("/weather"), (200, {}), "turned off in the settings")
        finally:
            b.stop()

    def test_place_chosen_in_the_settings(self):
        b = self.backend(None)
        try:
            code, r = b.request("/weather/places?q=Testhausen")
            self.assertEqual(code, 200)
            self.assertEqual([p["region"] for p in r["places"]], ["Sachsen, Deutschland", "Deutschland"])
            self.assertEqual(b.request("/settings", {"weather": r["places"][1]})[0], 200)
            self.assertEqual(b.request("/settings")[1]["weather"], "Testhausen")
            self.meteo.requests.clear()
            self.assertEqual(b.request("/weather")[1]["temp"], 12)
            self.assertEqual([(p, q.get("latitude")) for p, q in self.meteo.requests], [("/v1/forecast", "48.1")],
                             "the place chosen, without looking it up again")
            for bad in ({"name": "X", "lat": 99, "lon": 0}, {"name": "<b>", "lat": "x", "lon": 0}, "Testhausen", {"lat": 1, "lon": 1}):
                self.assertEqual(b.request("/settings", {"weather": bad})[0], 400, bad)
            self.assertEqual(b.request("/settings", {"weather": ""})[0], 200)
            self.assertEqual(b.request("/weather"), (200, {}), "no place any more")
        finally:
            b.stop()

    def test_no_place_no_weather(self):
        b = self.backend(None)
        try:
            self.assertEqual(b.request("/weather"), (200, {}))
            self.assertEqual(self.meteo.requests, [], "nothing asked")
        finally:
            b.stop()

    def test_unknown_place(self):
        b = self.backend("Nowhere")
        try:
            self.assertEqual(b.request("/weather"), (200, {}))
        finally:
            b.stop()


if __name__ == "__main__":
    unittest.main()
