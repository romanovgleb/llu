#!/usr/bin/env python3
"""Live LLM quota TUI — Codex / Cursor / GLM / Kimi Coding / DeepSeek / Groq STT."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.style import Style
from rich.text import Text

SECRETS = Path(os.environ.get("SKILLS_ROOT", Path.home() / ".agents" / "skills")) / ".secrets"
CURSOR_DB = (
    Path.home()
    / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
CURSOR_API = "https://api2.cursor.sh"
CODEX_AUTH = Path.home() / ".codex" / "auth.json"
CODEX_USAGE_API = "https://chatgpt.com/backend-api/wham/usage"
CODEX_RESET_CREDITS_API = (
    "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
)
# ChatGPT OAuth client id embedded in the Codex CLI binary.
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
# GLM Coding Plan reset cards (ZCode desktop inventory; same cards as z.ai Manage).
# Status is GET-only here. Redeem is POST …/use with
# {"idempotency_key": "<≤64 chars>", "reset_type": "FIVE_HOUR"|"WEEK"} —
# weekly also clears 5h without spending a 5h card. Never call /use from llu.
ZCODE_CREDENTIALS = Path.home() / ".zcode" / "v2" / "credentials.json"
ZCODE_RESET_STATUS_API = "https://zcode.z.ai/api/v1/coding-plan/reset/status"
# Sota often blackholes zcode.z.ai on Mac; jump via Tailscale hosts that reach it.
ZCODE_JUMP_HOSTS = tuple(
    h.strip()
    for h in os.environ.get("LLU_ZCODE_JUMP", "rog,edge").split(",")
    if h.strip()
)
# Cloudflare (api2.cursor.sh) bans Python-urllib's default User-Agent (error 1010).
# Mirror a normal desktop browser so Dashboard RPCs work.
CURSOR_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Origin": "https://cursor.com",
    "Referer": "https://cursor.com/settings",
    "Accept": "application/json",
}
TIMEOUT_S = 15
# Groq Whisper free tier (https://console.groq.com/docs/rate-limits)
GROQ_STT_USAGE = Path.home() / ".cache" / "llm-finance" / "groq_stt_usage.json"
ZAI_WEB_TOKEN_CACHE = Path.home() / ".cache" / "llm-finance" / "zai_web_token"
ZAI_BUSINESS_LOGIN = "https://api.z.ai/api/auth/z/login"
GROQ_FREE_ASH = 7200  # audio seconds per hour
GROQ_FREE_ASD = 28800  # audio seconds per day
GROQ_FREE_RPD = 2000
GROQ_FREE_RPM = 20
GROQ_FREE_UPLOAD_MB = 25
# Widget is 64×7. Columns must sum to 64 or the Cursor bar wraps and
# looks longer than the empty grey bars.
NAME_W = 10
LINE_W = 28
VAL_W = 7
DETAIL_W = 14
# name + sp + bar + 2 + value + 2 + detail = 64
ROW_W = NAME_W + 1 + LINE_W + 2 + VAL_W + 2 + DETAIL_W


@dataclass
class Snapshot:
    name: str
    ok: bool
    pct: float = 0.0
    detail: str = ""
    error: str = ""
    kind: str = "quota"  # quota | balance | spend
    amount: str = ""  # spend/balance: shown in the shared value column
    plan: str = ""  # subscription label for the widget group header


@dataclass
class Frame:
    rows: list[Snapshot] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now().astimezone())


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def load_keys() -> None:
    _load_dotenv(SECRETS / ".env")
    for name in ("deepseek.env", "kimi_code.env", "zai_glm.env"):
        _load_dotenv(SECRETS / name)


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    h = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw[:200]}
        return e.code, payload
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def _pct(used: float, limit: float) -> float:
    if limit <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * used / limit))


def _fmt_countdown_secs(secs: int, *, until: datetime | None = None) -> str:
    if secs < 0:
        return "due"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}h{m:02d}m"
    days = secs // 86400
    if until is None:
        until = datetime.now().astimezone() + timedelta(seconds=secs)
    return f"{days}d {until.strftime('%b')} {until.day}"


def _fmt_remaining_secs(secs: int) -> str:
    """Pure time-until countdown (no calendar date) — ZCode reset-card style."""
    if secs < 0:
        return "due"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}h{m:02d}m"
    days, rem = divmod(secs, 86400)
    hours = rem // 3600
    if hours:
        return f"{days}d{hours}h"
    return f"{days}d"


def _fmt_reset(iso_or_ms: str | int | float | None, *, idle: str = "") -> str:
    if iso_or_ms is None or iso_or_ms == "":
        return idle
    try:
        if isinstance(iso_or_ms, (int, float)) or (
            isinstance(iso_or_ms, str) and iso_or_ms.isdigit()
        ):
            ms = int(iso_or_ms)
            if ms > 10_000_000_000:
                ms //= 1000
            dt = datetime.fromtimestamp(ms, tz=timezone.utc).astimezone()
        else:
            s = str(iso_or_ms).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s).astimezone()
    except (ValueError, OSError, OverflowError):
        return idle
    now = datetime.now().astimezone()
    return _fmt_countdown_secs(int((dt - now).total_seconds()), until=dt)


def _codex_window_reset(window: dict) -> str:
    """Prefer Codex's own reset_after_seconds countdown; fall back to reset_at."""
    after = window.get("reset_after_seconds")
    if after is not None and after != "":
        try:
            secs = int(after)
        except (TypeError, ValueError):
            secs = None
        if secs is not None:
            until = None
            raw_at = window.get("reset_at")
            if raw_at is not None and raw_at != "":
                try:
                    ms = int(raw_at)
                    if ms > 10_000_000_000:
                        ms //= 1000
                    until = datetime.fromtimestamp(ms, tz=timezone.utc).astimezone()
                except (TypeError, ValueError, OSError, OverflowError):
                    until = None
            return _fmt_countdown_secs(secs, until=until)
    return _fmt_reset(window.get("reset_at"), idle="")


