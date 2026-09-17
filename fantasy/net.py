"""Tiny JSON-over-HTTP helper (stdlib only, so the GitHub Action needs no installs)."""

import gzip
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "fantasy-dashboard/1.0 (personal GitHub Pages site)"


class AuthError(Exception):
    """Raised when a site refuses access (private league, expired cookies)."""


def get_json(url, cookies=None, retries=3, timeout=60):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if cookies:
        headers["Cookie"] = "; ".join("%s=%s" % kv for kv in cookies.items())

    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("HTTP %d from %s" % (e.code, url.split("?")[0]))
            if e.code == 404:
                raise
            last_error = e
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_error = e
        time.sleep(2 ** attempt)
    raise last_error
