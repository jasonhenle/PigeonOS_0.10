"""Compare local Pigeon version against version.py on GitHub."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pigeon.version import version_string, version_tuple

_DEFAULT_GITHUB_USER = "jasonhenle"
_DEFAULT_GITHUB_REPO = "PigeonOS_0.10"
_DEFAULT_GITHUB_BRANCH = "main"
_DEFAULT_VERSION_PATHS: tuple[str, ...] = (
    "PigeonOS_0.10_Development/Pigeon/pigeonSystem/pigeon/version.py",
    "PigeonOS_0.9_Development/Pigeon/pigeonSystem/pigeon/version.py",
    "Pigeon_0.7.0_Development/Pigeon/pigeonSystem/pigeon/version.py",
    "Pigeon_0.70_Development/Pigeon/pigeonSystem/pigeon/version.py",
    "Pigeon/pigeonSystem/pigeon/version.py",
    "Pigeon_0.7.0/pigeonSystem/pigeon/version.py",
    "Pigeon_0.6.0_Development/Pigeon_0.6.0/pigeonSystem/pigeon/version.py",
    "Pigeon_0.6.0/pigeonSystem/pigeon/version.py",
    "pigeonSystem/pigeon/version.py",
)
_UA = "Pigeon/0.8 (update-check)"
_VERSION_FIELD_RE = re.compile(r"^(MAJOR|MINOR|PATCH)\s*=\s*(\d+)\s*$", re.MULTILINE)


def _ascii_only(value: str) -> str:
    """Keep printable ASCII only (HTTP URLs and slugs)."""
    return "".join(ch for ch in value if 32 <= ord(ch) < 127)


def _latin1_header(value: str) -> str:
    """HTTP/1.1 header values must be encodable as latin-1."""
    return value.encode("latin-1", errors="ignore").decode("latin-1")


def _latin1_safe_env() -> dict[str, str]:
    """Environment dict safe for subprocess (no U+202F etc. in values)."""
    out: dict[str, str] = {}
    for key, val in os.environ.items():
        ks = str(key)
        vs = str(val)
        try:
            ks.encode("latin-1")
            vs.encode("latin-1")
        except UnicodeEncodeError:
            ks = ks.encode("latin-1", errors="replace").decode("latin-1")
            vs = vs.encode("latin-1", errors="replace").decode("latin-1")
        out[ks] = vs
    return out


_GITHUB_ENV_KEYS = (
    "PIGEON_UPDATE_GITHUB_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
)
_PIGEON_PATH_ENV_KEYS = (
    "PIGEON_INSTALL_DIR",
    "PIGEON_UPDATE_GITHUB_REPO",
    "PIGEON_UPDATE_GITHUB_USER",
    "PIGEON_UPDATE_GITHUB_BRANCH",
    "PIGEON_UPDATE_VERSION_PATH",
    "PIGEON_UPDATE_GITHUB_RAW",
)


def prepare_github_update_environment() -> None:
    """
    Sanitize process environment before GitHub update HTTP/subprocess calls.

    Removes pasted narrow no-break spaces (U+202F) from tokens and paths — a common
    cause of ``latin-1`` errors in http.client on Raspberry Pi.
    """
    _scrub_github_token_file()
    _scrub_environ_to_ascii()
    if _github_repo_is_public() and os.environ.get("PIGEON_UPDATE_REQUIRE_TOKEN", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        for key in _GITHUB_ENV_KEYS:
            os.environ.pop(key, None)
        try:
            token_path = Path.home() / ".pigeon_0_6" / "github_update_token"
            token_path.unlink(missing_ok=True)
        except OSError:
            pass
    for key in _PIGEON_PATH_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            os.environ[key] = _ascii_only(str(val).strip())


def _scrub_environ_to_ascii() -> None:
    """Strip non-ASCII characters from all environment values (fixes U+202F in inherited env)."""
    for key in list(os.environ.keys()):
        val = os.environ.get(key)
        if val is None:
            continue
        clean = _ascii_only(str(val))
        if clean != val:
            if clean:
                os.environ[key] = clean
            else:
                os.environ.pop(key, None)


def safe_subprocess_path(path: Path | str) -> str:
    """Path string safe for subprocess argv on Pi (ASCII-only, no U+202F)."""
    return _ascii_only(str(Path(path).resolve()))


def _minimal_subprocess_env() -> dict[str, str]:
    """Small ASCII-safe env for curl/rsync/bootstrap (avoids polluted inherited env)."""
    keep = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "TZ",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    )
    out: dict[str, str] = {}
    for key in keep:
        val = os.environ.get(key)
        if val:
            out[key] = _ascii_only(str(val))
    if "PATH" not in out:
        out["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    if "HOME" not in out:
        try:
            out["HOME"] = _ascii_only(str(Path.home()))
        except RuntimeError:
            pass
    return out


def _scrub_github_token_file() -> None:
    """Rewrite token file as ASCII-only (fixes pasted narrow no-break spaces)."""
    try:
        token_path = Path.home() / ".pigeon_0_6" / "github_update_token"
        if not token_path.is_file():
            return
        cleaned = _sanitize_github_token(token_path.read_bytes())
        if cleaned:
            token_path.write_text(cleaned + "\n", encoding="ascii")
        else:
            token_path.unlink(missing_ok=True)
    except OSError:
        pass


def _sanitize_github_token(raw: str) -> str:
    """Strip BOM, whitespace (incl. U+202F), and non-ASCII from pasted tokens."""
    if not raw:
        return ""
    if isinstance(raw, bytes):
        return bytes(b for b in raw if 32 <= b < 127).decode("ascii")
    cleaned: list[str] = []
    for ch in raw.lstrip("\ufeff"):
        if ch.isspace():
            continue
        if ord(ch) < 128:
            cleaned.append(ch)
    return "".join(cleaned)


def _github_repo_name() -> str:
    return _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_REPO", _DEFAULT_GITHUB_REPO).strip())


def _github_repo_is_public() -> bool:
    return _github_repo_name() == "PigeonOS_0.10"


def github_token() -> str:
    """Token from env or ``~/.pigeon_0_6/github_update_token`` (skipped for public repo)."""
    if _github_repo_is_public() and os.environ.get("PIGEON_UPDATE_REQUIRE_TOKEN", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        return ""
    token = os.environ.get("PIGEON_UPDATE_GITHUB_TOKEN", "")
    if not token.strip():
        token = os.environ.get("GITHUB_TOKEN", "")
    if not token.strip():
        try:
            token_path = Path.home() / ".pigeon_0_6" / "github_update_token"
            if token_path.is_file():
                token = token_path.read_bytes()
        except OSError:
            token = b""
    return _sanitize_github_token(token if isinstance(token, bytes) else str(token))


def github_auth_headers(*, user_agent: str | None = None) -> dict[str, str]:
    """Optional token for private repos."""
    headers = {"User-Agent": _latin1_header(user_agent or _UA)}
    token = github_token()
    if token:
        headers["Authorization"] = _latin1_header(f"Bearer {token}")
    return headers


def _no_cache_headers() -> dict[str, str]:
    """Bypass intermediary / CDN caches when the user explicitly taps Update."""
    return {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


@dataclass(frozen=True)
class UpdateCheckResult:
    local_version: str
    remote_version: str | None
    update_available: bool
    error: str | None = None
    source_url: str | None = None
    github_branch: str | None = None


def github_repo_url() -> str:
    user = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_USER", _DEFAULT_GITHUB_USER).strip())
    repo = _github_repo_name()
    return f"https://github.com/{user}/{repo}"


def _curl_http_get(url: str, *, timeout_s: float, headers: dict[str, str]) -> bytes:
    import shutil
    import subprocess
    import tempfile

    curl = shutil.which("curl")
    if not curl:
        raise OSError("curl is required for GitHub downloads. Run: sudo apt install curl")
    safe_url = _ascii_only(url)
    cmd = [
        curl,
        "-fsSL",
        "--max-time",
        str(max(1, int(timeout_s))),
    ]
    header_path: str | None = None
    if headers:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="ascii",
                suffix=".pigeon-curl-headers",
                delete=False,
            ) as hf:
                for hk, hv in headers.items():
                    if hk and hv is not None:
                        hf.write(f"{_ascii_only(str(hk))}: {_ascii_only(str(hv))}\n")
                header_path = hf.name
            cmd.extend(["-H", f"@{header_path}"])
        except OSError as e:
            if header_path:
                try:
                    Path(header_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise OSError(f"Could not write curl headers: {e}") from e
    cmd.append(safe_url)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            env=_minimal_subprocess_env(),
        )
    except UnicodeEncodeError as e:
        raise OSError(f"subprocess encoding error (check install path for bad characters): {e}") from e
    finally:
        if header_path:
            try:
                Path(header_path).unlink(missing_ok=True)
            except OSError:
                pass
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
    raise OSError(err or f"curl exited {proc.returncode}")


def github_http_get(url: str, *, timeout_s: float, headers: dict[str, str] | None = None) -> bytes:
    """HTTPS GET via curl (never http.client — avoids latin-1 header crashes on Pi)."""
    safe_headers = {
        _ascii_only(str(k)): _ascii_only(str(v)) for k, v in (headers or {}).items()
    }
    safe_url = _ascii_only(url)
    return _curl_http_get(safe_url, timeout_s=timeout_s, headers=safe_headers)


def _branch_candidates() -> list[str]:
    out: list[str] = []
    env_branch = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_BRANCH", "").strip())
    if env_branch:
        out.append(env_branch)
    for b in (_DEFAULT_GITHUB_BRANCH, "main", "experiment", "0.8", "master"):
        if b and b not in out:
            out.append(b)
    return out


def _path_candidates() -> list[str]:
    env_path = _ascii_only(os.environ.get("PIGEON_UPDATE_VERSION_PATH", "").strip().lstrip("/"))
    if env_path:
        return [env_path]
    return list(_DEFAULT_VERSION_PATHS)


def version_py_raw_url(*, branch: str | None = None, path: str | None = None) -> str:
    override = os.environ.get("PIGEON_UPDATE_GITHUB_RAW", "").strip()
    if override:
        return _ascii_only(override)
    user = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_USER", _DEFAULT_GITHUB_USER).strip())
    repo = _github_repo_name()
    br = _ascii_only((branch or _branch_candidates()[0]).strip())
    rel = _ascii_only((path or _path_candidates()[0]).strip().lstrip("/"))
    return f"https://raw.githubusercontent.com/{user}/{repo}/{br}/{rel}"


def version_py_api_url(*, branch: str | None = None, path: str | None = None) -> str:
    from urllib.parse import quote

    user = _ascii_only(os.environ.get("PIGEON_UPDATE_GITHUB_USER", _DEFAULT_GITHUB_USER).strip())
    repo = _github_repo_name()
    br = _ascii_only((branch or _branch_candidates()[0]).strip())
    rel = _ascii_only((path or _path_candidates()[0]).strip().lstrip("/"))
    return f"https://api.github.com/repos/{user}/{repo}/contents/{quote(rel, safe='/')}?ref={br}"


def _private_repo_hint() -> str:
    user = os.environ.get("PIGEON_UPDATE_GITHUB_USER", _DEFAULT_GITHUB_USER).strip()
    repo = os.environ.get("PIGEON_UPDATE_GITHUB_REPO", _DEFAULT_GITHUB_REPO).strip()
    return (
        f"The GitHub repo ({user}/{repo}) is private.\n\n"
        "Create a one-line file on this Pi:\n"
        "  ~/.pigeon_0_6/github_update_token\n\n"
        "Put a GitHub personal access token with read access to that repo, "
        "then tap Updates again.\n\n"
        "Or reinstall from GitHub: bash installer/install_from_github.sh"
    )


def _linux_curl_get_simple(url: str, *, timeout_s: float) -> bytes | None:
    """Public raw.githubusercontent.com fetch without Authorization headers."""
    if not sys.platform.startswith("linux"):
        return None
    curl = shutil.which("curl")
    if not curl:
        return None
    try:
        proc = subprocess.run(
            [
                curl,
                "-fsSL",
                "--max-time",
                str(max(1, int(timeout_s))),
                _ascii_only(url),
            ],
            capture_output=True,
            check=False,
            env=_minimal_subprocess_env(),
        )
    except (OSError, UnicodeEncodeError):
        return None
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    return None


def _fetch_version_text(
    url: str,
    *,
    timeout_s: float,
    api: bool = False,
    force: bool = False,
) -> tuple[str | None, str | None]:
    headers = github_auth_headers()
    if force:
        headers.update(_no_cache_headers())
    if api:
        headers["Accept"] = _latin1_header("application/vnd.github.raw")
        headers["X-GitHub-Api-Version"] = _latin1_header("2022-11-28")
    try:
        body: bytes | None = None
        # Forced checks always use curl with no-cache headers (skip simple GET).
        if not force and not api and not github_token():
            body = _linux_curl_get_simple(url, timeout_s=timeout_s)
        if body is None:
            body = github_http_get(url, timeout_s=timeout_s, headers=headers)
        return body.decode("utf-8", errors="replace"), None
    except UnicodeEncodeError as e:
        return None, f"Encoding error talking to GitHub: {e}"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"Network error: {e.reason}"
    except OSError as e:
        return None, str(e)


def fetch_remote_version_tuple(
    *,
    timeout_s: float = 12.0,
    force: bool = False,
) -> tuple[tuple[int, int, int] | None, str | None, str | None, str | None]:
    """Return ``(remote_tuple, error_message, winning_raw_url, github_branch)``.

    When ``force`` is True (settings Update button), skip HTTP caches so a
    just-pushed main version is visible immediately.
    """
    token = github_token()
    saw_404 = False
    saw_auth_fail = False
    best: tuple[int, int, int] | None = None
    best_url: str | None = None
    best_branch: str | None = None
    bust = ""
    if force:
        import time as _time

        bust = f"pigeon_nocache={int(_time.time())}"
    for branch in _branch_candidates():
        for path in _path_candidates():
            attempts: list[tuple[str, bool]] = []
            if token:
                attempts.append((version_py_api_url(branch=branch, path=path), True))
            attempts.append((version_py_raw_url(branch=branch, path=path), False))
            for url, api in attempts:
                fetch_url = url
                if bust and not api:
                    fetch_url = f"{url}{'&' if '?' in url else '?'}{bust}"
                body, err = _fetch_version_text(
                    fetch_url,
                    timeout_s=timeout_s,
                    api=api,
                    force=force,
                )
                if body is None:
                    if err == "HTTP 404":
                        saw_404 = True
                    elif err in ("HTTP 401", "HTTP 403"):
                        saw_auth_fail = True
                    continue
                remote = parse_version_py(body)
                if remote is None:
                    continue
                if best is None or remote > best:
                    best = remote
                    best_url = url
                    best_branch = branch
    if best is not None:
        return best, None, best_url, best_branch
    if not token and saw_404:
        return None, _private_repo_hint(), version_py_raw_url(), None
    if saw_auth_fail:
        return (
            None,
            "GitHub rejected the update token (HTTP 401/403).\n\n"
            "Check ~/.pigeon_0_6/github_update_token — it needs read access to the repo.",
            version_py_raw_url(),
            None,
        )
    if saw_404:
        return (
            None,
            "version.py was not found on GitHub for branch "
            f"{_branch_candidates()[0]!r}. Push the latest code to main.",
            version_py_raw_url(),
            None,
        )
    return None, "Could not reach GitHub to check for updates.", version_py_raw_url(), None


def parse_version_py(text: str) -> tuple[int, int, int] | None:
    found: dict[str, int] = {}
    for m in _VERSION_FIELD_RE.finditer(text or ""):
        found[m.group(1)] = int(m.group(2))
    if not {"MAJOR", "MINOR", "PATCH"}.issubset(found.keys()):
        return None
    return (found["MAJOR"], found["MINOR"], found["PATCH"])


def format_version_tuple(t: tuple[int, int, int]) -> str:
    return f"{t[0]}.{t[1]}.{t[2]}"


def check_for_update(*, timeout_s: float = 12.0, force: bool = False) -> UpdateCheckResult:
    """Compare local version to GitHub.

    ``force=True`` (settings_pigeon Update button) bypasses HTTP caches so a
    newly merged main release is detected immediately.
    """
    try:
        from pigeon.source_toggles import source_enabled

        if not source_enabled("wifi"):
            return UpdateCheckResult(
                local_version=version_string(),
                remote_version=None,
                update_available=False,
                error="Pigeon is offline (Wi-Fi source is off).",
            )
    except ImportError:
        pass
    prepare_github_update_environment()
    local = version_string()
    local_t = version_tuple()
    remote_t, err, url, branch = fetch_remote_version_tuple(
        timeout_s=timeout_s,
        force=force,
    )
    if remote_t is None:
        return UpdateCheckResult(
            local_version=local,
            remote_version=None,
            update_available=False,
            error=err,
            source_url=url,
            github_branch=branch,
        )
    remote_s = format_version_tuple(remote_t)
    return UpdateCheckResult(
        local_version=local,
        remote_version=remote_s,
        update_available=remote_t > local_t,
        error=None,
        source_url=url,
        github_branch=branch,
    )