def _glm_window_reset(
    lim: dict,
    *,
    idle_if_empty: bool = False,
) -> str:
    """Prefer API nextResetTime; 5h window omits it while unused."""
    raw = lim.get("nextResetTime")
    if raw:
        return _fmt_reset(raw)
    # unit 3 / number 5 = rolling 5h — no pending expiry until spend starts
    if idle_if_empty and float(lim.get("percentage") or 0) <= 0:
        return "idle"
    unit = lim.get("unit")
    number = lim.get("number")
    if unit == 3 and number:  # hours
        return f"{int(number)}h win"
    if unit == 6 and number:  # days
        return f"{int(number)}d win"
    return ""


def _cursor_auth_json_paths() -> list[Path]:
    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    # Agent CLI: darwin ~/.cursor/auth.json; linux ~/.config/cursor/auth.json
    return [
        home / ".cursor" / "auth.json",
        xdg / "cursor" / "auth.json",
    ]


def _cursor_tokens_keychain() -> list[str]:
    """All macOS Keychain JWTs for Cursor agent login.

    After a seat switch, Keychain often keeps two ``cursor-access-token`` items:
    ``cursor-user`` (current ``agent login``) and ``cursor`` (stale). Bare
    ``security … -w`` returns the stale one first, so prefer ``cursor-user``.
    """
    if platform.system() != "Darwin":
        return []
    out: list[str] = []
    queries: list[list[str]] = [
        ["-s", "cursor-access-token", "-a", "cursor-user", "-w"],
        ["-s", "cursor-access-token", "-a", "cursor", "-w"],
        ["-s", "cursor-access-token", "-w"],
    ]
    for args in queries:
        try:
            raw = subprocess.check_output(
                ["security", "find-generic-password", *args],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        tok = raw.strip()
        if tok and tok not in out:
            out.append(tok)
    return out


def _cursor_token_auth_json() -> str | None:
    for path in _cursor_auth_json_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        tok = data.get("accessToken") if isinstance(data, dict) else None
        if tok:
            return str(tok)
    return None


def _cursor_token_ide_db() -> str | None:
    if not CURSOR_DB.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{CURSOR_DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'"
        ).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    return row[0].decode() if isinstance(row[0], bytes) else str(row[0])


def _cursor_access_tokens() -> list[str]:
    """Candidate JWTs in preference order; duplicates dropped.

    auth.json first — agent CLI live OAuth login. Then IDE DB. Keychain is
    last-resort only (phantom seats after switches). No CURSOR_API_KEY
    exchange — session-only policy since 2026-10-05.
    """
    seen: set[str] = set()
    out: list[str] = []
    for getter in (
        _cursor_token_auth_json,
        _cursor_token_ide_db,
    ):
        tok = getter()
        if not tok or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    for tok in _cursor_tokens_keychain():
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _cursor_period_usage(token: str) -> tuple[int, object]:
    return _http_json(
        "POST",
        f"{CURSOR_API}/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
        headers={
            **CURSOR_HTTP_HEADERS,
            "Authorization": f"Bearer {token}",
            "Connect-Protocol-Version": "1",
        },
        body={},
    )


def fetch_cursor() -> list[Snapshot]:
    tokens = _cursor_access_tokens()
    if not tokens:
        return [Snapshot("Cursor", False, error="not logged in")]

    status = 0
    data: object = {}
    token_ok: str | None = None
    for token in tokens:
        status, data = _cursor_period_usage(token)
        if status == 200 and isinstance(data, dict):
            token_ok = token
            break
        # Stale/dead seat → try next source. Their JWTs can flap 401/200,
        # so a one-off 200 is not proof of the live account — hence the
        # auth.json-first order in _cursor_access_tokens.
        if status in (401, 403):
            continue
        break
    else:
        return [Snapshot("Cursor", False, error=f"http {status}")]

    if status != 200 or not isinstance(data, dict) or not token_ok:
        return [Snapshot("Cursor", False, error=f"http {status}")]

    usage = data.get("planUsage") or {}
    # Bars mirror the dashboard's «Included in Pro» card (verified against
    # cursor.com/dashboard/spending 2026-10-04: Cursor Models showed 33% while
    # autoPercentUsed said 30.7 — same metric, still catching up):
    #   «Cursor»     = autoPercentUsed (Cursor Models / Grok / Composer)
    #   «Cursor API» = apiPercentUsed (Other Models)
    # Don't substitute includedSpend/limit or displayMessage: once the $20
    # plan pool is consumed both claim «usage limit» / 100% while the
    # dashboard bar keeps showing the per-bucket percent (~33%), and the
    # $138 of overage is provider-absorbed bonus (remainingBonus=false), not
    # on-demand spend. A $1/100% reading in Oct 2026 was a stale keychain
    # seat token, not this field — the 401 fallthrough below handles it.
    auto_pct = float(usage.get("autoPercentUsed") or 0)
    api_pct = float(usage.get("apiPercentUsed") or 0)
    total = float(usage.get("totalSpend") or 0)
    reset = _fmt_reset(data.get("billingCycleEnd"))
    return [
        Snapshot(
            "Cursor",
            True,
            pct=auto_pct,
            detail=reset,
            kind="spend",
            amount=f"${total / 100.0:.2f}",
        ),
        Snapshot(
            "Cursor API",
            True,
            pct=api_pct,
            detail=reset,
        ),
    ]


def _codex_banked_resets(
    usage: dict,
    *,
    headers: dict[str, str],
) -> Snapshot:
    """Banked usage-limit resets (Codex «Available N» credits)."""
    summary = usage.get("rate_limit_reset_credits") or {}
    try:
        n = int(summary.get("available_count") or 0)
    except (TypeError, ValueError):
        n = 0
    detail = "avail"
    if n > 0:
        status, payload = _http_json("GET", CODEX_RESET_CREDITS_API, headers=headers)
        if status == 200 and isinstance(payload, dict):
            soonest: datetime | None = None
            for credit in payload.get("credits") or []:
                if not isinstance(credit, dict):
                    continue
                if str(credit.get("status") or "").lower() not in ("", "available"):
                    continue
                raw = credit.get("expires_at")
                if not raw:
                    continue
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone()
                except (ValueError, OSError):
                    continue
                if soonest is None or dt < soonest:
                    soonest = dt
            if soonest is not None:
                detail = _fmt_countdown_secs(
                    int((soonest - datetime.now().astimezone()).total_seconds()),
                    until=soonest,
                )
    return Snapshot("Codex rst", True, kind="count", amount=str(n), detail=detail)


def _refresh_codex_auth(auth: dict) -> tuple[dict | None, str]:
    """Refresh Codex ChatGPT OAuth tokens and persist auth.json. Returns (auth, err)."""
    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    if not isinstance(tokens, dict):
        return None, "not logged in"
    refresh = tokens.get("refresh_token")
    if not refresh:
        return None, "no refresh token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": CODEX_OAUTH_CLIENT_ID,
        }
    ).encode()
    req = urllib.request.Request(
        CODEX_OAUTH_TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "codex-cli/0.153.4",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            err_body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            err_body = {}
        detail = err_body.get("error") or err_body.get("error_description") or raw[:80]
        return None, f"refresh http {e.code}: {detail}"
    except Exception as e:  # noqa: BLE001
        return None, f"refresh failed: {e}"

    if not isinstance(payload, dict) or not payload.get("access_token"):
        return None, "refresh bad response"
    new_tokens = dict(tokens)
    new_tokens["access_token"] = payload["access_token"]
    if payload.get("refresh_token"):
        new_tokens["refresh_token"] = payload["refresh_token"]
    if payload.get("id_token"):
        new_tokens["id_token"] = payload["id_token"]
    auth = dict(auth)
    auth["tokens"] = new_tokens
    auth["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        CODEX_AUTH.write_text(json.dumps(auth, indent=2) + "\n")
    except OSError as e:
        return None, f"auth write failed: {e}"
    return auth, ""


def fetch_codex() -> list[Snapshot]:
    """Read the signed-in ChatGPT account's Codex allowance via its OAuth token."""
    if not CODEX_AUTH.is_file():
        return [Snapshot("Codex", False, error="not logged in")]
    try:
        auth = json.loads(CODEX_AUTH.read_text())
    except (OSError, json.JSONDecodeError):
        return [Snapshot("Codex", False, error="bad auth file")]

    tokens = auth.get("tokens") if isinstance(auth, dict) else None
    if not isinstance(tokens, dict):
        return [Snapshot("Codex", False, error="not logged in")]
    token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not token or not account_id:
        return [Snapshot("Codex", False, error="not logged in")]

    def _headers(access: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access}",
            "ChatGPT-Account-Id": str(account_id),
            "User-Agent": "codex-cli/0.153.4",
        }

    headers = _headers(str(token))
    status, data = _http_json("GET", CODEX_USAGE_API, headers=headers)
    if status == 401:
        auth, err = _refresh_codex_auth(auth)
        if auth is None:
            return [Snapshot("Codex", False, error=err or "token expired")]
        tokens = auth.get("tokens") or {}
        token = tokens.get("access_token")
        if not token:
            return [Snapshot("Codex", False, error="refresh no access token")]
        headers = _headers(str(token))
        status, data = _http_json("GET", CODEX_USAGE_API, headers=headers)

    if status != 200 or not isinstance(data, dict):
        return [Snapshot("Codex", False, error=f"http {status}")]

    rate_limit = data.get("rate_limit") or {}
    window = rate_limit.get("primary_window") or {}
    used = window.get("used_percent")
    if used is None:
        return [Snapshot("Codex", False, error="no quota")]
    try:
        pct = float(used)
    except (TypeError, ValueError):
        return [Snapshot("Codex", False, error="bad quota")]

    # Detail = window countdown only (not plan_type — "plus 6d …" looked like expiry).
    plan = str(data.get("plan_type") or "").capitalize()
    rows = [
        Snapshot(
            "Codex 5h", True, pct=pct, detail=_codex_window_reset(window), plan=plan
        )
    ]

    weekly = rate_limit.get("secondary_window") or {}
    weekly_used = weekly.get("used_percent")
    if weekly_used is not None:
        try:
            weekly_pct = float(weekly_used)
        except (TypeError, ValueError):
            weekly_pct = None
        if weekly_pct is not None:
            rows.append(
                Snapshot(
                    "Codex wk",
                    True,
                    pct=weekly_pct,
                    detail=_codex_window_reset(weekly),
                    plan=plan,
                )
            )
    rows.append(_codex_banked_resets(data, headers=headers))
    return rows


def _zcode_credential_secret() -> str:
    env = os.environ.get("ZCODE_CREDENTIAL_SECRET")
    if env:
        return env
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    # Matches ZCode's Node os.platform() + os.homedir() + username fallback.
    return f"zcode-credential-fallback:{sys.platform}:{Path.home()}:{user}"


def _zcode_decrypt(blob: str, secret: str) -> str:
    """Decrypt ZCode `enc:v1:` AES-256-GCM credential values."""
    if not blob.startswith("enc:v1:"):
        return blob
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise RuntimeError("cryptography required for ZCode credentials") from e
    parts = blob[len("enc:v1:") :].split(".")
    if len(parts) != 3:
        raise ValueError("bad enc:v1 blob")
    pad = "=" * (-len(parts[0]) % 4)
    iv = base64.urlsafe_b64decode(parts[0] + pad)
    pad = "=" * (-len(parts[1]) % 4)
    tag = base64.urlsafe_b64decode(parts[1] + pad)
    pad = "=" * (-len(parts[2]) % 4)
    ct = base64.urlsafe_b64decode(parts[2] + pad)
    if len(iv) != 12 or len(tag) != 16:
        raise ValueError("bad enc:v1 iv/tag")
    key = hashlib.sha256(secret.encode()).digest()
    return AESGCM(key).decrypt(iv, ct + tag, None).decode()


def _zcode_coding_plan_tokens() -> tuple[str, str] | None:
    """Return (zcode_jwt, zai_business_token) from ~/.zcode/v2/credentials.json."""
    if not ZCODE_CREDENTIALS.is_file():
        return None
    try:
        data = json.loads(ZCODE_CREDENTIALS.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_jwt = data.get("zcodejwttoken")
    raw_zai = data.get("oauth:zai:access_token")
    if not isinstance(raw_jwt, str) or not isinstance(raw_zai, str):
        return None
    secret = _zcode_credential_secret()
    try:
        return _zcode_decrypt(raw_jwt, secret), _zcode_decrypt(raw_zai, secret)
    except Exception:
        return None


CHROMIUM_BROWSERS = (
    ("Brave Safe Storage", "Brave", "BraveSoftware/Brave-Browser"),
    ("Chrome Safe Storage", "Chrome", "Google/Chrome"),
)


def _chromium_cookie_key(service: str, user: str) -> bytes | None:
    try:
        proc = subprocess.run(
            [
                "/usr/bin/security",
                "-q",
                "find-generic-password",
                "-w",
                "-a",
                user,
                "-s",
                service,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    password = proc.stdout.strip()
    if proc.returncode != 0 or not password:
        return None
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        return None
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=16,
        salt=b"saltysalt",
        iterations=1003,
    )
    return kdf.derive(password.encode())


def _decrypt_chromium_cookie(encrypted: bytes, key: bytes) -> str | None:
    if not encrypted or encrypted[:3] not in (b"v10", b"v11"):
        return None
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None
    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
        raw = decryptor.update(encrypted[3:]) + decryptor.finalize()
        pad = raw[-1]
        if pad < 1 or pad > 16:
            return None
        # Chromium prepends a 32-byte SHA256 host digest to the value.
        return raw[:-pad][32:].decode()
    except Exception:  # noqa: BLE001
        return None


def _zai_web_tokens_from_browser() -> list[str]:
    """chat.z.ai session token from Brave/Chrome (same account as ZCode)."""
    out: list[str] = []
    for service, user, rel in CHROMIUM_BROWSERS:
        base = Path.home() / "Library/Application Support" / rel
        if not base.is_dir():
            continue
        key: bytes | None = None
        for profile in sorted(base.iterdir()):
            for relpath in ("Cookies", "Network/Cookies"):
                db_path = profile / relpath
                if not db_path.is_file():
                    continue
                if key is None:
                    key = _chromium_cookie_key(service, user)
                try:
                    with NamedTemporaryFile(suffix=".db") as tmp:
                        shutil.copy2(db_path, tmp.name)
                        con = sqlite3.connect(tmp.name)
                        try:
                            cur = con.execute(
                                "SELECT value, encrypted_value FROM cookies "
                                "WHERE host_key LIKE ? AND name = ? "
                                "ORDER BY expires_utc DESC",
                                ("%z.ai%", "token"),
                            )
                            for value, encrypted in cur:
                                token = value or None
                                if not token and encrypted and key:
                                    token = _decrypt_chromium_cookie(encrypted, key)
                                if token and token not in out:
                                    out.append(token)
                        finally:
                            con.close()
                except (OSError, sqlite3.Error):
                    continue
    return out


def _read_cached_zai_web_token() -> str | None:
    try:
        token = ZAI_WEB_TOKEN_CACHE.read_text().strip()
    except OSError:
        return None
    return token or None


def _cache_zai_web_token(token: str) -> None:
    try:
        ZAI_WEB_TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ZAI_WEB_TOKEN_CACHE.write_text(token)
        ZAI_WEB_TOKEN_CACHE.chmod(0o600)
    except OSError:
        pass


def _zai_business_token() -> str | None:
    """Exchange the chat.z.ai web session for a coding-plan business token.

    The ZCode desktop OAuth token in credentials.json is short-lived and goes
    stale within an hour of the last ZCode launch. The browser session outlives
    it, and `/api/auth/z/login` mints the token the reset endpoint wants.
    """
    candidates: list[str] = []
    cached = _read_cached_zai_web_token()
    if cached:
        candidates.append(cached)
    for token in _zai_web_tokens_from_browser():
        if token not in candidates:
            candidates.append(token)
    for web_token in candidates:
        status, data = _http_json(
            "POST",
            ZAI_BUSINESS_LOGIN,
            headers={"Content-Type": "application/json"},
            body={"token": web_token},
        )
        if status != 200 or not isinstance(data, dict):
            continue
        if data.get("code") not in (None, 0, 200):
            continue
        payload = data.get("data")
        if not isinstance(payload, dict):
            continue
        token = payload.get("access_token")
        if isinstance(token, str) and token:
            if web_token != cached:
                _cache_zai_web_token(web_token)
            return token
    return None


def _glm_reset_status_headers(zcode_jwt: str, zai_tok: str) -> dict[str, str]:
    auth = zcode_jwt if zcode_jwt.lower().startswith("bearer ") else f"Bearer {zcode_jwt}"
    return {
        "Authorization": auth,
        "X-Bigmodel-Authorization": zai_tok,
        "Bigmodel-Target-Type": "PERSONAL",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
    }


def _glm_reset_status_direct(zcode_jwt: str, zai_tok: str) -> tuple[int, object]:
    # Short timeout: Sota often blackholes zcode.z.ai; fail fast into jump hosts.
    h = _glm_reset_status_headers(zcode_jwt, zai_tok)
    req = urllib.request.Request(ZCODE_RESET_STATUS_API, headers=h, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw[:200]}
        return e.code, payload
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def _glm_reset_status_via_jump(
    zcode_jwt: str,
    zai_tok: str,
    host: str,
) -> tuple[int, object]:
    """GET reset status on `host` (stdin script — tokens not in argv)."""
    remote = f"""
import json, urllib.request, urllib.error, sys
zcode = {json.dumps(zcode_jwt)}
zai = {json.dumps(zai_tok)}
req = urllib.request.Request(
    {json.dumps(ZCODE_RESET_STATUS_API)},
    headers={{
        "Authorization": ("Bearer " + zcode) if not zcode.lower().startswith("bearer ") else zcode,
        "X-Bigmodel-Authorization": zai,
        "Bigmodel-Target-Type": "PERSONAL",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }},
    method="GET",
)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode()
        print(resp.status)
        print(raw)
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode(errors="replace"))
except Exception as e:
    print(0)
    print(json.dumps({{"error": type(e).__name__ + ":" + str(e)}}))
"""
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "StrictHostKeyChecking=accept-new",
                host,
                "python3",
                "-",
            ],
            input=remote,
            capture_output=True,
            text=True,
            timeout=35,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0, {"error": f"jump {host} failed"}
    if proc.returncode != 0 and not proc.stdout.strip():
        return 0, {"error": (proc.stderr or "jump failed")[:160]}
    lines = proc.stdout.splitlines()
    if not lines:
        return 0, {"error": "jump empty"}
    try:
        status = int(lines[0].strip())
    except ValueError:
        return 0, {"error": "jump bad status"}
    body_raw = "\n".join(lines[1:])
    try:
        payload = json.loads(body_raw) if body_raw else {}
    except json.JSONDecodeError:
        payload = {"error": body_raw[:200]}
    return status, payload


def _fetch_glm_reset_status(zcode_jwt: str, zai_tok: str) -> tuple[int, object]:
    status, data = _glm_reset_status_direct(zcode_jwt, zai_tok)
    if status == 200 and isinstance(data, dict):
        return status, data
    # Direct timeout / connect failure → Tailscale jump (Sota often blocks zcode.z.ai).
    if status not in (0, None) and status < 500 and status != 200:
        # Auth/application errors: don't bother jumping with the same tokens.
        if status in (401, 403):
            return status, data
    for host in ZCODE_JUMP_HOSTS:
        st, payload = _glm_reset_status_via_jump(zcode_jwt, zai_tok, host)
        if st == 200 and isinstance(payload, dict):
            return st, payload
    return status, data


def _glm_banked_resets() -> list[Snapshot]:
    """Banked GLM Coding Plan reset cards (5h + weekly), ZCode inventory.

    Missing or expired ZCode credentials are not an error state for the
    dashboard: the quota bars are live and the reset line simply drops out.
    """
    tokens = _zcode_coding_plan_tokens()
    if not tokens:
        return []
    zcode_jwt, zai_tok = tokens
    business = _zai_business_token()
    if business:
        zai_tok = business
    status, data = _fetch_glm_reset_status(zcode_jwt, zai_tok)
    if status != 200 or not isinstance(data, dict):
        return []
    # ZCode envelope: {code:0, data:{...}} ; tolerate unwrapped payloads.
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        return []
    if data.get("code") not in (None, 0, 200) and "available_five_hour_resets" not in payload:
        return []

    def _row(name: str, items: object) -> Snapshot:
        cards = items if isinstance(items, list) else []
        n = 0
        soonest: datetime | None = None
        for card in cards:
            if not isinstance(card, dict):
                continue
            raw = card.get("expire_at")
            try:
                ms = float(raw)
            except (TypeError, ValueError):
                continue
            # API uses epoch ms; ignore already-expired cards.
            try:
                dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).astimezone()
            except (OSError, OverflowError, ValueError):
                continue
            if dt <= datetime.now().astimezone():
                continue
            n += 1
            if soonest is None or dt < soonest:
                soonest = dt
        detail = "avail" if n else ""
        if soonest is not None:
            # Soonest unused card of this type (5h vs weekly). Remaining
            # countdown — not calendar date (easy to confuse with quota reset).
            detail = _fmt_remaining_secs(
                int((soonest - datetime.now().astimezone()).total_seconds())
            )
        return Snapshot(name, True, kind="count", amount=str(n), detail=detail)

    return [
        _row("GLM 5rst", payload.get("available_five_hour_resets")),
        _row("GLM wrst", payload.get("available_week_resets")),
    ]


def fetch_glm() -> list[Snapshot]:
    key = os.environ.get("ZAI_API_KEY") or os.environ.get("ZHIPU_API_KEY") or ""
    if not key:
        return [Snapshot("GLM", False, error="no ZAI_API_KEY")]
    status, data = _http_json(
        "GET",
        "https://api.z.ai/api/monitor/usage/quota/limit",
        headers={"Authorization": key},
    )
    if status != 200 or not isinstance(data, dict):
        return [Snapshot("GLM", False, error=f"http {status}")]
    if not data.get("success"):
        # An account without the Coding Plan returns HTTP 200 with code 500.
        # It is an account state, not a transport failure.
        message = str(data.get("msg") or "no coding plan")
        if data.get("code") == 500:
            message = "no coding plan"
        return [Snapshot("GLM", False, error=message)]
    payload = data.get("data") or {}
    plan = str(payload.get("level") or "").capitalize()
    five = Snapshot("GLM 5h", True, pct=0.0, detail="idle", plan=plan)
    week = Snapshot("GLM wk", True, pct=0.0, plan=plan)
    for lim in payload.get("limits") or []:
        # API returns CREDIT_LIMIT on coding plans; TOKENS_LIMIT is the legacy name.
        if lim.get("type") not in ("CREDIT_LIMIT", "TOKENS_LIMIT"):
            continue
        pct = float(lim.get("percentage") or 0)
        if lim.get("unit") == 3:
            five = Snapshot(
                "GLM 5h",
                True,
                pct=pct,
                detail=_glm_window_reset(lim, idle_if_empty=True),
                plan=plan,
            )
        elif lim.get("unit") == 6:
            week = Snapshot(
                "GLM wk",
                True,
                pct=pct,
                detail=_glm_window_reset(lim),
                plan=plan,
            )
    return [five, week, *_glm_banked_resets()]


def fetch_kimi() -> list[Snapshot]:
    key = (
        os.environ.get("KIMI_CODE_API_KEY")
        or os.environ.get("KIMI_API_KEY")
        or ""
    )
    if not key:
        return [Snapshot("Kimi", False, error="no KIMI_CODE_API_KEY")]
    status, data = _http_json(
        "GET",
        "https://api.kimi.com/coding/v1/usages",
        headers={"Authorization": f"Bearer {key}"},
    )
    if status == 429 and isinstance(data, dict) and data.get("code") == "resource_exhausted":
        return [Snapshot("Kimi", False, error="no credits")]
    if status != 200 or not isinstance(data, dict):
        return [Snapshot("Kimi", False, error=f"http {status}")]

    usage = data.get("usage") or {}
    week_limit = float(usage.get("limit") or 0)
    week_used = float(usage.get("used") or 0)
    if not week_used and week_limit:
        rem = usage.get("remaining")
        if rem is not None:
            week_used = week_limit - float(rem)
    week = Snapshot(
        "Kimi wk",
        True,
        pct=_pct(week_used, week_limit),
        detail=_fmt_reset(usage.get("resetTime")),
    )

    five = Snapshot("Kimi 5h", True, pct=0.0)
    for lim in data.get("limits") or []:
        window = lim.get("window") or {}
        detail = lim.get("detail") or {}
        if int(window.get("duration") or 0) != 300:
            continue
        limit = float(detail.get("limit") or 0)
        remaining = detail.get("remaining")
        used = detail.get("used")
        if used is not None:
            pct = _pct(float(used), limit)
        elif remaining is not None and limit:
            pct = _pct(limit - float(remaining), limit)
        else:
            pct = 0.0
        five = Snapshot(
            "Kimi 5h",
            True,
            pct=pct,
            detail=_fmt_reset(detail.get("resetTime")),
        )
        break
    return [five, week]


def fetch_deepseek() -> list[Snapshot]:
    key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        return [Snapshot("DeepSeek", False, error="no DEEPSEEK_API_KEY", kind="balance")]
    status, data = _http_json(
        "GET",
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}"},
    )
    if status != 200 or not isinstance(data, dict):
        return [Snapshot("DeepSeek", False, error=f"http {status}", kind="balance")]
    infos = data.get("balance_infos") or []
    pick = next((i for i in infos if i.get("currency") == "USD"), None) or (
        infos[0] if infos else None
    )
    if not pick:
        return [Snapshot("DeepSeek", False, error="no balance", kind="balance")]
    total = float(pick.get("total_balance") or 0)
    return [
        Snapshot(
            "DeepSeek",
            True,
            kind="balance",
            amount=f"${total:.2f}",
            plan="PAYG",
        )
    ]


