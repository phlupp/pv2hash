"""Offline checks of the 0.9.5 -> release-ID transition."""
from __future__ import annotations
import asyncio
from unittest import TestCase
from unittest.mock import patch

from pv2hash.release_identity import parse_release_id
from pv2hash.runtime import AppState
from pv2hash.update_check import UpdateChecker


class DummyResponse:
    def __init__(self, data):
        self._data=data
    def raise_for_status(self): pass
    def json(self):return self._data


class DummyClient:
    def __init__(self, responses):
        self.responses=responses
        self.calls=[]
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def get(self, url):
        self.calls.append(url)
        return DummyResponse(self.responses[url])


class ReleaseUpdateCheckerTest(TestCase):
    def test_github_package_latest_after_bridge_fetches_manifest_and_versions(self):
        github="https://api.github.com/repos/phlupp/pv2hash/releases/latest"
        manifest="https://github.com/phlupp/pv2hash/releases/download/r2026.10.18.1/manifest.json"
        tag="r2026.10.18.1"
        client=DummyClient({
            github:{
                "tag_name":tag,"name":"PV2Hash Release","published_at":"2026-10-18T10:00:00Z",
                "body":"Neue Oberfläche","draft":False,"prerelease":False,
                "assets":[
                    {"name":"pv2hash-2026.10.18.1.tar.gz","size":42000},
                    {"name":"manifest.json","browser_download_url":manifest},
                    {"name":"SHA256SUMS","browser_download_url":"https://example.invalid/sums"},
                ],
            },
            manifest:{
                "tag":tag,"core_version":"0.9.5","gui_version":"2.0.1",
                "release_id":tag,
            },
        })
        checker=UpdateChecker(AppState(config={"system":{}}),current_version="0.9.5")
        checker.installed_release_id="v0.9.5"
        checker.current_order=parse_release_id("v0.9.5").order
        with patch("pv2hash.update_check.httpx.AsyncClient",return_value=client):
            data=asyncio.run(checker.refresh())
        self.assertEqual(data["status"],"update_available")
        self.assertEqual(data["installed_release_id"],"v0.9.5")
        self.assertEqual(data["release_id"],tag)
        self.assertEqual(data["release_core_version"],"0.9.5") # GUI-only upgrade
        self.assertEqual(data["release_gui_version"],"2.0.1")
        self.assertEqual(client.calls,[github,manifest])

    def test_legacy_latest_stays_compatible_with_old_checker(self):
        github="https://api.github.com/repos/phlupp/pv2hash/releases/latest"
        client=DummyClient({github:{
            "tag_name":"v0.9.5","name":"PV2Hash 0.9.5","draft":False,"prerelease":False,
            "assets":[
                {"name":"pv2hash-0.9.5.tar.gz","size":42000},
                {"name":"manifest.json","browser_download_url":"https://example.invalid/manifest"},
                {"name":"SHA256SUMS","browser_download_url":"https://example.invalid/sums"},
            ],
        }})
        checker=UpdateChecker(AppState(config={"system":{}}),current_version="0.9.4")
        checker.installed_release_id=None
        checker.current_order=parse_release_id("v0.9.4").order
        with patch("pv2hash.update_check.httpx.AsyncClient",return_value=client):
            data=asyncio.run(checker.refresh())
        self.assertEqual(data["status"],"update_available")
        self.assertEqual(data["release_id"],"v0.9.5")
        self.assertEqual(client.calls,[github])
