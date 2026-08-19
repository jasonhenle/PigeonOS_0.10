"""Resolve GitHub Release download URLs for full Pigeon install packages."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

_DEFAULT_USER = "jasonhenle"
_DEFAULT_REPO = "PigeonOS_0.10"
_PI_TARBALL_RE = re.compile(r"^pigeon_[0-9]+\.[0-9]+\.[0-9]+_raspberry_pi\.tar\.gz$")


def _ascii_only(value: str) -> str:
    return "".join(ch for ch in value if 32 <= ord(ch) < 127)


def github_repo_slug() -> str:
    user = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_USER", _DEFAULT_USER).strip())
    repo = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_REPO", _DEFAULT_REPO).strip())
    return f"{user}/{repo}"


def github_releases_page_url() -> str:
    return f"https://github.com/{github_repo_slug()}/releases"


def _fetch_json(url: str, *, timeout_s: float = 20.0) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Pigeon/0.8 (github-release)",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None


def latest_pi_tarball_url(*, timeout_s: float = 20.0) -> tuple[str | None, str | None]:
    """
    Return ``(browser_download_url, version_string)`` for the latest Pi tarball release asset.

    Uses ``/releases/latest`` (ignores draft/pre-release).
    """
    slug = github_repo_slug()
    data = _fetch_json(f"https://api.github.com/repos/{slug}/releases/latest", timeout_s=timeout_s)
    if not isinstance(data, dict):
        return None, None
    tag = str(data.get("tag_name") or "").strip()
    version = tag.lstrip("vV") if tag else None
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if _PI_TARBALL_RE.match(name):
            url = str(asset.get("browser_download_url") or "").strip()
            if url:
                ver = name.replace("pigeon_", "").replace("_raspberry_pi.tar.gz", "")
                return url, ver or version
    return None, version


def release_pi_tarball_url(version: str, *, timeout_s: float = 20.0) -> str | None:
    """Return download URL for ``pigeon_<version>_raspberry_pi.tar.gz`` if that release exists."""
    ver = _ascii_only(version.strip().lstrip("vV"))
    if not ver:
        return None
    slug = github_repo_slug()
    data = _fetch_json(f"https://api.github.com/repos/{slug}/releases/tags/v{ver}", timeout_s=timeout_s)
    if not isinstance(data, dict):
        data = _fetch_json(f"https://api.github.com/repos/{slug}/releases/tags/{ver}", timeout_s=timeout_s)
    if not isinstance(data, dict):
        return None
    want = f"pigeon_{ver}_raspberry_pi.tar.gz"
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and str(asset.get("name") or "") == want:
            url = str(asset.get("browser_download_url") or "").strip()
            return url or None
    return None