def _groq_usage_buckets() -> tuple[float, float]:
    """Return (hour_seconds_used, day_seconds_used) from local ledger."""
    if not GROQ_STT_USAGE.is_file():
        return 0.0, 0.0
    try:
        data = json.loads(GROQ_STT_USAGE.read_text())
    except (OSError, json.JSONDecodeError):
        return 0.0, 0.0
    now = datetime.now(timezone.utc)
    hour_b = now.strftime("%Y-%m-%dT%H")
    day_b = now.strftime("%Y-%m-%d")
    hour_used = float(data.get("hour_seconds") or 0) if data.get("hour_bucket") == hour_b else 0.0
    day_used = float(data.get("day_seconds") or 0) if data.get("day_bucket") == day_b else 0.0
    return hour_used, day_used


def fetch_groq_stt() -> list[Snapshot]:
    """Groq Whisper free-tier caps; usage from local ledger (transcribe_long / STT)."""
    key = os.environ.get("GROQ_API_KEY") or ""
    if not key:
        return [
            Snapshot("Groq hr", False, error="no GROQ_API_KEY"),
            Snapshot("Groq day", False, error="no GROQ_API_KEY"),
        ]
    hour_used, day_used = _groq_usage_buckets()
    return [
        Snapshot(
            "Groq hr",
            True,
            pct=_pct(hour_used, GROQ_FREE_ASH),
            detail=f"{hour_used / 60.0:.0f}m / 2h",
            plan="Free",
        ),
        Snapshot(
            "Groq day",
            True,
            pct=_pct(day_used, GROQ_FREE_ASD),
            detail=f"{day_used / 60.0:.0f}m / 8h",
            plan="Free",
        ),
    ]


