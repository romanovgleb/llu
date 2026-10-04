#!/usr/bin/env python3
"""Live LLM quota TUI — Codex / Cursor / GLM / Kimi Coding / DeepSeek / Groq STT."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
# Cloudflare (api2.cursor.sh) bans Python-urllib's default User-Agent (error 1010).
# Mirror a normal desktop browser so Dashboard RPCs and API-key exchange work.
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


def _cursor_token_api_key() -> str | None:
    """Exchange CURSOR_API_KEY → short-lived access JWT (Dashboard needs JWT)."""
    key = os.environ.get("CURSOR_API_KEY") or ""
    if not key:
        return None
    status, data = _http_json(
        "POST",
        f"{CURSOR_API}/auth/exchange_user_api_key",
        headers={**CURSOR_HTTP_HEADERS, "Authorization": f"Bearer {key}"},
        body={},
    )
    if status != 200 or not isinstance(data, dict):
        return None
    tok = data.get("accessToken")
    return str(tok) if tok else None


def _cursor_access_tokens() -> list[str]:
    """Candidate JWTs in preference order; duplicates dropped.

    Prefer agent Keychain (``cursor-user`` first), then auth.json, API-key
    exchange, IDE DB. Callers must try until one authenticates — a stale
    Keychain JWT often sits ahead of the live seat.
    """
    seen: set[str] = set()
    out: list[str] = []
    for tok in _cursor_tokens_keychain():
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    for getter in (
        _cursor_token_auth_json,
        _cursor_token_api_key,
        _cursor_token_ide_db,
    ):
        tok = getter()
        if not tok or tok in seen:
            continue
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


def _cursor_sand_usage(token: str) -> tuple[int, object]:
    """Grok Bot weekly allowance (dashboard «Grok Bot» bar)."""
    return _http_json(
        "POST",
        f"{CURSOR_API}/aiserver.v1.DashboardService/GetSandUsageStatus",
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
        # Stale JWT → try next source (keychain often lags IDE / API key).
        if status in (401, 403):
            continue
        break
    else:
        return [Snapshot("Cursor", False, error=f"http {status}")]

    if status != 200 or not isinstance(data, dict) or not token_ok:
        return [Snapshot("Cursor", False, error=f"http {status}")]

    usage = data.get("planUsage") or {}
    # Dashboard "Included in Pro" bars (settings UI no longer shows $):
    #   Cursor Models  = autoPercentUsed
    #   Other Models   = apiPercentUsed
    # Cents still come from the same RPC (totalSpend / includedSpend / bonusSpend).
    auto_pct = float(usage.get("autoPercentUsed") or 0)
    api_pct = float(usage.get("apiPercentUsed") or 0)
    total = float(usage.get("totalSpend") or 0)
    reset = _fmt_reset(data.get("billingCycleEnd"))
    rows = [
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

    # Separate weekly Grok Bot pool (sand-*). Best-effort — never drop plan bars.
    sand_st, sand = _cursor_sand_usage(token_ok)
    if sand_st == 200 and isinstance(sand, dict) and sand.get("hasNonZeroIncludedLimit"):
        period_start = sand.get("currentPeriodStart")
        # Weekly window: period start + 7d ≈ next reset (dashboard: "Resets weekly").
        grok_detail = "weekly"
        try:
            if isinstance(period_start, str) and period_start:
                start = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
                end_ms = int((start.timestamp() + 7 * 86400) * 1000)
                grok_detail = _fmt_reset(end_ms, idle="weekly") or "weekly"
        except (ValueError, OSError, OverflowError, TypeError):
            grok_detail = "weekly"
        rows.append(
            Snapshot(
                "Grok Bot",
                True,
                pct=float(sand.get("usagePercent") or 0),
                detail=grok_detail,
            )
        )
    return rows


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
    rows = [Snapshot("Codex 5h", True, pct=pct, detail=_codex_window_reset(window))]

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
                )
            )
    rows.append(_codex_banked_resets(data, headers=headers))
    return rows


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
    five = Snapshot("GLM 5h", True, pct=0.0, detail="idle")
    week = Snapshot("GLM wk", True, pct=0.0)
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
            )
        elif lim.get("unit") == 6:
            week = Snapshot(
                "GLM wk",
                True,
                pct=pct,
                detail=_glm_window_reset(lim),
            )
    return [five, week]


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
        ),
        Snapshot(
            "Groq day",
            True,
            pct=_pct(day_used, GROQ_FREE_ASD),
            detail=f"{day_used / 60.0:.0f}m / 8h",
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
        "Grok Bot",
        "GLM 5h",
        "GLM wk",
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
                }
                for r in frame.rows
            ],
        }
        print(json.dumps(out, indent=2))
        return 0 if all(r.ok for r in frame.rows) else 1

    console = Console(highlight=False)
    if args.once:
        return run_once(console)
    try:
        return run_live(console, args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
