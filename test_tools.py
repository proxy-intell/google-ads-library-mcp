"""Tool behaviour through a real MCP client session, with ScrapeCreators stubbed. Run: python test_tools.py"""
import asyncio
import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["PROXY_TELEMETRY"] = "0"
os.environ["SCRAPECREATORS_API_KEY"] = "test"

import requests
from mcp.shared.memory import create_connected_server_and_client_session as connect

from src import ad_history, telemetry
from src.services import media_cache_service, scrapecreators_service

telemetry.STATE_FILE = Path(tempfile.mkdtemp()) / "state.json"
ad_history.HISTORY_FILE = Path(tempfile.mkdtemp()) / "history.json"
media_cache_service.image_cache.get_cached_image = lambda url: None
media_cache_service.image_cache.cache_image = lambda **kw: None
scrapecreators_service.time.sleep = lambda s: None

import mcp_server

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
ADS = [{"advertiserId": "AR1", "creativeId": "CR1", "format": "text", "adUrl": "https://adstransparency.google.com/advertiser/AR1/creative/CR1", "firstShown": "2026-11-18", "lastShown": "2026-11-19"}]


class Resp:
    def __init__(self, status, payload=None, content=b"", content_type="application/json"):
        self.status_code, self._payload, self.content = status, payload, content
        self.headers = {"content-type": content_type}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


calls = []


def fake_get(url, headers=None, params=None, timeout=None):
    calls.append((url, dict(params or {})))
    if "advertisers/search" in url:
        if params["query"] == "Broken":
            return Resp(400, {})
        if params["query"] == "Purina":
            return Resp(200, {"advertisers": [{"name": "Nestle Purina", "advertiser_id": "AR9", "region": "MX"}], "websites": [{"domain": "purina.com.mx"}]})
        return Resp(200, {"advertisers": [], "websites": []})
    if "company/ads" in url:
        first = sum("company/ads" in u for u, _ in calls) == 1
        return Resp(503) if first else Resp(200, {"success": True, "ads": ADS, "cursor": None})
    return Resp(200, content=PNG, content_type="image/png")


requests.get = fake_get


async def main():
    async with connect(mcp_server.mcp._mcp_server) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert {"get_google_platform_id", "get_google_ads", "get_playbook"} <= tools, tools
        assert len((await c.list_prompts()).prompts) == 3

        r = await c.call_tool("get_playbook", {"job": "competitor watch"})
        assert r.content[0].text.startswith("PLAYBOOK: competitor_watch")

        # Platform ID: trailing country words are stripped; a failed lookup stays visible, not "not found".
        r = json.loads((await c.call_tool("get_google_platform_id", {"brand_name": ["Purina Mexico", "Broken"]})).content[0].text)
        assert r["results"]["Purina Mexico"]["searched"] == "Purina" and r["results"]["Purina Mexico"]["advertisers"][0]["advertiser_id"] == "AR9", r
        assert r["results"]["Broken"] == {"error": "400"} and r["success"], r

        # get_google_ads: a 503 is retried once; the first fetch seeds the diff baseline.
        r = json.loads((await c.call_tool("get_google_ads", {"domain": "purina.com.mx"})).content[0].text)
        assert r["success"] and len(r["ads"]) == 1, r
        assert "baseline saved" in r["changes"]["note"], r["changes"]
        assert sum("company/ads" in u for u, _ in calls) == 2

        # The image comes back as an image block the model can see.
        r = await c.call_tool("analyze_ad_image", {"media_url": "http://img"})
        assert [b.type for b in r.content] == ["text", "image"] and not r.isError

        # A YouTube URL goes to Gemini as-is: no download, no upload.
        sent = []
        class FakeModels:
            def generate_content(self, model, contents):
                sent.append(contents[0])
                return type("R", (), {"text": "scene 1"})()
        mcp_server.configure_gemini = lambda: type("C", (), {"models": FakeModels()})()
        before = len(calls)
        r = json.loads((await c.call_tool("analyze_ad_video", {"media_url": "https://www.youtube.com/watch?v=abc"})).content[0].text)
        assert r["success"] and r["analysis"]["raw_analysis"] == "scene 1", r
        assert sent[0].file_data.file_uri == "https://www.youtube.com/watch?v=abc" and len(calls) == before


asyncio.run(main())

# Diff: first fetch is a baseline, inside 24h keeps it, 24h+ later reports new and dropped.
t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
ads = lambda *ids: [{"ad_id": i, "start_date": "2026-10-01"} for i in ids]
assert "baseline saved" in ad_history.changes("k", ads("1", "2"), t0)["note"]
assert "under 24h" in ad_history.changes("k", ads("3"), t0 + timedelta(hours=2))["note"]
later = ad_history.changes("k", ads("2", "3"), t0 + timedelta(days=2))
assert later["new_since_last_fetch"] == ["3"] and later["dropped_since_last_fetch"] == 1 and later["since_last_fetch"] == "2026-11-01"
recent = [{"ad_id": "n", "start_date": "2026-11-05"}, {"ad_id": "o", "start_date": "2026-09-01"}, {"ad_id": "x", "start_date": None}]
assert ad_history.launched_recently(recent, t0 + timedelta(days=6)) == ["n"]

print("tool tests passed")