FETCHERS = (fetch_codex, fetch_cursor, fetch_glm, fetch_kimi, fetch_deepseek, fetch_groq_stt)


def collect() -> Frame:
    order = (
        "Codex 5h",
        "Codex wk",
        "Codex rst",
        "Codex",  # login / http / refresh errors (ok rows use Codex 5h/wk/rst)
        "Cursor",
        "Cursor API",
        "GLM 5h",
        "GLM wk",
        "GLM 5rst",
        "GLM wrst",
        "Kimi 5h",
        "Kimi wk",
        "DeepSeek",
        "Groq hr",
        "Groq day",
        "GLM",
        "Kimi",
    )
    by_name: dict[str, Snapshot] = {}
    with ThreadPoolExecutor(max_workers=len(FETCHERS)) as pool:
        futs = [pool.submit(fn) for fn in FETCHERS]
        for fut in as_completed(futs):
            for snap in fut.result():
                by_name[snap.name] = snap
    rows = [by_name[n] for n in order if n in by_name]
    return Frame(rows=rows)


def _fill_color(pct: float) -> str:
    if pct >= 85:
        return "#e06c75"
    if pct >= 60:
        return "#e5c07b"
    if pct >= 30:
        return "#d19a66"
    return "#98c379"


def render_line(
    pct: float,
    width: int = LINE_W,
    *,
    empty: str = "#3e4451",
) -> Text:
    """Continuous thin rule — used span tinted, remainder dim white."""
    width = max(4, width)
    filled = int(round(width * min(max(pct, 0.0), 100.0) / 100.0))
    filled = max(0, min(width, filled))
    color = _fill_color(pct) if filled else empty
    out = Text()
    out.append("─" * filled, style=Style(color=color))
    out.append("─" * (width - filled), style=Style(color=empty))
    return out


