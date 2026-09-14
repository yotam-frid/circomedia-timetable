#!/usr/bin/env python3
"""Download timetables visible to the user's SharePoint session.

The first run opens a dedicated browser profile. Sign in to SharePoint there;
later runs reuse that session without requesting Microsoft Graph permissions.
"""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright


SITE = "https://circomedia.sharepoint.com/sites/Timetables-HEtimetables"
FOLDER = "/sites/Timetables-HEtimetables/Shared Documents"
LOGIN_URL = (
    "https://circomedia.sharepoint.com/:x:/r/sites/Timetables-HEtimetables/"
    "Shared%20Documents/Term%201a%20Week%201%202026.xlsx"
    "?d=w96932b5038cf4031badea5f6547f6764&csf=1&web=1&e=zumayo"
)
# Publishers use both "Week 1 2026" and "Weeks 2" naming conventions.
TIMETABLE_RE = re.compile(r"^Term .* Weeks? \d+(?: \d{4})?\.xlsx$", re.I)
PROFILE = Path(".sharepoint-browser-profile")
INCOMING = Path("incoming")


def api_url(path):
    return f"{SITE}/_api/web/GetFolderByServerRelativePath(decodedUrl=@folder)/Files?@folder='{quote(path)}'&$select=Name,ServerRelativeUrl,TimeLastModified"


def main():
    ap = argparse.ArgumentParser(description="Fetch SharePoint timetables")
    ap.add_argument("--login", action="store_true", help="open a browser and wait for sign-in")
    ap.add_argument("--headless", action="store_true", help="hide browser (only after sign-in works)")
    ap.add_argument("--all", action="store_true", help="download every matching timetable")
    args = ap.parse_args()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE.resolve()), headless=args.headless, accept_downloads=True
        )
        page = context.pages[0] if context.pages else context.new_page()
        if args.login:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            print("Sign in to SharePoint in the opened browser. Waiting up to 10 minutes...", flush=True)

        response = context.request.get(api_url(FOLDER), headers={"Accept": "application/json"})
        if args.login:
            for _ in range(120):
                if response.status == 200:
                    break
                time.sleep(5)
                response = context.request.get(api_url(FOLDER), headers={"Accept": "application/json"})
        if response.status != 200:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            context.close()
            sys.exit(
                f"SharePoint returned HTTP {response.status}. Sign in in the opened browser, "
                "then run this again with --login."
            )
        items = response.json().get("value", [])
        candidates = [f for f in items if TIMETABLE_RE.match(f.get("Name", ""))]
        if not candidates:
            context.close()
            sys.exit(f"No timetable matching {TIMETABLE_RE.pattern!r} in {FOLDER}")
        INCOMING.mkdir(exist_ok=True)
        selected = candidates if args.all else [max(candidates, key=lambda f: f["TimeLastModified"])]
        for item in sorted(selected, key=lambda f: f["Name"]):
            file_url = f"{SITE}/_api/web/GetFileByServerRelativePath(decodedUrl=@file)/$value?@file='{quote(item['ServerRelativeUrl'])}'"
            content = context.request.get(file_url)
            if content.status != 200:
                context.close()
                sys.exit(f"Download of {item['Name']} failed with HTTP {content.status}")
            out = INCOMING / item["Name"]
            out.write_bytes(content.body())
            print(f"Downloaded {item['Name']} (modified {item['TimeLastModified']}) -> {out}")
        context.close()


if __name__ == "__main__":
    main()
