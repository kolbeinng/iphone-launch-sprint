"""Shared helpers for the iPhone Launch Sprint kit (no purchase automation)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.yaml"
EXAMPLE_CONFIG = ROOT / "config.example.yaml"
# Dedicated profile for assist/probe (NOT your everyday Chrome profile).
# Override for parallel worktrees: ASSIST_PROFILE_DIR / ASSIST_CDP_PORT env vars.
_profile_env = (os.environ.get("ASSIST_PROFILE_DIR") or "").strip()
ASSIST_PROFILE_DIR = (
    Path(_profile_env) if _profile_env else ROOT / ".browser-profile-dynamic"
)
# Warm Chrome stays alive across runs — connect via CDP, never quit the app.
# Default 9223 so this worktree does not collide with practice kit on :9222.
ASSIST_CDP_PORT = int(os.environ.get("ASSIST_CDP_PORT") or "9223")
ASSIST_CDP_URL = f"http://127.0.0.1:{ASSIST_CDP_PORT}"
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


# Account page redirects to sign-in when logged out — better session probe than homepage.
DEFAULT_WARM_URL = "https://www.apple.com/vn/shop/goto/account"
DEFAULT_SESSION_URL = "https://www.apple.com/vn/shop/goto/account"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Exact Vietnamese labels on apple.com/vn buy flow (iPhone 17 page JSON).
DEFAULT_NO_TRADE_IN_LABEL = "Không đổi cũ lấy mới"
DEFAULT_NO_APPLECARE_LABEL = "Không có bảo hành AppleCare+"


class ConfigError(Exception):
    pass


def find_chrome_executable() -> Path:
    """Locate Google Chrome on Windows / macOS / Linux (env override first)."""
    env = (os.environ.get("CHROME_PATH") or "").strip()
    if env:
        p = Path(env)
        if p.exists():
            return p
        raise ConfigError(f"CHROME_PATH set but not found: {env}")

    candidates: list[Path] = []
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidates = [
            Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        which = shutil.which("chrome") or shutil.which("chrome.exe")
        if which:
            candidates.insert(0, Path(which))
    elif IS_MAC:
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            which = shutil.which(name)
            if which:
                candidates.append(Path(which))

    for p in candidates:
        if p and p.exists():
            return p
    raise ConfigError(
        "Google Chrome not found. Install Chrome, or set CHROME_PATH to chrome.exe.\n"
        r"  Windows typical: C:\Program Files\Google\Chrome\Application\chrome.exe"
    )


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _start_warm_chrome(user_data: str, port: int = ASSIST_CDP_PORT) -> None:
    """Start Google Chrome once with remote debugging — left running between scripts."""
    chrome = find_chrome_executable()
    log(f"Chrome binary: {chrome}")
    creationflags = 0
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if IS_WINDOWS:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survive parent exit
        creationflags = 0x00000008 | 0x00000200
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen(  # noqa: S603
        [
            str(chrome),
            f"--user-data-dir={user_data}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "about:blank",
        ],
        **popen_kwargs,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_open(port):
            return
        time.sleep(0.2)
    quit_hint = (
        "fully Quit Chrome (check tray) once, then re-run"
        if IS_WINDOWS
        else "Quit Chrome (⌘Q) once, then re-run"
    )
    raise ConfigError(
        f"Chrome did not open CDP on port {port}. "
        f"If an old assist Chrome is open without debugging, {quit_hint} — "
        "after that we keep it warm (no quit)."
    )


def launch_assist_browser(playwright, *, profile_dir: Path | None = None):
    """
    Attach to a long-lived Google Chrome via CDP.

    Returns (browser, context, page, meta) where meta["keep_alive"]=True means
    cleanup must only disconnect (browser.close on CDP) — NEVER quit Chrome.
    """
    user_data = str((profile_dir or ASSIST_PROFILE_DIR).resolve())
    Path(user_data).mkdir(parents=True, exist_ok=True)

    started = False
    if not _port_open(ASSIST_CDP_PORT):
        log(f"Starting warm Chrome (CDP :{ASSIST_CDP_PORT}) — will stay open between runs")
        _start_warm_chrome(user_data, ASSIST_CDP_PORT)
        started = True
    else:
        log(f"Reusing warm Chrome on CDP :{ASSIST_CDP_PORT} (no relaunch, no 2FA reset)")

    browser = playwright.chromium.connect_over_cdp(ASSIST_CDP_URL)
    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context(locale="vi-VN", viewport={"width": 1280, "height": 900})
    page = context.pages[0] if context.pages else context.new_page()
    meta = {
        "keep_alive": True,
        "cdp": ASSIST_CDP_URL,
        "started": started,
        "profile": user_data,
    }
    return browser, context, page, meta


def disconnect_assist_browser(browser, meta: dict | None = None) -> None:
    """Detach Playwright only — Chrome keeps running with cookies/SSO warm."""
    del meta  # reserved for future
    try:
        if browser:
            browser.close()  # CDP disconnect; does not quit Chrome
    except Exception:  # noqa: BLE001
        pass
    log("Disconnected from Chrome — browser LEFT OPEN (session warm).")


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG
    if not config_path.exists():
        raise ConfigError(
            f"Missing {config_path.name}. Copy the example first:\n"
            f"  cp {EXAMPLE_CONFIG.name} {DEFAULT_CONFIG.name}"
        )
    with config_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a mapping")
    return data


def require_dry_run(cfg: dict[str, Any]) -> None:
    if cfg.get("dry_run") is not True:
        raise ConfigError(
            "Refusing to run: dry_run must be true.\n"
            "This kit never places an order. Set dry_run: true in config.yaml."
        )


def encode_url(url: str) -> str:
    """Percent-encode non-ASCII path segments so urllib/open stay ASCII-safe."""
    parts = urlsplit(url.strip())
    path = quote(parts.path, safe="/%._-~,")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def validate_store_url(url: str, field: str) -> str:
    if not url or not isinstance(url, str):
        raise ConfigError(f"{field} is required")
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise ConfigError(f"{field} must be an http(s) URL")
    host = (parts.hostname or "").lower()
    allowed = {
        "www.apple.com",
        "apple.com",
        "secure.store.apple.com",
        "secure9.store.apple.com",
        "account.apple.com",
    }
    # allow secureN.store.apple.com
    if host not in allowed and not (
        host.startswith("secure") and host.endswith(".store.apple.com")
    ):
        raise ConfigError(f"{field} must point at apple.com (got {host or 'empty'})")
    path = parts.path or "/"
    if host.endswith("apple.com") and "/vn" not in path and "account.apple.com" not in host:
        # storefront paths should be VN; account.apple.com/vn is ok
        if not path.startswith("/vn") and "/vn/" not in path:
            raise ConfigError(f"{field} should be an Apple Vietnam URL (/vn/...)")
    return encode_url(raw)


def config_timezone(cfg: dict[str, Any]) -> ZoneInfo:
    name = cfg.get("timezone") or "Asia/Ho_Chi_Minh"
    try:
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"Invalid timezone: {name}") from exc


def parse_launch_at(cfg: dict[str, Any]) -> datetime:
    raw = cfg.get("launch_at")
    if not raw:
        raise ConfigError("launch_at is required (or pass --in / --now)")
    tz = config_timezone(cfg)
    text = str(raw).strip()
    if text.endswith("Z"):
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def parse_duration(text: str) -> timedelta:
    """Parse durations like 90s, 2m, 1h."""
    text = text.strip().lower()
    if text.isdigit():
        return timedelta(seconds=int(text))
    units = {"s": 1, "m": 60, "h": 3600}
    if len(text) >= 2 and text[-1] in units and text[:-1].isdigit():
        return timedelta(seconds=int(text[:-1]) * units[text[-1]])
    raise ConfigError(f"Invalid duration: {text} (use 90s, 2m, 1h)")


def now_in_tz(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def format_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def wait_until(target: datetime, tz: ZoneInfo, label: str = "T-0") -> None:
    """
    Sleep until target time (tz-aware). Logs countdown; wakes within ~50ms.
    Do NOT add artificial +5s — soft-404 poll handles 'not live yet'.
    """
    target = target.astimezone(tz)
    log(f"Waiting until {format_ts(target)} ({label})…")
    while True:
        now = now_in_tz(tz)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            log(f"{label} — GO (late by {-remaining * 1000:.0f}ms)" if remaining < -0.05 else f"{label} — GO")
            return
        if remaining > 30:
            log(f"  {label}: {remaining / 60:.1f} min left")
            time.sleep(min(10.0, remaining - 25))
        elif remaining > 5:
            log(f"  {label}: {remaining:.1f}s left")
            time.sleep(1.0)
        else:
            # Tight spin for the last seconds
            time.sleep(min(0.05, remaining))


def open_url(url: str, browser: str) -> None:
    """Open URL in the named browser (macOS) or default/Chrome (Windows)."""
    encoded = encode_url(url)
    if IS_WINDOWS:
        # Prefer Chrome if configured/named; else default handler.
        name = (browser or "").lower()
        if "chrome" in name:
            try:
                chrome = find_chrome_executable()
                subprocess.Popen(  # noqa: S603
                    [str(chrome), encoded],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x00000008 | 0x00000200,
                )
                return
            except ConfigError:
                pass
        os.startfile(encoded)  # type: ignore[attr-defined]
        return
    if IS_MAC:
        subprocess.run(["open", "-a", browser, encoded], check=False)  # noqa: S603
        return
    subprocess.run(["xdg-open", encoded], check=False)  # noqa: S603


def notify_macos(title: str, message: str) -> None:
    """Best-effort desktop notification (name kept for call-site compatibility)."""
    if IS_MAC:
        t = title.replace("\\", "\\\\").replace('"', '\\"')
        m = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{m}" with title "{t}"'
        subprocess.run(["osascript", "-e", script], check=False)  # noqa: S603
        return
    if IS_WINDOWS:
        # Console ping is enough; avoid Toast COM deps
        log(f"NOTIFY  {title}: {message[:160]}")
        return
    # Linux: try notify-send
    subprocess.run(  # noqa: S603
        ["notify-send", title, message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def beep() -> None:
    if IS_MAC:
        subprocess.run(["osascript", "-e", "beep"], check=False)  # noqa: S603
        return
    if IS_WINDOWS:
        try:
            import winsound  # type: ignore[import-untyped]

            winsound.MessageBeep(-1)
        except Exception:  # noqa: BLE001
            print("\a", end="", flush=True)
        return
    print("\a", end="", flush=True)


def log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def click_labels(cfg: dict[str, Any]) -> tuple[str, str]:
    clicks = cfg.get("manual_clicks") or {}
    if not isinstance(clicks, dict):
        clicks = {}
    no_trade = clicks.get("no_trade_in") or DEFAULT_NO_TRADE_IN_LABEL
    no_care = clicks.get("no_applecare") or DEFAULT_NO_APPLECARE_LABEL
    return str(no_trade), str(no_care)


def print_manual_clicks(cfg: dict[str, Any]) -> None:
    no_trade, no_care = click_labels(cfg)
    log("Deep link skips ONLY model/storage/color. Still click:")
    log(f"  1) Trade In → “{no_trade}”")
    log(f"  2) AppleCare → “{no_care}”")
    log("  3) YOU add to bag / checkout — script never pays")


def print_checklist_reminder() -> None:
    checklist = ROOT / "checklist.md"
    log("Prep reminder: same-browser Apple ID sign-in, saved ship/pay, 2FA nearby.")
    if checklist.exists():
        log(f"Full checklist: {checklist}")


def print_session_debug(browser: str) -> None:
    log(f"Session debug: launcher uses browser app “{browser}”.")
    log("Apple sign-in is per-browser. Safari ≠ Chrome ≠ assist profile.")
    log("If the account page asks you to Đăng nhập, you are NOT signed in here.")
    log("Fix: sign in on that page, stay signed in, re-run --check-session.")


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)