def _cell(text: str, width: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) > width:
        text = text[:width]
    return f"{text:<{width}}"


def _value_cell(snap: Snapshot) -> str:
    if not snap.ok:
        return _cell(snap.error or "error", VAL_W)
    if snap.kind == "count":
        return _cell(str(snap.amount or "0"), VAL_W)
    if snap.amount:
        n = float(snap.amount.lstrip("$").replace(",", ""))
        return _cell(f"${n:.2f}", VAL_W)
    return _cell(f"{snap.pct:.1f}%", VAL_W)


def render_row(snap: Snapshot) -> Text:
    line = Text()
    err = not snap.ok
    line.append(f"{snap.name:<{NAME_W}}", style="#e06c75" if err else "#abb2bf")
    line.append(" ")
    ghost = err or snap.kind in ("balance", "count")
    line.append_text(
        render_line(
            0.0 if ghost else snap.pct,
            empty="#252830" if ghost else "#3e4451",
        )
    )
    val_style = "#e06c75" if err else "#abb2bf"
    line.append(f"  {_value_cell(snap)}", style=val_style)
    line.append("  ")
    line.append(_cell(snap.detail, DETAIL_W), style="#5c6370")
    return line


def render_frame(frame: Frame) -> Group:
    return Group(*[render_row(s) for s in frame.rows])


def run_once(console: Console) -> int:
    frame = collect()
    console.print(render_frame(frame))
    # Some providers may legitimately be unavailable (for example a depleted
    # Kimi balance). The dashboard is still useful if it fetched any live row.
    return 0 if any(r.ok for r in frame.rows) else 1


def run_live(console: Console, interval: float) -> int:
    frame = collect()

    # Alternate screen so each refresh replaces the previous frame cleanly.
    with Live(
        render_frame(frame),
        console=console,
        refresh_per_second=2,
        screen=True,
        transient=False,
    ) as live:
        while True:
            time.sleep(interval)
            frame = collect()
            live.update(render_frame(frame))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="single fetch, no loop")
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        help="refresh seconds (default 60)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable dump")
    args = parser.parse_args(argv)

    load_keys()
    if args.json:
        frame = collect()
        out = {
            "fetched_at": frame.fetched_at.isoformat(),
            "providers": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "pct": r.pct,
                    "detail": r.detail,
                    "error": r.error,
                    "kind": r.kind,
                    "amount": r.amount,
                    "plan": r.plan,
                }
                for r in frame.rows
            ],
        }
        print(json.dumps(out, indent=2))
        # Match --once: one live provider is enough. The Mac widget pump used to
        # treat exit 1 as total failure and skip writing the snapshot, so a
        # single dead Codex session froze every other bar for days.
        return 0 if any(r.ok for r in frame.rows) else 1

    console = Console(highlight=False)
    if args.once:
        return run_once(console)
    try:
        return run_live(console, args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
