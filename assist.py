#!/usr/bin/env python3
"""
Dry-run browser assist for Apple VN buy flow.

- Uses a dedicated persistent Chromium profile (sign in once, reuse)
- Waits for Apple ID sign-in instead of hanging on selectors
- Opens the product deep link
- Selects NO trade-in and NO AppleCare+
- Continues checkout: fulfillment → shipping → saved card + CVV → review
- STOPS at Đặt hàng / place-order (never clicks it)

Never places an order.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

from sprint_common import (
    ASSIST_PROFILE_DIR,
    DEFAULT_CONFIG,
    DEFAULT_SESSION_URL,
    ROOT,
    ConfigError,
    alert_attention,
    beep,
    click_labels,
    config_timezone,
    die,
    disconnect_assist_browser,
    format_ts,
    launch_assist_browser,
    load_config,
    log,
    notify_macos,
    now_in_tz,
    parse_duration,
    parse_launch_at,
    print_checklist_reminder,
    print_manual_clicks,
    require_dry_run,
    validate_store_url,
    wait_until,
)

PROFILE_DIR = ASSIST_PROFILE_DIR


class StageTimer:
    """Wall-clock stage timer for efficiency debugging (click vs wait split)."""

    _KIND_PREFIX = {
        "click": "CLICK",
        "wait": "WAIT",
        "nav": "NAV",
        "fill": "FILL",
        "poll": "POLL",
    }

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.marks: list[tuple[str, float, str]] = []
        self._last = self.t0

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def record(self, name: str, ms: float, *, kind: str = "") -> float:
        """Record an already-measured span and log it immediately."""
        self.marks.append((name, ms, kind))
        self._last = time.perf_counter()
        prefix = self._KIND_PREFIX.get(kind, "⏱")
        log(f"{prefix}  {name}: {ms:.0f}ms (total {self.elapsed_ms():.0f}ms)")
        return ms

    def mark(self, name: str, *, kind: str = "") -> float:
        """Record time since the previous mark/record."""
        now = time.perf_counter()
        delta_ms = (now - self._last) * 1000
        return self.record(name, delta_ms, kind=kind)

    def since(self, t_start: float, name: str, *, kind: str = "") -> float:
        return self.record(name, (time.perf_counter() - t_start) * 1000, kind=kind)

    @contextmanager
    def span(self, name: str, *, kind: str = ""):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.since(t, name, kind=kind)

    def report(self, title: str = "TIMER REPORT") -> None:
        total = (time.perf_counter() - self.t0) * 1000
        log("=" * 48)
        log(title)
        us_ms = 0.0
        apple_ms = 0.0
        for name, ms, kind in self.marks:
            prefix = self._KIND_PREFIX.get(kind, "⏱")
            bar = "█" * min(int(ms / 25), 40)
            log(f"  {prefix:5} {ms:7.0f}ms  {name}  {bar}")
            if kind in ("click", "fill"):
                us_ms += ms
            elif kind in ("wait", "nav", "poll"):
                apple_ms += ms
        log(f"        {total:7.0f}ms  TOTAL")
        log("WHO")
        us_pct = (us_ms / total * 100) if total else 0
        apple_pct = (apple_ms / total * 100) if total else 0
        log(
            f"  US     (CLICK/FILL)     {us_ms:7.0f}ms  {us_pct:5.1f}%  "
            "our clicks — if this is big, we are slow"
        )
        log(
            f"  APPLE  (WAIT/NAV/POLL)  {apple_ms:7.0f}ms  {apple_pct:5.1f}%  "
            "page load / checkout hop — we are idle"
        )
        if self.marks:
            slow = sorted(self.marks, key=lambda x: x[1], reverse=True)[:8]
            log("SLOWEST")
            for name, ms, kind in slow:
                pct = (ms / total * 100) if total else 0
                prefix = self._KIND_PREFIX.get(kind, "⏱")
                log(f"  {prefix:5} {ms:7.0f}ms  {pct:5.1f}%  {name}")
        log("=" * 48)


class CheckoutBlocked(RuntimeError):
    """Hop cannot succeed — Apple opened a blocking form (e.g. billing address)."""


def _wait_js_heartbeat(
    page,
    js: str,
    *,
    label: str,
    timeout_ms: int = 15_000,
    beat_ms: int = 1000,
    arg=None,
    snapshot_js: str | None = None,
    abort_js: str | None = None,
) -> float:
    """
    Poll a JS predicate with a 1s heartbeat.

    Apple checkout hops (Fulfillment→Shipping, etc.) sit on the same page for
    ~10s while graviton answers. A silent wait_for_function looks like a hang;
    this logs elapsed + _s= so you see immediately that *we* are idle on Apple.

    abort_js: if this becomes true *before* the success predicate, raise
    CheckoutBlocked immediately instead of burning the remaining timeout.
    """
    t0 = time.perf_counter()
    deadline = t0 + timeout_ms / 1000.0
    last_beat = 0.0
    log(f"WAIT  {label} — heartbeat {beat_ms}ms (cap {timeout_ms}ms)")
    while time.perf_counter() < deadline:
        try:
            ok = page.evaluate(js, arg) if arg is not None else page.evaluate(js)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            ms = (time.perf_counter() - t0) * 1000
            log(f"WAIT  {label} READY: {ms:.0f}ms")
            return ms
        if abort_js:
            try:
                blocked = bool(page.evaluate(abort_js))
            except Exception:  # noqa: BLE001
                blocked = False
            if blocked:
                ms = (time.perf_counter() - t0) * 1000
                log(f"WAIT  {label} BLOCKED: {ms:.0f}ms (billing address prompt)")
                raise CheckoutBlocked(
                    f"{label} blocked by billing address prompt after {ms:.0f}ms"
                )
        now = time.perf_counter()
        elapsed = (now - t0) * 1000
        if elapsed - last_beat >= beat_ms:
            snap = ""
            try:
                if snapshot_js:
                    snap = page.evaluate(snapshot_js)
                else:
                    snap = _checkout_step(page)
            except Exception:  # noqa: BLE001
                snap = "?"
            log(f"WAIT  {label} … {elapsed:.0f}ms  {snap}")
            last_beat = elapsed
        page.wait_for_timeout(40)
    raise TimeoutError(f"{label} timed out after {timeout_ms}ms")


_SNAP_CHECKOUT = """() => {
  const s = (location.search.match(/[?&]_s=([^&]+)/) || [])[1] || '';
  const newAddr = !!document.querySelector('input[data-autom="newAddress"]');
  const savedCard = !!document.querySelector('[data-autom="checkout-billingOptions-SAVED_CARD"]');
  const cvv = !!document.querySelector('[data-autom="security-code-input"]');
  const place = !!document.querySelector(
    '[data-autom="placeOrder"], [data-autom="place-order"], #rs-checkout-place-order-button'
  );
  const allDist = Array.from(document.querySelectorAll(
    'select[data-autom="form-field-district"]'
  ));
  const visSel = allDist.find((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  }) || allDist[allDist.length - 1] || allDist[0];
  const distN = visSel ? visSel.options.length : 0;
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const save = vis(document.querySelector('[data-autom="address-savebutton"]'));
  return '_s=' + s
    + ' newAddr=' + (newAddr ? 'Y' : 'N')
    + ' card=' + (savedCard ? 'Y' : 'N')
    + ' cvv=' + (cvv ? 'Y' : 'N')
    + ' place=' + (place ? 'Y' : 'N')
    + ' save=' + (save ? 'Y' : 'N')
    + ' phuongOpts=' + distN;
}"""


SIGNIN_HOST_HINTS = (
    "idmsa.apple.com",
    "appleid.apple.com",
    "secure.store.apple.com",
    "secure8.store.apple.com",
    "secure9.store.apple.com",
)
SIGNIN_PATH_HINTS = (
    "signin",
    "sign-in",
    "login",
    "sign_in",
    "authenticate",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Persistent-browser assist: wait for sign-in if needed, open product deep link, "
            "select no trade-in + no AppleCare. Never pays."
        )
    )
    p.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--setup-login",
        action="store_true",
        help="Only complete sign-in in the assist profile, then exit (recommended first run)",
    )
    p.add_argument(
        "--warm-only",
        action="store_true",
        help="Pre-launch: warm account + checkout SSO, then exit (leave Chrome open). Run this BEFORE T-0.",
    )
    p.add_argument(
        "--warmup-before-run",
        action="store_true",
        help="Warm checkout SSO then start timed run in one process (adds wall-clock before product — avoid at T-0)",
    )
    p.add_argument(
        "--skip-checkout-warm",
        action="store_true",
        help="Deprecated alias: timed runs skip warm by default; use --warm-only before launch",
    )
    p.add_argument(
        "--login-timeout-sec",
        type=int,
        default=600,
        help="Max seconds to wait while you sign in (default 600)",
    )
    p.add_argument(
        "--timeout-ms",
        type=int,
        default=45_000,
        help="Selector timeout in ms for trade-in / AppleCare clicks (default 45000)",
    )
    p.add_argument(
        "--unlock-timeout-sec",
        type=int,
        default=0,
        help="Max seconds to poll family page until configure unlocks (0 = config/default)",
    )
    p.add_argument(
        "--keep-open-sec",
        type=int,
        default=30,
        help="Seconds to pause on the final page before disconnecting (default 30; "
        "Chrome always stays open either way)",
    )
    timing = p.add_mutually_exclusive_group()
    timing.add_argument(
        "--now",
        action="store_true",
        help="Fire timed run immediately (default). Practice / resume.",
    )
    timing.add_argument(
        "--at-launch",
        action="store_true",
        help="Sleep until config launch_at, then sprint (use AFTER --warm-only on launch day)",
    )
    timing.add_argument(
        "--in",
        dest="in_duration",
        metavar="DURATION",
        help="Practice timer: wait DURATION then sprint (e.g. 30s, 2m)",
    )
    return p


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
    except ImportError:
        die(
            "Playwright not installed. Activate the venv, then run:\n"
            "  pip install -r requirements.txt"
        )
    return sync_playwright


def looks_like_signin(url: str, title: str = "") -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    blob = f"{host}{path}{title}".lower()
    if any(h in host for h in SIGNIN_HOST_HINTS) and any(p in path for p in SIGNIN_PATH_HINTS):
        return True
    if "idmsa.apple.com" in host or "appleid.apple.com" in host:
        return True
    if any(p in path for p in ("/signin", "/sign_in", "/login", "/shop/signin")):
        return True
    # Do NOT treat title "Đăng nhập" alone as sign-in — silent store SSO pages use it too
    if "sign in" in blob and "account" not in path and "ssi=" not in (parsed.query or "").lower():
        return "signin" in blob or "sign-in" in blob or "login" in blob
    return False


def is_silent_store_sso(url: str) -> bool:
    """True for secure*.store.apple.com/shop/signIn?ssi=… (auto hop, usually no 2FA UI)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    q = (parsed.query or "").lower()
    if "idmsa.apple.com" in host or "appleid.apple.com" in host:
        return False
    return "store.apple.com" in host and "signin" in path and "ssi=" in q


def is_interactive_signin(page) -> bool:
    """True when password / 2FA UI is actually shown."""
    try:
        url = page.url or ""
        if "idmsa.apple.com" in url or "appleid.apple.com" in url:
            return True
        return bool(
            page.evaluate(
                """() => {
                  const pwd = document.querySelector(
                    'input[type="password"], input[name="password"], #password_text_field'
                  );
                  if (pwd && (pwd.offsetParent !== null || pwd.getClientRects().length)) {
                    return true;
                  }
                  const text = (document.body && document.body.innerText) || '';
                  return /verification code|mã xác nhận|trusted phone|two-factor|hai yếu tố/i.test(text);
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def looks_signed_in(url: str, title: str = "") -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if looks_like_signin(url, title):
        return False
    if host in {"www.apple.com", "apple.com"} and (
        "/shop/account" in path
        or "/shop/goto/account" in path
        or path.endswith("/account")
        or "/shop/order/list" in path
        or "/shop/bag" in path
    ):
        return True
    # After successful login Apple often lands on account / storefront without signin host
    if host.endswith("apple.com") and "idmsa" not in host and "sign" not in path:
        if "/vn/" in path or path.startswith("/vn"):
            return True
    return False


def wait_for_signin(page, timeout_sec: int) -> bool:
    """Poll until sign-in page is gone / account looks available. Returns True if signed in.

    Checkout always routes through /shop/signIn?ssi=… (Apple step-up). That can finish
    silently in 1–3s when the profile is warm — poll fast so we don't inflate timers.
    """
    deadline = time.time() + max(timeout_sec, 30)
    notified = False
    last_log = 0.0
    t0 = time.perf_counter()
    while time.time() < deadline:
        url = page.url
        try:
            title = page.title()
        except Exception:  # noqa: BLE001
            title = ""

        if looks_like_signin(url, title):
            silent = is_silent_store_sso(url)
            interactive = (not silent) or is_interactive_signin(page)
            if not notified:
                if silent and not interactive:
                    log(
                        "Silent Apple store SSO hop (/signIn?ssi=) — waiting "
                        "(usually auto-completes; 2FA only if a form appears)."
                    )
                else:
                    log("Interactive sign-in detected — complete Apple ID + 2FA in Chrome.")
                notified = True
            now = time.time()
            if now - last_log >= 2.0:
                remaining = int(deadline - now)
                elapsed = (time.perf_counter() - t0) * 1000
                kind = "interactive 2FA" if interactive else "silent SSO"
                log(
                    f"Still on sign-in ({kind})… {remaining}s left "
                    f"(waited {elapsed:.0f}ms, host={urlparse(url).hostname})"
                )
                last_log = now
                if interactive and elapsed >= 4000:
                    notify_macos(
                        "Assist — sign in required",
                        "Finish Apple ID sign-in + 2FA in the Chromium window. Assist is waiting.",
                    )
            page.wait_for_timeout(200)
            continue

        if looks_signed_in(url, title):
            elapsed = (time.perf_counter() - t0) * 1000
            log(f"Signed-in looks good ({elapsed:.0f}ms): {url}")
            return True

        # Unknown intermediate page — keep waiting a bit, user may still be in 2FA
        now = time.time()
        if now - last_log >= 2.0:
            remaining = int(deadline - now)
            log(
                f"Waiting for account/session… {remaining}s left "
                f"({urlparse(url).hostname}{urlparse(url).path[:40]})"
            )
            last_log = now
        page.wait_for_timeout(250)

    return False


def _is_apple_404(page) -> bool:
    try:
        url = (page.url or "").lower()
        if "/shop/404" in url or url.rstrip("/").endswith("/404"):
            return True
        title = (page.title() or "").lower()
        if "page not found" in title or "không tìm thấy" in title:
            return True
        return bool(
            page.evaluate(
                """() => {
                  const h1 = (document.querySelector('h1') || {}).innerText || '';
                  return /can.?t be\\s*found|không tìm thấy/i.test(h1);
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _product_family_url(product_url: str) -> str:
    """Strip configured slug → /vn/shop/buy-iphone/iphone-17."""
    parts = urlsplit(product_url)
    segs = [s for s in parts.path.split("/") if s]
    if "buy-iphone" in segs:
        i = segs.index("buy-iphone")
        family = "/" + "/".join(segs[: i + 2])
        return urlunsplit((parts.scheme, parts.netloc, family, "", ""))
    # fallback: drop last path segment
    if len(segs) >= 2:
        family = "/" + "/".join(segs[:-1])
        return urlunsplit((parts.scheme, parts.netloc, family, "", ""))
    return product_url


def _norm_pref(s: str) -> str:
    """Normalize preference / label text for fuzzy match."""
    s = (s or "").strip().lower()
    # VN UI often uses comma decimals; autom uses underscores
    s = s.replace(",", ".").replace("_", ".")
    s = re.sub(r"\s+", "", s)
    return s


def _pref_regex(pref: str) -> re.Pattern[str] | None:
    """
    Optional regex prefs for weird launch-day color names.
      re:cam|orange|cosmic
      /cam.?v[uũ].*tr[uụ]|cosmicorange/i
    """
    raw = (pref or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("re:"):
        body = raw[3:]
        if not body:
            return None
        try:
            return re.compile(body, re.I | re.UNICODE)
        except re.error as exc:
            log(f"WARNING: bad color regex {raw!r}: {exc}")
            return None
    if len(raw) >= 3 and raw.startswith("/") and raw.count("/") >= 2:
        end = raw.rfind("/")
        if end > 0:
            body, flags_s = raw[1:end], raw[end + 1 :]
            flags = re.UNICODE
            if "i" in flags_s.lower() or not flags_s:
                flags |= re.I
            try:
                return re.compile(body, flags)
            except re.error as exc:
                log(f"WARNING: bad color regex {raw!r}: {exc}")
                return None
    return None


def _pref_matches(option: dict, pref: str) -> bool:
    """True if preference matches autom / value / label of a dimension option."""
    raw_haystacks = [
        option.get("autom") or "",
        option.get("value") or "",
        option.get("label") or "",
    ]
    # Regex prefs (re:… or /…/i) — match against original + normalized text
    rx = _pref_regex(pref)
    if rx is not None:
        for h in raw_haystacks:
            if not h:
                continue
            if rx.search(h) or rx.search(_norm_pref(h)):
                return True
        return False

    want = _norm_pref(pref)
    if not want:
        return False
    haystacks = [_norm_pref(h) for h in raw_haystacks]
    for h in haystacks:
        if not h:
            continue
        if want == h or want in h or h in want:
            return True
        # 256 vs 256gb
        if want.rstrip("gb") and want.rstrip("gb") == h.rstrip("gb"):
            return True
        # 6.3 vs dimensionScreensize6.3inch (dots already normalized)
        if len(want) >= 2 and want.replace(".", "") in h.replace(".", ""):
            return True
    return False


def _format_dimension_options(opts: list[dict]) -> str:
    bits = []
    for o in opts:
        bits.append(
            f"{o.get('autom') or o.get('value') or '?'}="
            f"{(o.get('label') or '')[:40]!r}"
            f"{'(disabled)' if o.get('disabled') else ''}"
        )
    return ", ".join(bits) if bits else "(none)"


def _dimension_pretty(dimension_name: str) -> str:
    return {
        "dimensionScreensize": "SIZE",
        "dimensionColor": "COLOR",
        "dimensionCapacity": "STORAGE",
    }.get(dimension_name, dimension_name)


def _checked_dimension_key(opts: list[dict]) -> str:
    for o in opts:
        if o.get("checked"):
            return str(o.get("autom") or o.get("value") or "")
    return ""


def _scroll_dimension_into_view(page, dimension_name: str) -> None:
    """Bring the size/color/storage radios into the viewport for a manual click."""
    try:
        page.evaluate(
            """(name) => {
              const el = document.querySelector(
                'input[type="radio"][name="' + name + '"]:not([disabled])'
              ) || document.querySelector('input[type="radio"][name="' + name + '"]');
              if (!el) return false;
              const anchor = el.closest(
                'fieldset, .rf-form-selector, .form-selector, [role="radiogroup"], section, form'
              ) || el;
              anchor.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' });
              return true;
            }""",
            dimension_name,
        )
        # Sticky header can cover the top of the section — nudge a bit
        page.evaluate("() => window.scrollBy(0, -80)")
        log(f"USER PICK  scrolled to {_dimension_pretty(dimension_name)} section")
    except Exception as exc:  # noqa: BLE001
        log(f"USER PICK  scroll failed (continuing): {exc}")


def _wait_user_dimension_pick(
    page,
    dimension_name: str,
    *,
    baseline_key: str = "",
    timeout_sec: float = 20.0,
    poll_ms: int = 50,
    asked: list[str] | None = None,
) -> dict:
    """
    Fast handoff: user clicks the radio in Chrome; we continue within ~poll_ms.
    Detects any newly checked option (or first check if none was checked).
    """
    pretty = _dimension_pretty(dimension_name)
    _scroll_dimension_into_view(page, dimension_name)
    available = _format_dimension_options(_list_dimension_options(page, dimension_name))
    log(
        f"USER PICK  click {pretty} in Chrome NOW "
        f"(poll={poll_ms}ms, timeout={timeout_sec:.0f}s) — available=[{available}]"
    )
    alert_attention()
    asked_s = ", ".join(str(x) for x in (asked or []) if str(x).strip())
    notify_macos(
        f"Assist — click {pretty} in Chrome NOW",
        (
            (f"{asked_s} is not on the page. " if asked_s else "No auto-match. ")
            + f"Click {pretty}. Have: {available[:140]}"
        ),
    )

    t0 = time.perf_counter()
    deadline = t0 + max(3.0, timeout_sec)
    last_log = 0.0
    while time.perf_counter() < deadline:
        opts = _list_dimension_options(page, dimension_name)
        key = _checked_dimension_key(opts)
        # Continue as soon as user has a selection that differs from baseline,
        # or any selection if nothing was checked before.
        if key and (not baseline_key or key != baseline_key):
            chosen = next(
                (o for o in opts if (o.get("autom") or o.get("value")) == key),
                None,
            )
            if chosen:
                ms = (time.perf_counter() - t0) * 1000
                log(
                    f"USER PICK  {pretty} detected in {ms:.0f}ms → "
                    f"{chosen.get('autom') or chosen.get('value')} "
                    f"({(chosen.get('label') or '')[:40]})"
                )
                return chosen
        # If baseline was already wrong/matched-none but user re-clicks same,
        # also accept a checked option after a tiny grace if prefs were empty?
        # Keep strict: require change from baseline when baseline set.
        now = time.perf_counter()
        if now - last_log >= 2.0:
            log(
                f"USER PICK  waiting for {pretty}… "
                f"{(now - t0):.1f}s / {timeout_sec:.0f}s"
            )
            last_log = now
        page.wait_for_timeout(poll_ms)

    raise RuntimeError(
        f"Timed out waiting for user to click {pretty} ({timeout_sec:.0f}s). "
        f"Available: [{available}]"
    )


def _list_dimension_options(page, dimension_name: str) -> list[dict]:
    """Read radio options for dimensionColor / dimensionCapacity / dimensionScreensize."""
    return page.evaluate(
        """(name) => {
          const inputs = Array.from(
            document.querySelectorAll('input[type="radio"][name="' + name + '"]')
          );
          return inputs.map((el) => {
            const lab = el.id
              ? document.querySelector('label[for="' + el.id + '"]')
              : null;
            const labelEl = lab || el.closest('label');
            const label = ((labelEl && labelEl.innerText) || '')
              .replace(/\\s+/g, ' ').trim();
            const er = el.getBoundingClientRect();
            const lr = labelEl ? labelEl.getBoundingClientRect() : null;
            const visible = (er.width > 1 && er.height > 1)
              || !!(lr && lr.width > 1 && lr.height > 1);
            return {
              autom: el.getAttribute('data-autom') || '',
              value: el.value || '',
              checked: !!el.checked,
              disabled: !!el.disabled,
              visible,
              label: label.slice(0, 120),
            };
          });
        }""",
        dimension_name,
    )


def _select_dimension_by_prefs(
    page,
    dimension_name: str,
    prefs: list[str],
    *,
    timer: StageTimer | None = None,
    mark: str = "",
    timeout_ms: int = 12_000,
    allow_failover: bool = False,
    on_miss: str = "wait_user",
    user_pick_timeout_sec: float = 20.0,
) -> str:
    """
    Pick first enabled option matching ordered prefs (substring or re:/… regex).
    Returns selected autom/value.

    on_miss when nothing matches:
      wait_user — beep + poll ~50ms for your click in Chrome (default, fast)
      stop — raise
      failover — first enabled option (dangerous on launch day)
    """
    t0 = time.perf_counter()
    deadline = t0 + timeout_ms / 1000
    last_opts: list[dict] = []
    miss_mode = (on_miss or "wait_user").strip().lower()
    if allow_failover:
        miss_mode = "failover"

    while time.perf_counter() < deadline:
        opts = _list_dimension_options(page, dimension_name)
        last_opts = opts
        enabled = [o for o in opts if not o.get("disabled")]
        if not enabled:
            page.wait_for_timeout(120)
            continue
        for o in enabled:
            if o.get("checked") and any(_pref_matches(o, p) for p in prefs):
                if _bfe_dimension_committed(page, dimension_name):
                    picked = o.get("autom") or o.get("value") or "?"
                    wall_ms = (time.perf_counter() - t0) * 1000
                    log(
                        f"Dimension {dimension_name} already selected: {picked} "
                        f"({wall_ms:.0f}ms) — BFE committed, skip click"
                    )
                    if timer and mark:
                        timer.record(mark, wall_ms, kind="click")
                    return str(picked)
                log(
                    f"Dimension {dimension_name} looks checked "
                    f"({o.get('autom')}) but Apple has not committed it — "
                    "one pointer click"
                )
                break
        chosen = None
        matched_pref = None
        user_picked = False
        for pref in prefs:
            for o in enabled:
                if _pref_matches(o, pref):
                    chosen = o
                    matched_pref = pref
                    break
            if chosen:
                break
        if not chosen:
            available = _format_dimension_options(opts)
            log(
                f"No pref matched for {dimension_name}; prefs={prefs!r}; "
                f"available=[{available}]"
            )
            if len(enabled) == 1:
                # Nothing to click: Apple only listed one tile (sold-out
                # capacities disappear). Waiting for a user pick can never
                # succeed if that tile is already checked.
                chosen = enabled[0]
                log(
                    f"ONLY ONE { _dimension_pretty(dimension_name) } option — "
                    f"taking {chosen.get('autom') or chosen.get('value')} "
                    f"({(chosen.get('label') or '')[:40]})"
                )
            elif miss_mode == "failover":
                chosen = enabled[0]
                log(
                    f"WARNING: failover → "
                    f"{chosen.get('autom') or chosen.get('value')} "
                    f"({(chosen.get('label') or '')[:40]})"
                )
            elif miss_mode == "wait_user":
                baseline = _checked_dimension_key(opts)
                chosen = _wait_user_dimension_pick(
                    page,
                    dimension_name,
                    baseline_key=baseline,
                    timeout_sec=user_pick_timeout_sec,
                    poll_ms=50,
                    asked=prefs,
                )
                user_picked = True
            else:
                raise RuntimeError(
                    f"No {dimension_name} match for prefs={prefs!r}. "
                    f"Available: [{available}]. "
                    "Set product_prefs.on_miss: wait_user (click in Chrome) "
                    "or allow_failover: true."
                )
        else:
            log(
                f"Pref match {dimension_name}: pref={matched_pref!r} → "
                f"{chosen.get('autom') or chosen.get('value')} "
                f"({(chosen.get('label') or '')[:40]})"
            )

        # User already clicked — don't re-click, just continue
        if user_picked and chosen.get("checked"):
            wall_ms = (time.perf_counter() - t0) * 1000
            picked = chosen.get("autom") or chosen.get("value") or "?"
            log(
                f"Selected {dimension_name}: {picked} "
                f"(user click, wall={wall_ms:.0f}ms)"
            )
            if timer and mark:
                timer.record(mark, wall_ms, kind="click")
            return str(picked)

        # Sold-out / single SKU: Apple leaves a 0×0 radio in the DOM (checked)
        # and no tile. Clicking it times out on scroll_into_view. Continue.
        if chosen and not chosen.get("visible", True):
            picked = chosen.get("autom") or chosen.get("value") or "?"
            wall_ms = (time.perf_counter() - t0) * 1000
            log(
                f"No visible {_dimension_pretty(dimension_name)} button — "
                f"Apple already set {picked} "
                f"({(chosen.get('label') or '')[:40]}), skip click ({wall_ms:.0f}ms)"
            )
            if timer and mark:
                timer.record(mark, wall_ms, kind="click")
            return str(picked)

        autom = chosen.get("autom") or ""
        sel = (
            f'[data-autom="{autom}"]'
            if autom
            else f'input[name="{dimension_name}"][value="{chosen.get("value")}"]'
        )
        wait_opts_ms = (time.perf_counter() - t0) * 1000
        sel_ms = _click_dimension_tile(page, sel)
        if not _radio_is_checked(page, sel):
            log(f"WARN  pointer click missed {sel} — retry once")
            sel_ms = _click_dimension_tile(page, sel)
        wall_ms = (time.perf_counter() - t0) * 1000
        picked = autom or chosen.get("value") or "?"
        log(
            f"Selected {dimension_name}: {picked} "
            f"({(chosen.get('label') or '')[:40]}) "
            f"wait_opts={wait_opts_ms:.0f}ms click={sel_ms:.0f}ms "
            f"wall={wall_ms:.0f}ms"
        )
        if timer and mark:
            timer.record(mark, wall_ms, kind="click")
        return str(picked)
    raise RuntimeError(
        f"Dimension {dimension_name} never became selectable; last={last_opts!r}"
    )


def _configure_unlocked(page) -> bool:
    """True when size/color/storage radios exist and at least one is enabled."""
    try:
        if _is_apple_404(page):
            return False
        return bool(
            page.evaluate(
                """() => {
                  const dims = Array.from(
                    document.querySelectorAll(
                      'input[type="radio"][name="dimensionColor"],'
                      + 'input[type="radio"][name="dimensionScreensize"],'
                      + 'input[type="radio"][name="dimensionCapacity"]'
                    )
                  );
                  if (!dims.length) return false;
                  return dims.some((el) => !el.disabled);
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _list_hub_buy_links(page) -> list[dict]:
    """Unique product family links from /shop/buy-iphone hub."""
    try:
        return (
            page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  for (const a of document.querySelectorAll('a[href*="/shop/buy-iphone/"]')) {
                    const href = a.href || '';
                    if (!href || seen.has(href)) continue;
                    // Skip the hub itself
                    const path = (new URL(href)).pathname.replace(/\\/+$/, '');
                    if (path.endsWith('/buy-iphone')) continue;
                    const text = (a.innerText || a.textContent || '')
                      .replace(/\\s+/g, ' ').trim();
                    if (!text) continue;
                    seen.add(href);
                    out.push({ href, text: text.slice(0, 120) });
                  }
                  return out;
                }"""
            )
            or []
        )
    except Exception:  # noqa: BLE001
        return []


def _norm_match_hay(value: str) -> str:
    s = (value or "").lower()
    s = re.sub(r"[-_/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_match_sets(family_match: list | None) -> list[list[str]]:
    """
    Accept either a flat token list or a list of token lists.

      ["18", "Pro Max"]                  -> [["18", "Pro Max"]]
      [["18", "Pro Max"], ["18", "Pro"]] -> tried strictest-first

    Apple has always spelled out both variants on the hub card
    ("iPhone 17 Pro & iPhone 17 Pro Max"), but if the launch card is shortened
    to just "iPhone 18 Pro" the "Pro Max" token finds nothing. A looser second
    set lets Phase B still auto-open instead of dropping to a manual click.
    """
    raw = family_match or []
    if raw and all(isinstance(x, (list, tuple)) for x in raw):
        groups = [list(x) for x in raw]
    else:
        groups = [list(raw)]
    out: list[list[str]] = []
    for group in groups:
        tokens = [str(t) for t in group if str(t).strip()]
        if tokens and tokens not in out:
            out.append(tokens)
    return out


# How each model is worded on Apple's hub card, strictest first. Apple normally
# spells out both variants ("iPhone 17 Pro & iPhone 17 Pro Max"), so "Pro Max"
# hits; the looser "Pro" covers a shortened launch card. Models not listed match
# on the year alone, which is right for base / Plus-sharing pages like iPhone 16.
_MODEL_HUB_TOKENS: dict[str, tuple[list[str], ...]] = {
    "pro-max": (["Pro Max"], ["Pro"]),
    "pro": (["Pro"],),
    "plus": (["Plus"],),
    "air": (["Air"],),
}


# Which screensize tile belongs to each model, used when config omits
# screensizes. Inch numbers change every generation (16 was 6.1/6.7, 17 is
# 6.3/6.9), so match the model word instead. The lookahead is what stops "pro"
# from also matching the "Pro Max" tile.
_MODEL_SIZE_PREFS: dict[str, list[str]] = {
    "pro-max": [r"re:pro\s*max"],
    "pro": [r"re:pro(?!\s*max)"],
    "plus": [r"re:plus"],
}


def _size_prefs_for_model(model: str, size_opts: list[dict]) -> list[str]:
    """Screensize prefs for a model, or [] when it cannot be told apart.

    Returning [] is deliberate: the caller then falls through to on_miss and
    asks you to click, which beats guessing the wrong phone.
    """
    prefs = _MODEL_SIZE_PREFS.get(model)
    if prefs:
        return list(prefs)
    enabled = [o for o in size_opts if not o.get("disabled")] or list(size_opts)
    if model == "base":
        # "Base" means the tile that is *not* Plus/Pro/Max. Prefs are matched
        # per field, and the plain tile's autom ("...6_7inch") carries no such
        # word, so a negative pattern would match it too. Name the tile instead.
        plain = [
            o
            for o in enabled
            if not re.search(
                r"plus|pro|max",
                _norm_match_hay(
                    f"{o.get('autom') or ''} {o.get('label') or ''} "
                    f"{o.get('value') or ''}"
                ),
            )
        ]
        if len(plain) == 1:
            return [str(plain[0].get("autom") or plain[0].get("value") or "")]
    if len(enabled) == 1:
        return [str(enabled[0].get("autom") or enabled[0].get("value") or "")]
    return []


_MODEL_WORDS = {
    "pro-max": "Pro Max",
    "pro": "Pro",
    "plus": "Plus",
    "air": "Air",
    "base": "",
}


def _target_pretty(target: dict | None) -> str:
    """'iPhone 18 Pro Max', or 'iPhone Air' when year is the family word."""
    year = str((target or {}).get("year") or "").strip()
    model = str((target or {}).get("model") or "").strip().lower().replace(" ", "-")
    word = _MODEL_WORDS.get(model, model.replace("-", " ").title())
    if year.lower() == model:
        return f"iPhone {word}".strip() or "iPhone"
    return f"iPhone {year} {word}".strip()


def _derive_match_sets(target: dict | None) -> list[list[str]]:
    """Build hub match tokens from target.year + target.model.

    Lets config omit family_match entirely: year and model already say which
    card we want, so repeating it is just another line to get wrong.
    """
    year = str((target or {}).get("year") or "").strip()
    if not year:
        return []
    model = str((target or {}).get("model") or "").strip().lower()
    out: list[list[str]] = []
    for extra in _MODEL_HUB_TOKENS.get(model, ([],)):
        tokens = [year] + list(extra)
        if tokens not in out:
            out.append(tokens)
    return out


def _hub_link_matches(link: dict, family_match: list[str]) -> bool:
    """All match tokens must appear in href or text (case-insensitive)."""
    tokens = [str(t).strip().lower() for t in family_match if str(t).strip()]
    if not tokens:
        return False
    hay = _norm_match_hay(f"{link.get('href') or ''} {link.get('text') or ''}")
    return all(_norm_match_hay(tok) in hay for tok in tokens)


def _hub_link_forbidden_reason(link: dict, *, year: str) -> str | None:
    """Drop leftover 17 / Fold / Air cards when the order target is iPhone 18."""
    hay = _norm_match_hay(f"{link.get('href') or ''} {link.get('text') or ''}")
    href = (link.get("href") or "").lower()
    if str(year) != "18":
        return None
    if "iphone 17" in hay or "iphone-17" in href:
        return "iPhone 17"
    if "iphone 16" in hay or "iphone-16" in href:
        return "iPhone 16"
    if "fold" in hay:
        return "Fold"
    if re.search(r"iphone[- ]?duo\b", hay) or "iphone-duo" in href:
        return "Duo"
    if re.search(r"iphone[- ]?air\b", hay):
        return "Air"
    return None


def load_order_target(cfg: dict) -> dict:
    raw = cfg.get("target") if isinstance(cfg.get("target"), dict) else {}
    year = str(raw.get("year") or "").strip()
    model = str(raw.get("model") or "").strip().lower().replace(" ", "-")
    if model in ("promax", "pro_max"):
        model = "pro-max"
    if not year:
        raise ConfigError(
            "config target.year is required. Use the generation number (17, "
            "18), or the family word when the phone has no number in its "
            "name — iPhone Air is target.year: air, model: air."
        )
    if not model:
        raise ConfigError(
            "config target.model is required: pro-max, pro, plus, base or air."
        )
    return {"year": year, "model": model}


def _buy_page_snapshot(page) -> dict:
    try:
        snap = page.evaluate(
            """() => {
              const h1 = ((document.querySelector('h1') || {}).innerText || '')
                .replace(/\\s+/g, ' ').trim();
              return {
                url: location.href || '',
                h1,
                title: (document.title || '').replace(/\\s+/g, ' ').trim(),
              };
            }"""
        )
    except Exception:  # noqa: BLE001
        snap = {}
    return snap if isinstance(snap, dict) else {}


def _family_year_matches(page, target: dict | None) -> bool:
    """
    Non-raising year check for a candidate configure page.

    Before launch, a not-yet-published slug may redirect to the current
    generation instead of 404ing, so a guessed URL can come back live, unlocked
    and completely wrong. Phase A uses this to discard such a page and keep
    looking, rather than handing it to the hard guard and killing the run.
    """
    year = str((target or {}).get("year") or "")
    if not year:
        return True
    snap = _buy_page_snapshot(page)
    url = (snap.get("url") or page.url or "").lower()
    blob = _norm_match_hay(
        f"{url} {snap.get('h1') or ''} {snap.get('title') or ''}"
    )
    return f"iphone {year}" in blob or f"iphone-{year}" in url


def assert_family_is_order_target(page, target: dict) -> None:
    """Hard stop before trade-in if this is 17 / Fold / Air / wrong year."""
    year = str(target.get("year") or "")
    snap = _buy_page_snapshot(page)
    url = (snap.get("url") or page.url or "").lower()
    h1 = str(snap.get("h1") or "")
    title = str(snap.get("title") or "")
    blob = _norm_match_hay(f"{url} {h1} {title}")
    log(f"TARGET CHECK family: year={year} url={page.url} h1={h1[:80]!r}")

    if f"iphone {year}" not in blob and f"iphone-{year}" not in url:
        raise RuntimeError(
            f"REFUSE: not iPhone {year} (url={page.url}, h1={h1[:80]!r}). "
            "Will not add to bag — this prevents ordering iPhone 17."
        )
    if year == "18":
        if "iphone-17" in url or "iphone 17" in blob:
            raise RuntimeError(
                "REFUSE: iPhone 17 configure page. Target is iPhone 18 Pro Max. "
                "Will not add to bag."
            )
        if "fold" in url or re.search(r"\bfold\b", blob):
            raise RuntimeError(
                "REFUSE: Fold page. Fold is not the VN order target."
            )
        if "iphone-duo" in url or re.search(r"iphone[- ]?duo\b", blob):
            raise RuntimeError(
                "REFUSE: iPhone Duo page. Duo is not the VN order target."
            )
        if re.search(r"iphone[- ]?air\b", url) or re.search(r"iphone air\b", blob):
            raise RuntimeError("REFUSE: iPhone Air page. Target is 18 Pro Max.")


def assert_sku_is_pro_max(page, target: dict) -> None:
    """After size/color/storage: 6.9 / Pro Max must be the checked screensize."""
    if str(target.get("model") or "pro-max") != "pro-max":
        return
    sizes = _list_dimension_options(page, "dimensionScreensize")
    if not sizes:
        raise RuntimeError(
            "REFUSE: no screensize radios — not a Pro/Pro Max family page. "
            "Will not add to bag."
        )
    checked = next((s for s in sizes if s.get("checked")), None)
    if not checked:
        raise RuntimeError("REFUSE: no screensize selected. Will not add to bag.")
    hay = _norm_match_hay(
        f"{checked.get('autom') or ''} {checked.get('label') or ''} "
        f"{checked.get('value') or ''}"
    )
    ok = any(tok in hay for tok in ("6 9", "6,9", "6.9", "pro max"))
    log(f"TARGET CHECK sku: selected screensize={checked!r} ok={ok}")
    if not ok:
        raise RuntimeError(
            f"REFUSE: screensize is not Pro Max 6.9 (got {checked.get('label')!r} "
            f"/ {checked.get('autom')!r}). Will not add to bag."
        )


def assert_sku_matches_model(page, target: dict) -> None:
    """Refuse a screensize that contradicts target.model.

    Pro Max has its own stricter guard above. This covers the rest, so a
    `model: pro` target can no longer walk off with a Pro Max in the bag.
    """
    model = str(target.get("model") or "pro-max").strip().lower().replace(" ", "-")
    if model == "pro-max":
        return
    sizes = _list_dimension_options(page, "dimensionScreensize")
    if not sizes:
        return  # single-size family (Air, 17e): nothing to contradict
    prefs = _size_prefs_for_model(model, sizes)
    if not prefs:
        return  # unknown model: we never picked it, so we cannot judge it
    checked = next((s for s in sizes if s.get("checked")), None)
    if not checked:
        raise RuntimeError("REFUSE: no screensize selected. Will not add to bag.")
    ok = any(_pref_matches(checked, p) for p in prefs)
    log(
        f"TARGET CHECK sku: model={model} "
        f"selected={checked.get('autom')!r} ok={ok}"
    )
    if not ok:
        raise RuntimeError(
            f"REFUSE: screensize {checked.get('label')!r} does not match "
            f"model {model!r}. Will not add to bag."
        )


def _wait_user_family_page(
    page,
    *,
    timeout_sec: float = 45.0,
    poll_ms: int = 100,
    target: dict | None = None,
) -> str:
    """
    User opens/clicks the real family buy page in Chrome.
    Continues as soon as configure radios unlock (soft-404 ignored).
    """
    want = _target_pretty(target) if target else "your target iPhone"
    log(
        f"USER PICK  click {want} on the hub NOW "
        f"(anything else will be refused) "
        f"(poll={poll_ms}ms, timeout={timeout_sec:.0f}s)"
    )
    beep()
    notify_macos(
        f"Assist — pick {want}",
        f"Click {want} on the hub. Anything else is refused before the bag.",
    )
    t0 = time.perf_counter()
    deadline = t0 + max(5.0, timeout_sec)
    last_log = 0.0
    while time.perf_counter() < deadline:
        if _configure_unlocked(page):
            ms = (time.perf_counter() - t0) * 1000
            log(f"USER PICK  family configure ready in {ms:.0f}ms → {page.url}")
            return page.url
        now = time.perf_counter()
        if now - last_log >= 2.0:
            log(
                f"USER PICK  waiting for family page… "
                f"{(now - t0):.1f}s / {timeout_sec:.0f}s "
                f"(url={page.url[:90]})"
            )
            last_log = now
        page.wait_for_timeout(poll_ms)
    raise RuntimeError(
        f"Timed out waiting for you to open a live buy-iphone configure page "
        f"({timeout_sec:.0f}s). Last url={page.url}"
    )


def wait_family_configure_ready(
    page,
    family_urls: list[str],
    *,
    timer: StageTimer | None = None,
    poll_ms: int = 400,
    timeout_sec: int = 20,
    hub_url: str = "https://www.apple.com/vn/shop/buy-iphone/",
    family_match: list[str] | None = None,
    user_pick_timeout_sec: float = 45.0,
    target: dict | None = None,
) -> str:
    """
    Resolve a live configure page:
      A) Poll guessed family_urls (soft-404 = dead slug, short budget)
      B) Open buy-iphone hub, auto-open unique family_match link
      C) wait_user: you click the phone tile / open the right URL
    Returns the URL that became configure-ready.
    """
    urls = []
    seen = set()
    for u in family_urls:
        if u and u not in seen:
            urls.append(u)
            seen.add(u)
    if not urls and not hub_url:
        raise RuntimeError("No family_url / hub_url candidates")

    t_all = time.perf_counter()
    match_sets = _normalize_match_sets(family_match)

    # ----- Phase A: guessed URLs -----
    if urls:
        deadline_a = time.perf_counter() + max(5, int(timeout_sec))
        log(
            f"FAMILY A  poll guessed URLs ({len(urls)}), "
            f"up to {timeout_sec}s (soft-404 = skip)…"
        )
        dead_404: set[str] = set()
        idx = 0
        while time.perf_counter() < deadline_a:
            alive = [u for u in urls if u not in dead_404]
            if not alive:
                log("FAMILY A  all guessed URLs are soft-404 — skipping to hub")
                break
            url = alive[idx % len(alive)]
            idx += 1
            t_round = time.perf_counter()
            try:
                already = (
                    urlparse(page.url).path.rstrip("/")
                    == urlparse(url).path.rstrip("/")
                )
                if not already:
                    goto_resilient(page, url)
            except Exception as exc:  # noqa: BLE001
                log(f"FAMILY A  nav issue: {exc}")
                page.wait_for_timeout(poll_ms)
                continue
            if _is_apple_404(page):
                dead_404.add(url)
                log(
                    f"FAMILY A  soft-404 ({(time.perf_counter() - t_round) * 1000:.0f}ms): "
                    f"{url}"
                )
                page.wait_for_timeout(min(poll_ms, 200))
                continue
            if _configure_unlocked(page):
                if not _family_year_matches(page, target):
                    dead_404.add(url)
                    log(
                        f"FAMILY A  live but WRONG family — discarding {url} "
                        f"(landed {page.url[:90]}, target iPhone "
                        f"{(target or {}).get('year')})"
                    )
                    page.wait_for_timeout(min(poll_ms, 200))
                    continue
                elapsed = (time.perf_counter() - t_all) * 1000
                log(f"FAMILY A  configure unlocked ({elapsed:.0f}ms): {page.url}")
                if timer:
                    timer.since(t_all, "0a configure unlocked (guessed URL)", kind="poll")
                notify_macos("Assist — configure unlocked", page.url[:80])
                return page.url
            log(
                f"FAMILY A  page live but configure not ready yet "
                f"({(time.perf_counter() - t_round) * 1000:.0f}ms) {page.url[:90]}"
            )
            page.wait_for_timeout(poll_ms)
        log(
            f"FAMILY A  done without unlock "
            f"({(time.perf_counter() - t_all) * 1000:.0f}ms) → hub"
        )

    # ----- Phase B: hub scrape -----
    hub = (hub_url or "https://www.apple.com/vn/shop/buy-iphone/").strip()
    if hub:
        log(f"FAMILY B  opening hub: {hub}")
        try:
            goto_resilient(page, hub)
        except Exception as exc:  # noqa: BLE001
            log(f"FAMILY B  hub nav failed: {exc}")
        page.wait_for_timeout(300)
        links = _list_hub_buy_links(page)
        log(
            "FAMILY B  hub cards: "
            + (
                ", ".join(f"{x.get('text')!r}" for x in links[:12])
                if links
                else "(none)"
            )
        )
        year = str((target or {}).get("year") or "")
        # Strictest token set first; only widen when a set finds nothing at all.
        for attempt, match_tokens in enumerate(match_sets):
            matched = [x for x in links if _hub_link_matches(x, match_tokens)]
            dropped = []
            kept = []
            for x in matched:
                why = _hub_link_forbidden_reason(x, year=year)
                if why:
                    dropped.append(f"{x.get('text')!r} ({why})")
                else:
                    kept.append(x)
            if dropped:
                log("FAMILY B  dropped forbidden cards: " + ", ".join(dropped[:8]))
            matched = kept
            looser = " (looser fallback)" if attempt else ""
            log(
                f"FAMILY B  match tokens={match_tokens!r}{looser} → "
                f"{len(matched)} hit(s): "
                + ", ".join(f"{x.get('text')!r}" for x in matched[:6])
            )
            if len(matched) > 1:
                log("FAMILY B  multiple matches — USER PICK (won't guess)")
                break
            if not matched:
                continue
            hub_target = matched[0]["href"]
            log(f"FAMILY B  unique match — opening {hub_target}")
            goto_resilient(page, hub_target)
            # Brief wait for configure (page may need a beat)
            t_b = time.perf_counter()
            while time.perf_counter() - t_b < 8.0:
                if _configure_unlocked(page):
                    elapsed = (time.perf_counter() - t_all) * 1000
                    log(f"FAMILY B  configure unlocked ({elapsed:.0f}ms): {page.url}")
                    if timer:
                        timer.since(t_all, "0a configure unlocked (hub)", kind="poll")
                    notify_macos("Assist — configure unlocked", page.url[:80])
                    return page.url
                page.wait_for_timeout(150)
            log("FAMILY B  opened match but configure not ready — USER PICK")
            break
        else:
            log("FAMILY B  no match on any token set — USER PICK on hub")

        # Scroll hub into a useful spot
        try:
            page.evaluate(
                """() => {
                  const a = document.querySelector('a[href*="/shop/buy-iphone/iphone"]');
                  if (a) a.scrollIntoView({ block: 'center', behavior: 'auto' });
                }"""
            )
        except Exception:  # noqa: BLE001
            pass

    # ----- Phase C: user clicks -----
    ready_url = _wait_user_family_page(
        page, timeout_sec=user_pick_timeout_sec, poll_ms=100, target=target
    )
    if timer:
        timer.since(t_all, "0a configure unlocked (user)", kind="poll")
    notify_macos("Assist — configure unlocked", ready_url[:80])
    return ready_url


def _some_dimension_enabled(page, dimension_name: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """(name) => Array.from(
                  document.querySelectorAll(
                    'input[type="radio"][name="' + name + '"]'
                  )
                ).some((el) => !el.disabled)""",
                dimension_name,
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _capacity_prices_committed(page) -> bool:
    """True when storage tiles show a concrete SKU price, not family 'Từ …'."""
    try:
        return bool(
            page.evaluate(
                """() => {
                  const els = Array.from(
                    document.querySelectorAll(
                      'input[type="radio"][name="dimensionCapacity"]'
                    )
                  );
                  if (!els.some((el) => !el.disabled)) return false;
                  return els.filter((el) => !el.disabled).every((el) => {
                    const lab = el.id
                      ? document.querySelector('label[for="' + el.id + '"]')
                      : null;
                    const t = ((lab && lab.innerText) || '').replace(/\\s+/g, ' ');
                    return /\\d[\\d.]*\\s*đ/.test(t) && !/Từ\\s*[\\d.]/.test(t);
                  });
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _trade_in_enabled(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => {
                  const t = document.querySelector(
                    '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
                  );
                  return !!(t && !t.disabled);
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _bfe_dimension_committed(page, dimension_name: str) -> bool:
    """DOM `checked` is not enough — Apple's buy-flow has a later commit."""
    if dimension_name == "dimensionScreensize":
        return _some_dimension_enabled(page, "dimensionColor")
    if dimension_name == "dimensionColor":
        return _capacity_prices_committed(page)
    if dimension_name == "dimensionCapacity":
        return _trade_in_enabled(page)
    return False


def _wait_dimension_enabled_stable(
    page, dimension_name: str, *, settle_ms: int = 200, timeout_ms: int = 10_000
) -> float:
    """Wait until radios are enabled AND stay enabled (cascade finished)."""
    t0 = time.perf_counter()
    deadline = t0 + timeout_ms / 1000
    ok_since: float | None = None
    while time.perf_counter() < deadline:
        enabled = _some_dimension_enabled(page, dimension_name)
        now = time.perf_counter()
        if enabled:
            if ok_since is None:
                ok_since = now
            elif (now - ok_since) * 1000 >= settle_ms:
                return (now - t0) * 1000
        else:
            ok_since = None
        page.wait_for_timeout(40)
    raise TimeoutError(f"{dimension_name} radios never stayed enabled")


def _wait_capacity_cascade_ready(page, *, timeout_ms: int = 10_000) -> float:
    """After color, wait until storage tiles show a concrete SKU price.

    Clicking 256GB in the 2ms window where radios merely `!disabled` checks the
    input; Apple's buy-flow has not bound Trade In yet, so it stays locked.
    """
    t0 = time.perf_counter()
    page.wait_for_function(
        """() => {
          const els = Array.from(
            document.querySelectorAll(
              'input[type="radio"][name="dimensionCapacity"]'
            )
          );
          if (!els.some((el) => !el.disabled)) return false;
          return els.filter((el) => !el.disabled).every((el) => {
            const lab = el.id
              ? document.querySelector('label[for="' + el.id + '"]')
              : null;
            const t = ((lab && lab.innerText) || '').replace(/\\s+/g, ' ');
            return /\\d[\\d.]*\\s*đ/.test(t) && !/Từ\\s*[\\d.]/.test(t);
          });
        }""",
        timeout=timeout_ms,
    )
    return (time.perf_counter() - t0) * 1000


def _click_dimension_tile(page, input_selector: str) -> float:
    """One real pointer click on the SKU tile (trusted event). JS .click() is not enough."""
    t0 = time.perf_counter()
    loc = page.locator(input_selector).first
    loc.wait_for(state="attached", timeout=5_000)
    loc.scroll_into_view_if_needed(timeout=2_000)
    input_id = loc.get_attribute("id")
    target = (
        page.locator(f'label[for="{input_id}"]').first if input_id else loc
    )
    target.click(timeout=2_000)
    page.wait_for_function(
        """(sel) => {
          const el = document.querySelector(sel);
          return !!(el && el.checked);
        }""",
        arg=input_selector,
        timeout=3_000,
    )
    ms = (time.perf_counter() - t0) * 1000
    log(f"CLICK  dimension tile {input_selector}: {ms:.0f}ms (Playwright pointer)")
    return ms


def _trade_in_debug(page) -> str:
    try:
        return str(
            page.evaluate(
                """() => {
                  const t = document.querySelector('[data-autom="choose-noTradeIn"]');
                  const yes = document.querySelector('[data-autom="choose-tradeIn"]');
                  const add = document.querySelector('[data-autom="add-to-cart"]');
                  const verify = document.querySelector(
                    '[data-autom="tradeup-module-verify"]'
                  );
                  const cap = document.querySelector(
                    'input[name="dimensionCapacity"]:checked'
                  );
                  const busy = !!document.querySelector(
                    '[aria-busy="true"], .as-loader, .rf-loader'
                  );
                  const parent = t && t.closest(
                    'fieldset, .rf-tradeupinline-mainwrapper, [class*="decision"]'
                  );
                  return [
                    'cap=' + (cap && cap.getAttribute('data-autom')),
                    'noTrade=' + (t ? ('dis=' + t.disabled + '/chk=' + t.checked) : 'missing'),
                    'yesTrade=' + (yes ? ('dis=' + yes.disabled) : 'missing'),
                    'addDis=' + (add ? add.disabled : '?'),
                    'verify=' + (verify && verify.offsetParent ? 'visible' : 'no'),
                    'busy=' + busy,
                    'parent=' + (parent && String(parent.className).slice(0, 80)),
                    'path=' + location.pathname.slice(-60),
                  ].join(' ');
                }"""
            )
        )
    except Exception as exc:  # noqa: BLE001
        return f"debug-failed {exc}"


def _wait_trade_in_ready(page, *, timer: StageTimer | None = None) -> None:
    """Wait for Apple to enable Trade In after the SKU click. No reload, no second 256GB click."""
    ready_js = """() => {
      const t = document.querySelector(
        '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
      );
      return !!(t && !t.disabled);
    }"""
    t0 = time.perf_counter()
    log(f"Trade-in debug (before wait): {_trade_in_debug(page)}")
    try:
        _wait_js_heartbeat(
            page,
            ready_js,
            label="trade-in radios enabled after SKU",
            timeout_ms=15_000,
            snapshot_js="""() => {
              const t = document.querySelector('[data-autom="choose-noTradeIn"]');
              const cap = document.querySelector(
                'input[name="dimensionCapacity"]:checked'
              );
              const add = document.querySelector('[data-autom="add-to-cart"]');
              const ds = document.querySelector(
                '[class*="decisionsection"], [data-autom="tradein_decisionsection"]'
              );
              return [
                t ? ('dis=' + t.disabled) : 'missing',
                'cap=' + (cap && cap.getAttribute('data-autom')),
                'addDis=' + (add ? add.disabled : '?'),
                'ds=' + (ds && String(ds.className).slice(0, 50)),
              ].join(' ');
            }""",
        )
    except TimeoutError:
        dbg = _trade_in_debug(page)
        log(f"Trade-in STUCK disabled: {dbg}")
        raise TimeoutError(
            "Trade In radios stayed disabled after 256GB. "
            f"{dbg}"
        ) from None
    wait_ms = (time.perf_counter() - t0) * 1000
    log(f"WAIT  trade-in ready after dimensions: {wait_ms:.0f}ms — {page.url}")
    if timer:
        timer.record("0g dimensions complete (trade-in ready)", wait_ms, kind="wait")


def select_product_dimensions(
    page,
    prefs: dict,
    *,
    timer: StageTimer | None = None,
    target: dict | None = None,
) -> None:
    """
    Family-page SKU pick (order Apple uses):
      Screensize (Pro) → Color → Capacity
    Then trade-in becomes enabled for the existing decline path.
    """
    screensizes = [str(x) for x in (prefs.get("screensizes") or prefs.get("sizes") or [])]
    colors = [str(x) for x in (prefs.get("colors") or [])]
    storages = [str(x) for x in (prefs.get("storages") or prefs.get("capacities") or [])]
    allow_failover = bool(prefs.get("allow_failover"))
    on_miss = str(prefs.get("on_miss") or "wait_user")
    user_pick_timeout_sec = float(prefs.get("user_pick_timeout_sec") or 20)
    if not colors and not storages and not screensizes:
        raise RuntimeError("product_prefs needs colors and/or storages (and sizes for Pro)")

    # Always dump live VN labels/slugs — critical on launch day for unknown names
    size_opts = _list_dimension_options(page, "dimensionScreensize")
    color_opts = _list_dimension_options(page, "dimensionColor")
    cap_opts = _list_dimension_options(page, "dimensionCapacity")
    if size_opts:
        log(f"LIVE screensizes: [{_format_dimension_options(size_opts)}]")
    if color_opts:
        log(f"LIVE colors: [{_format_dimension_options(color_opts)}]")
    if cap_opts:
        log(f"LIVE storages: [{_format_dimension_options(cap_opts)}]")

    dim_kwargs = dict(
        timer=timer,
        allow_failover=allow_failover,
        on_miss=on_miss,
        user_pick_timeout_sec=user_pick_timeout_sec,
    )

    if size_opts:
        t_size = time.perf_counter()
        size_prefs = screensizes
        if not size_prefs:
            model = (
                str((target or {}).get("model") or "pro-max")
                .strip()
                .lower()
                .replace(" ", "-")
            )
            size_prefs = _size_prefs_for_model(model, size_opts)
            log(
                f"screensizes not set — model {model!r} → prefs {size_prefs!r}"
                f"{' (ambiguous, will ask you to click)' if not size_prefs else ''}"
            )
        _select_dimension_by_prefs(
            page,
            "dimensionScreensize",
            size_prefs,
            mark="0d screensize",
            **dim_kwargs,
        )
        log(
            f"CLICK  screensize select wall: "
            f"{(time.perf_counter() - t_size) * 1000:.0f}ms"
        )
        t_wait = time.perf_counter()
        wait_ms = _wait_dimension_enabled_stable(page, "dimensionColor")
        log(f"WAIT  color options after screensize: {wait_ms:.0f}ms")
        if timer:
            timer.record("0d2 wait color unlock", wait_ms, kind="wait")

    if colors or _list_dimension_options(page, "dimensionColor"):
        t_color = time.perf_counter()
        _select_dimension_by_prefs(
            page,
            "dimensionColor",
            colors or ["black", "Đen"],
            mark="0e color",
            **dim_kwargs,
        )
        log(
            f"CLICK  color select wall: "
            f"{(time.perf_counter() - t_color) * 1000:.0f}ms"
        )
        t_wait = time.perf_counter()
        wait_ms = _wait_capacity_cascade_ready(page)
        log(f"WAIT  capacity cascade after color: {wait_ms:.0f}ms")
        if timer:
            timer.record("0e2 wait capacity cascade", wait_ms, kind="wait")

    if storages or _list_dimension_options(page, "dimensionCapacity"):
        t_cap = time.perf_counter()
        _select_dimension_by_prefs(
            page,
            "dimensionCapacity",
            storages or ["256gb", "256"],
            mark="0f capacity",
            **dim_kwargs,
        )
        log(
            f"CLICK  capacity select wall: "
            f"{(time.perf_counter() - t_cap) * 1000:.0f}ms"
        )
        try:
            st = page.evaluate(
                """() => {
                  const t = document.querySelector('[data-autom="choose-noTradeIn"]');
                  const cap = document.querySelector(
                    'input[name="dimensionCapacity"]:checked'
                  );
                  return {
                    url: location.href,
                    cap: cap ? cap.getAttribute('data-autom') : null,
                    tradeDisabled: t ? t.disabled : null,
                  };
                }"""
            )
            log(
                f"After capacity: cap={st.get('cap')} "
                f"tradeDisabled={st.get('tradeDisabled')} url={st.get('url','')[:100]}"
            )
        except Exception:  # noqa: BLE001
            pass

    _wait_trade_in_ready(page, timer=timer)


def open_product_page(page, product_url: str) -> None:
    """
    Open configured product URL. Apple VN deep links often 404 when opened cold;
    family page first, then deep link, fixes it.
    """
    goto_resilient(page, product_url)
    if not _is_apple_404(page):
        # Wait until trade-in exists (configured page) or bail to family
        try:
            page.locator(
                '#noTradeIn, [data-autom="choose-noTradeIn"], input[value="noTradeIn"]'
            ).first.wait_for(state="attached", timeout=2_500)
            return
        except Exception:  # noqa: BLE001
            if not _is_apple_404(page):
                return
    family = _product_family_url(product_url)
    log(f"Product deep link weak/404 — warming via family page: {family}")
    goto_resilient(page, family)
    page.wait_for_timeout(350)
    goto_resilient(page, product_url)
    if _is_apple_404(page):
        log("Deep link still 404 — continuing on family buy page")
        goto_resilient(page, family)
    if _is_apple_404(page):
        raise RuntimeError(f"Apple product page 404: {product_url}")


def goto_resilient(page, url: str, attempts: int = 3) -> float:
    """Apple sometimes aborts the first navigation right after login redirects.

    Returns elapsed ms for the successful (or usable-abort) navigation.
    """
    last_exc: Exception | None = None
    t0 = time.perf_counter()
    for i in range(1, attempts + 1):
        t_attempt = time.perf_counter()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            ms = (time.perf_counter() - t0) * 1000
            log(
                f"NAV  goto ok attempt {i}/{attempts}: "
                f"{(time.perf_counter() - t_attempt) * 1000:.0f}ms "
                f"(total nav {ms:.0f}ms) → {page.url[:100]}"
            )
            return ms
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            log(
                f"NAV  attempt {i}/{attempts} failed "
                f"({(time.perf_counter() - t_attempt) * 1000:.0f}ms): "
                f"{msg.splitlines()[0][:160]}"
            )
            # If the abort still landed us on (or near) the target, continue.
            current = page.url
            if "buy-iphone" in current or urlparse(current).path.rstrip("/") == urlparse(url).path.rstrip("/"):
                ms = (time.perf_counter() - t0) * 1000
                log(f"NAV  usable page after abort ({ms:.0f}ms): {current}")
                return ms
            page.wait_for_timeout(1500)
    assert last_exc is not None
    raise last_exc


def _radio_is_checked(page, input_selector: str) -> bool:
    return bool(
        page.evaluate(
            """(selector) => {
              const el = document.querySelector(selector);
              return !!(el && el.checked);
            }""",
            input_selector,
        )
    )


def _click_radio_strategies(page, input_selector: str, *, text_hints: list[str] | None = None) -> tuple[str, float]:
    """
    Try several real-UI click strategies (never fake .checked).
    Returns (strategy_used, click_wall_ms).
    """
    t0 = time.perf_counter()
    page.locator(input_selector).first.wait_for(state="attached", timeout=5_000)
    attach_ms = (time.perf_counter() - t0) * 1000
    hints = text_hints or []

    # 1) Associated label / form-selector click (in-page — beats sticky intercept)
    t_click = time.perf_counter()
    via = page.evaluate(
        """(selector) => {
          const el = document.querySelector(selector);
          if (!el) return 'missing';
          el.scrollIntoView({ block: 'center', inline: 'nearest' });
          const label = el.closest('label')
            || (el.id ? document.querySelector('label[for="' + el.id + '"]') : null)
            || el.parentElement?.querySelector('label');
          const row = el.closest('.form-selector, .rf-form-selector, [role="radio"]')
            || el.parentElement;
          if (label) { label.click(); return 'label'; }
          if (row) { row.click(); return 'row'; }
          el.click();
          return 'input';
        }""",
        input_selector,
    )
    click1_ms = (time.perf_counter() - t_click) * 1000
    if _radio_is_checked(page, input_selector):
        total = (time.perf_counter() - t0) * 1000
        log(
            f"CLICK  radio {via}: attach={attach_ms:.0f}ms click={click1_ms:.0f}ms "
            f"total={total:.0f}ms"
        )
        return via, total

    # 2) Visible Vietnamese/English text on page (AppleCare often needs this)
    for hint in hints:
        try:
            loc = page.get_by_text(hint, exact=False)
            if loc.count() == 0:
                continue
            t_hint = time.perf_counter()
            target = loc.first
            target.scroll_into_view_if_needed(timeout=800)
            target.click(force=True, timeout=1_200, no_wait_after=True)
            hint_ms = (time.perf_counter() - t_hint) * 1000
            if _radio_is_checked(page, input_selector):
                total = (time.perf_counter() - t0) * 1000
                log(
                    f"CLICK  radio text:{hint!r}: click={hint_ms:.0f}ms "
                    f"total={total:.0f}ms"
                )
                return f"text:{hint}", total
        except Exception:  # noqa: BLE001
            continue

    # 3) Playwright force-click on the input itself
    try:
        t_pw = time.perf_counter()
        page.locator(input_selector).first.click(
            force=True, timeout=1_200, no_wait_after=True
        )
        pw_ms = (time.perf_counter() - t_pw) * 1000
        if _radio_is_checked(page, input_selector):
            total = (time.perf_counter() - t0) * 1000
            log(f"CLICK  radio pw-force-input: click={pw_ms:.0f}ms total={total:.0f}ms")
            return "pw-force-input", total
    except Exception:  # noqa: BLE001
        pass

    # 4) Playwright force-click label[for=id]
    try:
        input_id = page.locator(input_selector).first.get_attribute("id")
        if input_id:
            t_lbl = time.perf_counter()
            page.locator(f'label[for="{input_id}"]').first.click(
                force=True, timeout=1_200, no_wait_after=True
            )
            lbl_ms = (time.perf_counter() - t_lbl) * 1000
            if _radio_is_checked(page, input_selector):
                total = (time.perf_counter() - t0) * 1000
                log(
                    f"CLICK  radio pw-force-label: click={lbl_ms:.0f}ms "
                    f"total={total:.0f}ms"
                )
                return "pw-force-label", total
    except Exception:  # noqa: BLE001
        pass

    total = (time.perf_counter() - t0) * 1000
    log(f"CLICK  radio strategies exhausted ({total:.0f}ms) last={via}")
    return via or "failed", total


def _select_radio_until_checked(
    page,
    input_selector: str,
    *,
    label: str,
    text_hints: list[str] | None = None,
    timeout_ms: int = 6_000,
    attempts: int = 5,
) -> float:
    """Click + verify loop until input.checked. Returns elapsed ms."""
    t0 = time.perf_counter()
    deadline = time.perf_counter() + timeout_ms / 1000
    last_via = "none"
    for i in range(attempts):
        if time.perf_counter() >= deadline:
            break
        if _radio_is_checked(page, input_selector):
            ms = (time.perf_counter() - t0) * 1000
            log(f"CLICK  {label}: already checked (attempt {i + 1}, {ms:.0f}ms)")
            return ms
        last_via, click_ms = _click_radio_strategies(
            page, input_selector, text_hints=text_hints
        )
        # Short poll for React to commit checked state
        t_verify = time.perf_counter()
        try:
            page.wait_for_function(
                """(selector) => {
                  const el = document.querySelector(selector);
                  return !!(el && el.checked);
                }""",
                arg=input_selector,
                timeout=min(900, max(200, int((deadline - time.perf_counter()) * 1000))),
            )
            verify_ms = (time.perf_counter() - t_verify) * 1000
            ms = (time.perf_counter() - t0) * 1000
            log(
                f"CLICK  {label}: OK via={last_via} attempt={i + 1} "
                f"click={click_ms:.0f}ms verify={verify_ms:.0f}ms total={ms:.0f}ms"
            )
            return ms
        except Exception:  # noqa: BLE001
            verify_ms = (time.perf_counter() - t_verify) * 1000
            log(
                f"CLICK  {label}: not committed yet via={last_via} "
                f"attempt={i + 1} click={click_ms:.0f}ms verify_wait={verify_ms:.0f}ms"
            )
            page.wait_for_timeout(80)
            continue
    raise RuntimeError(
        f"{label}: not checked after {attempts} attempts "
        f"(last={last_via}, checked={_radio_is_checked(page, input_selector)})"
    )


def select_declines(
    page, no_trade: str, no_care: str, timeout_ms: int, timer: StageTimer | None = None
) -> None:
    """
    Ordered sprint with hard gates:
      prefind → click trade → VERIFY checked →
      wait AppleCare → click care → VERIFY checked →
      re-verify BOTH + add ready → click add → VERIFY attach/proceed/bag
    """
    del timeout_ms, no_trade, no_care  # labels are for logs; DOM uses data-autom
    timer = timer or StageTimer()

    trade_sel = (
        'input[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
    )
    care_sel = 'input[data-autom="noapplecare"]'

    # ========== PREFIND ==========
    t0 = time.perf_counter()
    pref = page.evaluate(
        """() => ({
          trade: !!document.querySelector(
            '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
          ),
          care: !!document.querySelector('input[data-autom="noapplecare"]'),
          add: !!document.querySelector('[data-autom="add-to-cart"]'),
        })"""
    )
    prefind_ms = (time.perf_counter() - t0) * 1000
    timer.record("0 prefind handles", prefind_ms)
    log(
        f"Prefind: trade={'Y' if pref.get('trade') else 'N'} "
        f"applecare={'Y' if pref.get('care') else 'N'} "
        f"add={'Y' if pref.get('add') else 'N'} ({prefind_ms:.0f}ms)"
    )

    # ========== 1) NO TRADE-IN → verify (retry until checked) ==========
    trade_ms = _select_radio_until_checked(
        page,
        trade_sel,
        label="No trade-in",
        text_hints=["Không đổi cũ lấy mới", "No trade-in", "No, I don’t want to trade in"],
        timeout_ms=6_000,
        attempts=5,
    )
    timer.record("1 no trade-in (verify)", trade_ms, kind="click")

    # ========== 2) Wait AppleCare mount ==========
    t = time.perf_counter()
    page.locator(care_sel).first.wait_for(state="attached", timeout=6_000)
    # Care radios can be attached but not interactive until trade AJAX settles
    try:
        page.wait_for_function(
            """() => {
              const c = document.querySelector('input[data-autom="noapplecare"]');
              if (!c) return false;
              if (c.disabled) return false;
              const wrap = c.closest('[aria-disabled="true"], [data-core-disabled="true"]');
              return !wrap;
            }""",
            timeout=3_000,
        )
    except Exception:  # noqa: BLE001
        page.wait_for_timeout(150)
    care_mount_ms = (time.perf_counter() - t) * 1000
    timer.record("2 wait AppleCare mount", care_mount_ms, kind="wait")

    # ========== 3) NO APPLECARE → verify with retries ==========
    care_ms = _select_radio_until_checked(
        page,
        care_sel,
        label="No AppleCare",
        text_hints=[
            "Không có bảo hành AppleCare+",
            "Không có bảo hành AppleCare",
            "No AppleCare+",
            "No AppleCare",
            "Không thêm bảo hành",
        ],
        timeout_ms=8_000,
        attempts=6,
    )
    # Trade must still be checked after care click
    if not _radio_is_checked(page, trade_sel):
        log("Trade-in lost after AppleCare — re-selecting trade")
        _select_radio_until_checked(
            page,
            trade_sel,
            label="No trade-in (re)",
            text_hints=["Không đổi cũ lấy mới", "No trade-in"],
            timeout_ms=4_000,
            attempts=3,
        )
        _select_radio_until_checked(
            page,
            care_sel,
            label="No AppleCare (re)",
            text_hints=["Không có bảo hành AppleCare+", "No AppleCare+"],
            timeout_ms=4_000,
            attempts=3,
        )
    timer.record("3 no AppleCare (verify)", care_ms, kind="click")

    # ========== 4) Re-verify BOTH + wait until add is actually usable ==========
    t = time.perf_counter()
    page.wait_for_function(
        """() => {
          const t = document.querySelector(
            '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
          );
          const c = document.querySelector('input[data-autom="noapplecare"]');
          if (!t?.checked || !c?.checked) return false;
          const buttons = Array.from(
            document.querySelectorAll('[data-autom="add-to-cart"]')
          );
          const btn = buttons.find((b) => {
            const text = (b.innerText || b.textContent || '')
              .replace(/\\s+/g, ' ').trim().toLowerCase();
            return text === 'thêm vào giỏ hàng' || text === 'add to bag'
              || text === 'thêm vào túi';
          }) || buttons[0];
          if (!btn) return false;
          if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
          const sticky = btn.closest('.rf-bfe-stickybar');
          if (sticky && sticky.hasAttribute('inert')) return false;
          if (btn.closest('[data-core-disabled="true"]')) return false;
          return true;
        }""",
        timeout=3_000,
    )
    add_ready_ms = (time.perf_counter() - t) * 1000
    timer.record("4 wait add ready (both verified)", add_ready_ms, kind="wait")
    log("Verified before add: trade=Y applecare=Y both=Y")

    # Brief settle: Apple enables add before purchase-option AJAX finishes.
    t = time.perf_counter()
    page.wait_for_timeout(50)
    settle_ms = (time.perf_counter() - t) * 1000
    timer.record("4b settle after verify", settle_ms, kind="wait")

    # Re-verify selections survived the settle (React remounts can clear them)
    still = page.evaluate(
        """() => {
          const t = document.querySelector(
            '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
          );
          const c = document.querySelector('input[data-autom="noapplecare"]');
          return !!(t && t.checked && c && c.checked);
        }"""
    )
    if not still:
        raise RuntimeError("Selections lost during settle — before add")
    log("Re-verified after settle: trade=Y applecare=Y — clicking add")

    # ========== 5) Click add ONLY after gates pass ==========
    # In-page click: Playwright hit-testing fights the sticky bar; nav destroys JS context.
    t = time.perf_counter()
    clicked = page.evaluate(
        """() => {
          const norm = (el) => (el.innerText || el.textContent || '')
            .replace(/\\s+/g, ' ').trim().toLowerCase();
          const buttons = Array.from(
            document.querySelectorAll('[data-autom="add-to-cart"]')
          ).filter((b) => {
            const text = norm(b);
            return text === 'thêm vào giỏ hàng' || text === 'add to bag'
              || text === 'thêm vào túi';
          });
          const btn = buttons.find((b) => b.closest('.rf-bfe-stickybar'))
            || buttons.find((b) => !b.disabled
              && b.getAttribute('aria-disabled') !== 'true')
            || buttons[0];
          if (!btn || btn.disabled) return { ok: false, reason: 'no-enabled-add' };
          btn.click();
          return { ok: true, inSticky: !!btn.closest('.rf-bfe-stickybar') };
        }"""
    )
    add_click_ms = (time.perf_counter() - t) * 1000
    timer.record("5 click add-to-cart", add_click_ms, kind="click")
    if not clicked or not clicked.get("ok"):
        raise RuntimeError(f"Add click failed after verify: {clicked}")
    log(
        f"Click order: trade {trade_ms:.0f}ms → "
        f"care-mount {care_mount_ms:.0f}ms → "
        f"care {care_ms:.0f}ms → "
        f"add-ready {add_ready_ms:.0f}ms → "
        f"add-click {add_click_ms:.0f}ms"
        f" (sticky={'Y' if clicked.get('inSticky') else 'N'})"
    )

    # ========== 6) Verify add worked (URL/nav — not in-page evaluate during unload) ==========
    # Race URL change vs proceed button — don't serialize a second wait if proceed wins.
    t_attach = time.perf_counter()
    attach_ok = False
    try:
        page.wait_for_function(
            """() => {
              const u = location.href.toLowerCase();
              if (u.includes('step=attach') || u.includes('/shop/bag')) return true;
              return !!document.querySelector('[data-autom="proceed"]');
            }""",
            timeout=8_000,
        )
        attach_ok = True
    except Exception:  # noqa: BLE001
        attach_ok = False

    if "/shop/404" in page.url.lower() or page.url.rstrip("/").endswith("/404"):
        timer.since(t_attach, "6 add → 404 (blocked/bad session)", kind="wait")
        raise RuntimeError(
            f"Add-to-cart hit Apple 404 (likely session/bot block): {page.url}"
        )

    if not attach_ok:
        log(f"Add did not reach attach (url={page.url}) — quick re-click add")
        t_retry = time.perf_counter()
        try:
            still_ok = page.evaluate(
                """() => {
                  const t = document.querySelector(
                    '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
                  );
                  const c = document.querySelector('input[data-autom="noapplecare"]');
                  return !!(t && t.checked && c && c.checked);
                }"""
            )
            if not still_ok and page.locator(trade_sel).count() > 0:
                _select_radio_until_checked(
                    page,
                    trade_sel,
                    label="No trade-in (retry)",
                    text_hints=["Không đổi cũ lấy mới", "No trade-in"],
                    timeout_ms=3_000,
                    attempts=3,
                )
                page.locator(care_sel).first.wait_for(state="attached", timeout=2_500)
                _select_radio_until_checked(
                    page,
                    care_sel,
                    label="No AppleCare (retry)",
                    text_hints=["Không có bảo hành AppleCare+", "No AppleCare+"],
                    timeout_ms=3_000,
                    attempts=3,
                )
            t_reclick = time.perf_counter()
            page.evaluate(
                """() => {
                  const btn = Array.from(
                    document.querySelectorAll('[data-autom="add-to-cart"]')
                  ).find((b) => {
                    const text = (b.innerText || '').replace(/\\s+/g, ' ')
                      .trim().toLowerCase();
                    return text === 'thêm vào giỏ hàng' || text === 'add to bag'
                      || text === 'thêm vào túi';
                  });
                  if (btn && !btn.disabled) btn.click();
                  return !!btn;
                }"""
            )
            log(
                f"CLICK  add-to-cart retry: "
                f"{(time.perf_counter() - t_reclick) * 1000:.0f}ms"
            )
            t_wait_retry = time.perf_counter()
            page.wait_for_url(
                re.compile(r".*(step=attach|/shop/bag).*", re.I),
                timeout=6_000,
            )
            log(
                f"WAIT  add-retry → attach/bag: "
                f"{(time.perf_counter() - t_wait_retry) * 1000:.0f}ms → {page.url[:100]}"
            )
            attach_ok = True
        except Exception as exc:  # noqa: BLE001
            log(f"Retry add failed: {exc}")
        timer.since(t_retry, "5b retry add → attach", kind="wait")

    if attach_ok:
        if "/shop/404" in page.url.lower():
            raise RuntimeError(f"Landed on 404 after add: {page.url}")
        first_leg = (time.perf_counter() - t_attach) * 1000
        timer.record(
            "6 wait attach/bag after add",
            first_leg,
            kind="wait",
        )
        log(f"Verified after add ({first_leg:.0f}ms since first add click): {page.url}")
    else:
        timer.since(t_attach, "6 attach page (miss → bag fallback)", kind="wait")
        log(f"No attach after verify+retry — bag URL fallback (was {page.url})")

def click_xem_gio_hang_now(page, timer: StageTimer | None = None) -> None:
    """
    Land on bag so Thanh Toán is clickable.
    Prefer direct /shop/bag navigation from attach — more reliable and often
    faster than Xem Giỏ Hàng (proceed click can miss after add nav).
    """
    timer = timer or StageTimer()
    t0 = time.perf_counter()

    if "/shop/bag" in page.url.lower():
        timer.mark("7 already on bag", kind="nav")
        return

    # Direct bag URL from attach — skip proceed hop
    nav_ms = goto_resilient(page, "https://www.apple.com/vn/shop/bag")
    timer.record("7 bag page (direct)", nav_ms, kind="nav")
    log(f"At bag ({(time.perf_counter() - t0) * 1000:.0f}ms): {page.url}")


def _bag_quantity_select_info(page) -> dict:
    """Find real bag line-item quantity dropdowns (not footer stubs)."""
    try:
        info = page.evaluate(
            """() => {
              const real = Array.from(document.querySelectorAll('select'))
                .map((el) => {
                  const autom = el.getAttribute('data-autom') || '';
                  const id = el.id || '';
                  const opts = Array.from(el.options).map((o) => String(o.value));
                  const numeric = opts.length >= 2
                    && opts.every((v) => /^\\d+$/.test(v));
                  const isQty = /quantity/i.test(autom + ' ' + id);
                  return {
                    autom, id, value: String(el.value || ''),
                    options: opts, ok: numeric && isQty,
                  };
                })
                .filter((x) => x.ok);
              return {
                found: real[0] || null,
                n: real.length,
                all: real.slice(0, 6),
              };
            }"""
        )
        return info if isinstance(info, dict) else {}
    except Exception as exc:  # noqa: BLE001
        return {"found": None, "n": 0, "error": str(exc)}


def _checkout_quantity(checkout_cfg: dict | None) -> int:
    raw = 1
    if isinstance(checkout_cfg, dict) and checkout_cfg.get("quantity") is not None:
        raw = checkout_cfg.get("quantity")
    try:
        qty = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"checkout.quantity must be a whole number (got {raw!r})"
        ) from exc
    if qty < 1:
        raise RuntimeError(f"checkout.quantity must be >= 1 (got {qty})")
    return qty


def _bag_qty_css(found: dict) -> str:
    if found.get("id"):
        return f'select[id="{found["id"]}"]'
    if found.get("autom"):
        return f'select[data-autom="{found["autom"]}"]'
    return 'select[name="quantity"]'


def _bag_qty_live(page, css: str) -> dict:
    """Dropdown value + whether Apple is still saving the bag."""
    try:
        snap = page.evaluate(
            """(css) => {
              const el = document.querySelector(css);
              const btn = document.querySelector('[data-autom="checkout"]');
              const updating = !!document.querySelector(
                '[class*="bag-updating"], [class*="is-updating"], '
                + '.rs-bag-updating, [data-autom*="bag-updating"]'
              );
              const busyBtn = !!(
                btn
                && (btn.disabled
                    || btn.getAttribute('aria-disabled') === 'true'
                    || btn.getAttribute('aria-busy') === 'true')
              );
              return {
                value: el ? String(el.value || '') : '',
                ready: !!(el && btn && !busyBtn && !updating),
                updating: updating || busyBtn,
              };
            }""",
            css,
        )
        return snap if isinstance(snap, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _wait_bag_quantity_committed(
    page, css: str, want: int, *, timeout_sec: float = 8.0
) -> None:
    """Do not click Thanh Toán until Apple has kept the new qty (not just the dropdown)."""
    loc = page.locator(css).first
    t_start = time.time()
    deadline = t_start + timeout_sec
    saw_updating = False
    last = {}
    while time.time() < deadline:
        last = _bag_qty_live(page, css)
        if last.get("updating"):
            saw_updating = True
        if last.get("value") != str(want):
            try:
                loc.select_option(value=str(want), timeout=1_200)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(80)
            continue
        # Value stuck at `want`. If Apple flashed a spinner, wait until it clears.
        # If it never did, hold ~700ms so the cart POST can finish (slow Macs).
        if last.get("ready") and (saw_updating or (time.time() - t_start) >= 0.7):
            held = time.time()
            while time.time() - held < 0.25:
                confirm = _bag_qty_live(page, css)
                if confirm.get("value") != str(want) or not confirm.get("ready"):
                    break
                page.wait_for_timeout(50)
            else:
                log(
                    f"Bag quantity committed at {want} "
                    f"(updating={'Y' if saw_updating else 'N'})"
                )
                return
        page.wait_for_timeout(80)
    raise RuntimeError(
        f"Bag quantity did not stay at {want} before Thanh Toán (last={last!r})"
    )


def set_bag_quantity(page, quantity: int, *, timer: StageTimer | None = None) -> None:
    """Set bag line-item qty and wait until Apple saves it, then caller may checkout."""
    want = int(quantity)
    t0 = time.perf_counter()
    page.locator('[data-autom="checkout"]').first.wait_for(
        state="attached", timeout=8_000
    )
    info = _bag_quantity_select_info(page)
    found = info.get("found") if isinstance(info, dict) else None
    n_real = int((info or {}).get("n") or 0)
    if n_real > 1:
        raise RuntimeError(
            f"Bag has {n_real} line-item quantity dropdowns (leftover items). "
            "Empty the bag and re-run so quantity applies to the SKU just added."
        )
    if not found:
        if want == 1:
            log("Bag quantity control not found — leaving Apple default 1")
            return
        raise RuntimeError(
            f"Bag has no quantity dropdown (want {want}). "
            f"debug={info!r}"
        )
    current = str(found.get("value") or "")
    options = [str(x) for x in (found.get("options") or [])]
    autom = found.get("autom") or found.get("id") or found.get("name") or "quantity"
    log(
        f"Bag quantity now={current} want={want} options={options} "
        f"via={autom}"
    )
    if str(want) not in options:
        raise RuntimeError(
            f"checkout.quantity={want} is not in Apple's bag dropdown {options}. "
            "iPhone is often capped at 2."
        )
    css = _bag_qty_css(found)
    if current != str(want):
        page.locator(css).first.select_option(value=str(want), timeout=3_000)
        log(f"FILL  bag quantity dropdown → {want}")
    _wait_bag_quantity_committed(page, css, want)
    ms = (time.perf_counter() - t0) * 1000
    log(f"Bag quantity ready {want} ({ms:.0f}ms) — Thanh Toán is safe")
    if timer:
        timer.record("8 bag quantity", ms, kind="click")


def click_thanh_toan_now(
    page,
    timer: StageTimer | None = None,
    *,
    quantity: int | None = None,
) -> None:
    """From bag, enter checkout form. Never place the order."""
    timer = timer or StageTimer()
    t_ready = time.perf_counter()
    checkout = page.locator('[data-autom="checkout"]')
    checkout.first.wait_for(state="attached", timeout=5_000)
    log(
        f"WAIT  checkout button attached: "
        f"{(time.perf_counter() - t_ready) * 1000:.0f}ms"
    )
    for attempt in range(3):
        if quantity is not None and int(quantity) > 1:
            live = _bag_quantity_select_info(page).get("found") or {}
            if str(live.get("value") or "") != str(int(quantity)):
                log(
                    f"Thanh Toán blocked — qty still {live.get('value')!r}, "
                    f"want {quantity}. Re-setting first."
                )
                set_bag_quantity(page, int(quantity), timer=timer)
        t_click = time.perf_counter()
        page.evaluate(
            """() => {
              const btn = document.querySelector('[data-autom="checkout"]');
              if (btn) btn.click();
            }"""
        )
        click_ms = (time.perf_counter() - t_click) * 1000
        log(f"CLICK  Thanh Toán / checkout attempt {attempt + 1}: {click_ms:.0f}ms")
        if attempt == 0:
            timer.record("9 click Thanh Toán", click_ms, kind="click")
        t_wait = time.perf_counter()
        try:
            page.wait_for_url(
                re.compile(
                    r".*/(checkout|signIn|signin|shipping|billing|fulfillment).*",
                    re.I,
                ),
                timeout=8_000,
            )
        except Exception:  # noqa: BLE001
            pass
        wait_ms = (time.perf_counter() - t_wait) * 1000
        log(
            f"WAIT  after Thanh Toán click: {wait_ms:.0f}ms → {page.url[:100]}"
        )
        if _is_apple_404(page):
            log(f"Thanh Toán hit Apple 404 (attempt {attempt + 1}) — retry from bag")
            goto_resilient(page, "https://www.apple.com/vn/shop/bag")
            page.locator('[data-autom="checkout"]').first.wait_for(
                state="attached", timeout=8_000
            )
            continue
        # Qty change not saved yet: Apple cancels checkout and dumps us on /bag.
        if "/shop/bag" in (page.url or "").lower() and attempt < 2:
            log(
                "Thanh Toán bounced back to the bag "
                "(quantity likely not saved) — wait and retry"
            )
            if quantity is not None:
                set_bag_quantity(page, int(quantity), timer=timer)
            else:
                page.wait_for_timeout(700)
            continue
        break
    timer.mark("10 checkout-ready page", kind="wait")
    log(f"Checkout-ready at: {page.url}")
    if _is_apple_404(page):
        raise RuntimeError(f"Thanh Toán landed on Apple 404: {page.url}")


# Hard stop — NEVER click place-order / Đặt hàng (review button is allowed)
_CHECKOUT_FORBIDDEN_AUTOM = {
    "placeOrder",
    "place-order",
    "place_order",
}


def _checkout_step(page) -> str:
    try:
        return page.evaluate(
            """() => (location.search.match(/[?&]_s=([^&]+)/) || [])[1] || ''"""
        ) or ""
    except Exception:  # noqa: BLE001
        return ""


def _prefind_automs(page, keys: dict[str, str]) -> dict[str, bool]:
    """Prefind data-autom handles; log Y/N. keys: name -> selector autom value."""
    t0 = time.perf_counter()
    found = page.evaluate(
        """(keys) => {
          const out = {};
          for (const [name, autom] of Object.entries(keys)) {
            out[name] = !!document.querySelector('[data-autom="' + autom + '"]');
          }
          return out;
        }""",
        keys,
    )
    ms = (time.perf_counter() - t0) * 1000
    bits = " ".join(f"{k}={'Y' if found.get(k) else 'N'}" for k in keys)
    log(f"Prefind ({ms:.0f}ms): {bits}")
    return found or {}


def _verify_checked(page, autom: str, *, timeout_ms: int = 5_000) -> None:
    page.wait_for_function(
        """(autom) => {
          const el = document.querySelector('[data-autom="' + autom + '"]');
          return !!(el && (el.checked || el.getAttribute('aria-checked') === 'true'));
        }""",
        arg=autom,
        timeout=timeout_ms,
    )


def _is_forbidden_checkout_autom(autom: str) -> bool:
    a = autom or ""
    if a in _CHECKOUT_FORBIDDEN_AUTOM:
        return True
    # Never allow place-order variants (EN/VN automs)
    return bool(
        re.search(r"placeorder|place-order|place_order|dat.?hang|đặt.?hàng", a, re.I)
    )


def _billing_popup(page):
    """Locator for the Chỉnh Sửa Địa Chỉ overlay (focus here, not background)."""
    # Prefer aria dialog; fall back to Apple's rc-overlay-popup shell.
    dlg = page.locator('[role="dialog"][aria-modal="true"]').filter(
        has=page.locator('[data-autom="address-savebutton"]')
    )
    if dlg.count() > 0:
        return dlg.first
    overlay = page.locator(".rc-overlay-popup").filter(
        has=page.locator('[data-autom="address-savebutton"]')
    )
    if overlay.count() > 0:
        return overlay.first
    save = page.locator('[data-autom="address-savebutton"]')
    if save.count() > 0:
        return page.locator("body")
    return page.locator("body")


# True when Apple is already asking for a billing address (popup, heading,
# visible empty street, Lưu, or required-field errors). Must stay false on a
# machine that already has a card address and goes straight to Đặt hàng.
_BILLING_PROMPT_JS = """() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    return !!(el.offsetParent || el.getClientRects().length);
  };
  const href = location.href || '';
  const step = ((href.match(/[?&]_s=([^&]+)/) || [])[1] || '');
  if (/Shipping/i.test(href) || /Shipping/i.test(step)) return false;

  const titleRe = /chỉnh\\s*sửa\\s*địa\\s*chỉ|edit\\s+address/i;
  const titled = Array.from(document.querySelectorAll(
    'h1, h2, h3, h4, [role="heading"], legend, header, [class*="subheader"], [class*="title"]'
  )).some((h) => vis(h) && titleRe.test((h.innerText || '').trim().slice(0, 80)));
  if (titled) return true;

  const save = Array.from(document.querySelectorAll(
    '[data-autom="address-savebutton"], button'
  )).some((b) => vis(b) && (
    b.getAttribute('data-autom') === 'address-savebutton'
    || /lưu thay đổi|save changes/i.test((b.innerText || '').replace(/\\s+/g, ' ').trim())
  ));
  if (save) return true;

  const dlg = document.querySelector(
    '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
  );
  if (dlg && vis(dlg) && dlg.querySelector(
    '[data-autom="address-savebutton"], input[data-autom="form-field-street"]'
  )) {
    return true;
  }

  const onBilling = /Billing/i.test(href) || /Billing/i.test(step);
  if (!onBilling) return false;

  const emptyStreet = Array.from(
    document.querySelectorAll('input[data-autom="form-field-street"]')
  ).some((el) => vis(el) && el.getBoundingClientRect().height > 12
    && !(el.value || '').trim());
  return emptyStreet;
}"""


def _billing_inline_form_visible(page) -> bool:
    """True only when Apple is actually asking for a billing address on-page."""
    return _billing_prompt_visible(page) and not _billing_address_editor_open(page)


def _billing_prompt_visible(page) -> bool:
    """True only if Apple is already showing a billing address UI to fill."""
    try:
        return bool(page.evaluate(_BILLING_PROMPT_JS))
    except Exception:  # noqa: BLE001
        return False


def _billing_address_editor_open(page) -> bool:
    """True when the Chỉnh Sửa Địa Chỉ popup / editor UI is visible."""
    try:
        return bool(
            page.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) return false;
                    return !!(el.offsetParent || el.getClientRects().length);
                  };
                  const heading = Array.from(
                    document.querySelectorAll('h1, h2, h3, [role="heading"]')
                  ).some((h) => vis(h) && /chỉnh\\s*sửa\\s*địa\\s*chỉ|edit\\s+address/i.test(
                    h.innerText || ''
                  ));
                  if (heading) return true;
                  const save = Array.from(
                    document.querySelectorAll('[data-autom="address-savebutton"]')
                  ).some(vis);
                  if (save) return true;
                  const dlg = document.querySelector(
                    '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
                  );
                  if (!dlg || !vis(dlg)) return false;
                  return !!dlg.querySelector(
                    '[data-autom="address-savebutton"], '
                    + 'select[id*="editSavedBillingAddress"], '
                    + 'input[data-autom="form-field-street"]'
                  );
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _open_billing_address_edit(page) -> None:
    """Click Chỉnh sửa and wait for the Chỉnh Sửa Địa Chỉ popup."""
    if _billing_address_editor_open(page):
        log("Billing address popup already open (Chỉnh Sửa Địa Chỉ)")
        return
    selectors = [
        'button[id*="editBillingAddress"]',
        "button.rf-creditcard-editaddress",
    ]
    last_err: Exception | None = None
    for attempt in range(1, 4):
        if _billing_address_editor_open(page):
            return
        try:
            page.wait_for_function(
                """() => {
                  const el = document.querySelector(
                    'button[id*="editBillingAddress"], button.rf-creditcard-editaddress'
                  );
                  if (!el || el.disabled) return false;
                  return !!(el.offsetParent || el.getClientRects().length);
                }""",
                timeout=3_000,
            )
        except Exception as wait_exc:  # noqa: BLE001
            last_err = wait_exc
            log(f"WAIT  billing edit button attempt {attempt}: {wait_exc}")
        clicked = ""
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.scroll_into_view_if_needed(timeout=2_000)
                loc.click(timeout=3_000)
                clicked = sel
                break
            except Exception as click_exc:  # noqa: BLE001
                last_err = click_exc
                continue
        if not clicked:
            clicked = page.evaluate(
                """() => {
                  const byId = document.querySelector(
                    'button[id*="editBillingAddress"], button.rf-creditcard-editaddress'
                  );
                  if (byId) { byId.click(); return 'dom-id'; }
                  const buttons = Array.from(document.querySelectorAll('button'));
                  const edit = buttons.find((b) => {
                    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    return t === 'chỉnh sửa' || t === 'edit';
                  });
                  if (edit) { edit.click(); return 'dom-text'; }
                  return '';
                }"""
            )
        if not clicked:
            time.sleep(0.35 * attempt)
            continue
        log(f"CLICK  billing Chỉnh sửa via={clicked} attempt={attempt}")
        try:
            page.wait_for_function(
                _BILLING_PROMPT_JS,
                timeout=8_000,
            )
            log("WAIT  Chỉnh Sửa Địa Chỉ editor open")
            return
        except Exception as open_exc:  # noqa: BLE001
            last_err = open_exc
            log(f"WAIT  billing popup mount failed attempt {attempt}: {open_exc}")
            time.sleep(0.35 * attempt)
    detail = f" ({last_err})" if last_err else ""
    raise RuntimeError(
        f"Could not open billing address popup (Chỉnh Sửa Địa Chỉ){detail}"
    )


def _clear_input_autom(page, autom: str) -> None:
    """Clear a text input (needed for bad postal codes on saved billing)."""
    page.evaluate(
        """(autom) => {
          const nodes = Array.from(
            document.querySelectorAll('[data-autom="' + autom + '"]')
          ).filter((el) => el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
          for (const el of nodes) {
            const visible = !!(el.offsetParent || el.getClientRects().length);
            if (!visible && nodes.length > 1) continue;
            const proto = window.HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(el, '');
            else el.value = '';
            const tracker = el._valueTracker;
            if (tracker && typeof tracker.setValue === 'function') tracker.setValue('x');
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }""",
        autom,
    )
    try:
        page.locator(f'input[data-autom="{autom}"]').first.fill("", timeout=1_200)
    except Exception:  # noqa: BLE001
        pass


def _billing_select(page, autom: str):
    """The address-form <select>, not a leftover shipping one."""
    loc = page.locator(f'select[data-autom="{autom}"]')
    idx = int((_pick_select_info(page, autom) or {}).get("idx") or -1)
    if idx >= 0:
        return loc.nth(idx)
    popup = _billing_popup(page)
    inner = popup.locator(f'select[data-autom="{autom}"]')
    if inner.count() > 0:
        return inner.last
    return loc.last if loc.count() else loc.first


def _fill_billing_input(page, autom: str, value: str) -> None:
    """Fill an input inside the billing address popup (fast path)."""
    loc = _billing_popup(page).locator(f'input[data-autom="{autom}"]').first
    loc.wait_for(state="attached", timeout=5_000)
    loc.fill(value, timeout=1_500, force=True)


def _clear_billing_postal(page) -> None:
    """Erase Mã Bưu Điện (không bắt buộc) — bad values like 76165 block review."""
    popup = _billing_popup(page)
    loc = popup.locator('input[data-autom="form-field-postalCode"]').first
    try:
        loc.wait_for(state="attached", timeout=5_000)
    except Exception:  # noqa: BLE001
        log("FILL  billing postal field not visible — skip clear")
        return
    before = ""
    try:
        before = loc.input_value(timeout=1_000)
    except Exception:  # noqa: BLE001
        pass
    if not (before or "").strip():
        log("FILL  billing Mã Bưu Điện already empty")
        return
    try:
        loc.fill("", timeout=2_000, force=True)
    except Exception:  # noqa: BLE001
        pass
    page.evaluate(
        """() => {
          const dlg = document.querySelector(
            '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
          );
          const el = dlg && dlg.querySelector('input[data-autom="form-field-postalCode"]');
          if (!el) return;
          const proto = window.HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          if (desc && desc.set) desc.set.call(el, '');
          else el.value = '';
          const tracker = el._valueTracker;
          if (tracker && typeof tracker.setValue === 'function') tracker.setValue('x');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    after = ""
    try:
        after = loc.input_value(timeout=1_000)
    except Exception:  # noqa: BLE001
        pass
    log(f"FILL  billing Mã Bưu Điện cleared (was={before!r} now={after!r})")


# Prefer the address-form <select> Apple is showing, not a leftover shipping one.
_PICK_SELECT_INFO_JS = """(autom) => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    return true;
  };
  const all = Array.from(document.querySelectorAll(
    'select[data-autom="' + autom + '"]'
  ));
  const dlg = document.querySelector(
    '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
  );
  const dlgVis = !!(dlg && vis(dlg));
  const pool = dlgVis
    ? Array.from(dlg.querySelectorAll('select[data-autom="' + autom + '"]'))
    : all;
  const sel = pool.find(vis) || pool[pool.length - 1]
    || all.find(vis) || all[all.length - 1] || null;
  if (!sel) return { label: '', n: 0, idx: -1 };
  const opt = sel.options[sel.selectedIndex];
  return {
    label: ((opt && opt.textContent) || sel.value || '').trim(),
    n: sel.options.length,
    idx: all.indexOf(sel),
  };
}"""


_SELECT_HAS_WANT_JS = """(args) => {
  const autom = args.autom;
  const want = String(args.want || '').trim().toLowerCase();
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    return true;
  };
  const all = Array.from(document.querySelectorAll(
    'select[data-autom="' + autom + '"]'
  ));
  const dlg = document.querySelector(
    '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
  );
  const dlgVis = !!(dlg && vis(dlg));
  const pool = dlgVis
    ? Array.from(dlg.querySelectorAll('select[data-autom="' + autom + '"]'))
    : all;
  const sel = pool.find(vis) || pool[pool.length - 1]
    || all.find(vis) || all[all.length - 1] || null;
  if (!sel || sel.disabled || !want) return false;
  return Array.from(sel.options).some((o) => {
    const t = (o.textContent || '').trim().toLowerCase();
    return t && (t === want || t.includes(want));
  });
}"""


def _pick_select_info(page, autom: str) -> dict:
    try:
        return page.evaluate(_PICK_SELECT_INFO_JS, autom) or {}
    except Exception:  # noqa: BLE001
        return {}


def _vn_select_already_set(current: str, want: str) -> bool:
    """True when the selected OPTION already is the value we want.

    Do not treat the leftover 'Trước Sáp Nhập' placeholder option as a match,
    and do not re-select just because that placeholder exists in the list.
    Re-selecting a tỉnh that is already set resets quận/phường (~10s each).
    """
    cur = (current or "").strip()
    w = (want or "").strip()
    if not w or not cur:
        return False
    cur_l = cur.lower()
    w_l = w.lower()
    if re.search(r"trước\s*sáp", cur_l) and w_l not in cur_l:
        return False
    if re.fullmatch(
        r"(tỉnh/thành phố|quận/huyện|phường)\s*trước\s*sáp\s*nhập", cur_l
    ):
        return False
    return w_l in cur_l


def _billing_select_current(page, autom: str) -> str:
    return str((_pick_select_info(page, autom) or {}).get("label") or "").strip()


def _select_billing_cascade(
    page,
    autom: str,
    label: str,
    *,
    ajax_token: str = "",
    wait_sec: float = 8.0,
) -> bool:
    """
    Fast tỉnh/quận/phường select inside the popup.

    Do NOT block on long AJAX timeouts — select, then confirm DOM stuck / next
    cascade unlocks. (Waiting 10s for Selectstate often just burned the timeout.)
    """
    if not label:
        return False
    sel = _billing_select(page, autom)
    try:
        sel.wait_for(state="attached", timeout=5_000)
    except Exception:  # noqa: BLE001
        return False

    deadline = time.time() + wait_sec
    matched = ""
    while time.time() < deadline:
        try:
            texts = sel.locator("option").all_text_contents()
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(25)
            continue
        matched = _match_select_option_label(texts, label)
        if matched:
            break
        page.wait_for_timeout(25)
    if not matched:
        return False

    # Fire select; optional short AJAX listen (don't stall the sprint)
    if ajax_token:
        try:
            with page.expect_response(
                lambda r, tok=ajax_token: tok in r.url and r.status == 200,
                timeout=2_500,
            ) as resp_info:
                sel.select_option(label=matched, timeout=1_500)
            _ = resp_info.value
        except Exception:  # noqa: BLE001
            try:
                sel.select_option(label=matched, timeout=1_500)
            except Exception:  # noqa: BLE001
                pass
    else:
        try:
            sel.select_option(label=matched, timeout=1_500)
        except Exception:  # noqa: BLE001
            return False

    settle_deadline = time.time() + 1.2
    while time.time() < settle_deadline:
        cur = _billing_select_current(page, autom)
        cur_l = (cur or "").lower()
        if "trước sáp" in cur_l:
            page.wait_for_timeout(40)
            continue
        if cur_l == matched.lower() or matched.lower() in cur_l or label.lower() in cur_l:
            return True
        page.wait_for_timeout(40)
    cur = _billing_select_current(page, autom)
    log(f"Billing popup {autom} did not stick: want={label!r} got={cur!r}")
    return False


def _wait_billing_select_ready(
    page,
    autom: str,
    *,
    want_label: str = "",
    min_options: int = 2,
    timeout_ms: int = 10_000,
) -> None:
    """Wait until a select inside the billing popup is enabled with options."""
    page.wait_for_function(
        """({ autom, want, minOptions }) => {
          const dlg = document.querySelector(
            '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
          );
          if (!dlg) return false;
          const sel = dlg.querySelector('select[data-autom="' + autom + '"]');
          if (!sel || sel.disabled) return false;
          const opts = Array.from(sel.options).map(
            (o) => (o.textContent || '').trim()
          ).filter(Boolean);
          if (opts.length < minOptions) return false;
          if (!want) return true;
          const w = String(want).toLowerCase();
          return opts.some((t) => t.toLowerCase() === w || t.toLowerCase().includes(w));
        }""",
        arg={"autom": autom, "want": want_label, "minOptions": min_options},
        timeout=timeout_ms,
    )


def _billing_view_address(page) -> dict[str, str]:
    """Read saved-card billing address from the closed (view-mode) card panel."""
    try:
        return (
            page.evaluate(
                """() => {
                  const root = document.querySelector('.rf-creditcard-address')
                    || document.querySelector('.rf-creditcard-savedcard-address');
                  if (!root) {
                    return {
                      first_name: '', last_name: '', street: '', city: '',
                      district: '', postal_code: '', blob: ''
                    };
                  }
                  const dig = (autom) => {
                    const el = root.querySelector('[data-autom="' + autom + '"]');
                    return el ? (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim() : '';
                  };
                  return {
                    first_name: dig('form-field-firstName'),
                    last_name: dig('form-field-lastName'),
                    street: dig('form-field-street'),
                    city: dig('form-field-city'),
                    district: dig('form-field-district'),
                    postal_code: dig('form-field-postalCode'),
                    blob: (root.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 400),
                  };
                }"""
            )
            or {}
        )
    except Exception:  # noqa: BLE001
        return {}


def _billing_address_already_ok(page, addr: dict) -> bool:
    """
    True when view-mode billing already matches config (skip slow popup).
    Same idea as shipping skipping tỉnh/quận when fulfillment pre-seeded them.
    """
    if not isinstance(addr, dict) or not addr:
        return False
    view = _billing_view_address(page)
    blob = (view.get("blob") or "").lower()
    # Bad leftover postal from Apple Account — must open popup to clear
    postal_cfg = (addr.get("postal_code") or "").strip()
    postal_view = (view.get("postal_code") or "").strip()
    if not postal_cfg and postal_view and re.search(r"\d{4,}", postal_view):
        log(f"Billing view still has postal {postal_view!r} — need popup clear")
        return False
    if "76165" in blob or "ben van don" in blob or "ho chi minh city" in blob:
        return False

    def _has(want: str) -> bool:
        w = (want or "").strip().lower()
        if not w:
            return True
        return w in blob or w in (view.get("street") or "").lower()

    street = (addr.get("street") or "").strip()
    city = (addr.get("city") or "").strip()
    district = (addr.get("district") or "").strip()
    # Street is the strongest signal; require it + (city or district)
    if street and not _has(street):
        return False
    if city and not (_has(city) or _has(city.replace("Quận ", ""))):
        return False
    if district and not (_has(district) or _has(district.replace("Phường ", ""))):
        return False
    if street and (city or district):
        log(f"Billing view already OK — skip popup ({street!r} / {city!r})")
        return True
    return False


def _billing_card_incomplete(page, addr: dict) -> bool:
    """True when the saved card will fail review (empty/non-VN billing).

    Used to open Chỉnh sửa *before* the 9s failed-review hop. Must stay false
    when the card already shows a Vietnam address (Mac path).
    """
    try:
        info = page.evaluate(
            """() => {
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) return false;
                return !!(el.offsetParent || el.getClientRects().length);
              };
              const emptyStreet = Array.from(document.querySelectorAll(
                'input[data-autom="form-field-street"]'
              )).some((el) => vis(el) && !(el.value || '').trim());
              const root = document.querySelector(
                '.rf-creditcard-address, .rf-creditcard-savedcard-address, '
                + '.rf-creditcard-carddetails'
              );
              const blob = ((root && vis(root) ? root.innerText : '') || '')
                .replace(/\\s+/g, ' ').trim();
              return { emptyStreet, blob: blob.slice(0, 300) };
            }"""
        ) or {}
    except Exception:  # noqa: BLE001
        return False
    if info.get("emptyStreet"):
        return True
    blob = str(info.get("blob") or "").lower()
    if not blob:
        return False
    street = ((addr or {}).get("street") or "").strip().lower()
    if street and street[:16] in blob:
        return False
    if re.search(r"hồ chí minh|ho chi minh|bình thạnh|binh thanh", blob):
        return False
    # Card panel text has no VN city — Apple will pop the editor after review.
    return True


def _click_billing_same_as_shipping(page) -> bool:
    """Check 'use shipping as billing' if Apple offers it (skips the 20s cascade)."""
    try:
        hit = page.evaluate(
            """() => {
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 4 && r.height > 4;
              };
              const nodes = Array.from(document.querySelectorAll(
                'input[type="checkbox"], [role="checkbox"], label, button'
              ));
              const hit = nodes.find((el) => {
                if (!vis(el)) return false;
                const t = (
                  (el.innerText || '') + ' '
                  + (el.getAttribute('aria-label') || '') + ' '
                  + (el.getAttribute('data-autom') || '') + ' '
                  + (el.id || '')
                ).toLowerCase();
                return /sameasshipping|same-as-shipping|useShippingAddress/i.test(t);
              });
              if (!hit) return '';
              const box = hit.matches('input') ? hit
                : hit.querySelector('input[type="checkbox"]') || hit;
              if (box && box.checked) return 'already';
              hit.click();
              return (hit.getAttribute('data-autom') || hit.id || hit.tagName || 'yes');
            }"""
        )
    except Exception:  # noqa: BLE001
        return False
    if not hit:
        return False
    log(f"CLICK  billing same-as-shipping via={hit}")
    return True


def _visible_autom_input(page, autom: str):
    """The on-screen field, not a leftover hidden shipping input."""
    loc = page.locator(
        f'input[data-autom="{autom}"], textarea[data-autom="{autom}"]'
    )
    chosen = None
    try:
        n = loc.count()
    except Exception:  # noqa: BLE001
        return None
    for i in range(n):
        el = loc.nth(i)
        try:
            box = el.bounding_box()
            if not box or box["height"] < 8 or box["width"] < 8:
                continue
            if not el.is_visible():
                continue
        except Exception:  # noqa: BLE001
            continue
        try:
            val = (el.input_value(timeout=400) or "").strip()
        except Exception:  # noqa: BLE001
            val = ""
        if not val:
            return el
        chosen = el
    return chosen


def _sync_billing_address_from_checkout(
    page,
    addr: dict,
    *,
    timer: StageTimer | None = None,
) -> None:
    """
    Overwrite saved-card BILLING address inside the Chỉnh Sửa Địa Chỉ popup.

    Uses the SAME select helpers as shipping (_select_option_native /
    _select_option_by_label) — one wait loop per field, skip when already set.
    """
    if not isinstance(addr, dict) or not addr:
        log("Billing address sync skipped — no checkout.billing_address")
        return
    if _billing_address_already_ok(page, addr) and not _billing_prompt_visible(page):
        if timer:
            timer.mark("18b billing already OK (skip popup)")
        return

    t0 = time.perf_counter()
    if not _billing_prompt_visible(page):
        log("No billing address prompt — leaving the card address alone")
        return
    popup = _billing_popup(page)
    try:
        popup.wait_for(state="visible", timeout=2_000)
    except Exception:  # noqa: BLE001
        popup = page.locator("body")
    log("FILL  billing editor (visible fields only)")
    _click_billing_same_as_shipping(page)

    first_name = (addr.get("first_name") or "").strip()
    last_name = (addr.get("last_name") or "").strip()
    street = (addr.get("street") or "").strip()
    street2 = (addr.get("street2") or "").strip()
    state = (addr.get("state") or "Thành phố Hồ Chí Minh").strip()
    city = (addr.get("city") or "Quận Bình Thạnh").strip()
    district = (addr.get("district") or "").strip()
    postal = (addr.get("postal_code") or "").strip()

    def _fill_one(autom: str, value: str, label: str) -> None:
        """Same fill style as shipping _fill_one (native + Playwright)."""
        if not value:
            return
        t_f = time.perf_counter()
        _fill_fields_native(page, {autom: value})
        vis_in = _visible_autom_input(page, autom)
        if vis_in is not None:
            try:
                vis_in.fill(str(value), timeout=1_200)
            except Exception:  # noqa: BLE001
                try:
                    vis_in.fill(str(value), timeout=800, force=True)
                except Exception:  # noqa: BLE001
                    pass
        log(f"Filled billing {label} ({(time.perf_counter() - t_f) * 1000:.0f}ms)")

    def _skip_or_select(autom: str, want: str, pretty: str, *, wait_sec: float = 4.0) -> bool:
        """Leave quận/phường alone when the visible select already matches.

        Returns True if we changed the select (caller should give the next
        cascade field more time). Returns False if we skipped.
        """
        t_sel = time.perf_counter()
        info = _pick_select_info(page, autom)
        if int(info.get("idx") or -1) < 0 and page.locator(
            f'select[data-autom="{autom}"]'
        ).count() == 0:
            log(f"No billing {pretty} select — skip")
            return False
        cur = str(info.get("label") or "").strip()
        if _vn_select_already_set(cur, want):
            log(f"Billing {pretty} already set: {cur}")
            if timer:
                timer.since(t_sel, f"18b {pretty} already set (skip)", kind="fill")
            return False
        # One wait for Apple's cascade, then one select — do not stack
        # cascade + native + by_label each with the same 12s budget.
        try:
            _wait_js_heartbeat(
                page,
                _SELECT_HAS_WANT_JS,
                arg={"autom": autom, "want": want},
                label=f"billing {pretty} options",
                timeout_ms=max(800, int(wait_sec * 1000)),
                snapshot_js=_SNAP_CHECKOUT,
            )
        except Exception as wait_exc:  # noqa: BLE001
            log(f"WAIT  billing {pretty} options: {wait_exc}")
        ok = _select_option_by_label(
            page, autom, want, wait_sec=2.0, prefer_visible=True
        )
        if not ok:
            ok = _select_option_native(
                page, autom, want, wait_sec=2.0, prefer_visible=True
            )
        if not ok:
            ok = _select_billing_cascade(page, autom, want, wait_sec=2.0)
        if not ok:
            raise RuntimeError(f"Billing {pretty} select failed: {want!r} (was {cur!r})")
        log(f"Selected billing {pretty}: {want}")
        if timer:
            timer.since(t_sel, f"18b select {pretty}", kind="fill")
        return True

    # Text first — then skip dropdowns that Apple already filled (do NOT
    # re-select tỉnh just because a leftover 'Trước Sáp Nhập' option exists;
    # that resets quận/phường and costs ~10s each).
    _fill_one("form-field-firstName", first_name, "firstName")
    _fill_one("form-field-lastName", last_name, "lastName")
    _fill_one("form-field-street", street, "street")
    if street2:
        _fill_one("form-field-street2", street2, "street2")
    if postal:
        _fill_one("form-field-postalCode", postal, "postalCode")
    else:
        try:
            _clear_billing_postal(page)
        except Exception:  # noqa: BLE001
            log("FILL  billing postal clear skipped (no popup field)")

    state_changed = _skip_or_select(
        "form-field-state", state, "state/tỉnh", wait_sec=4.0
    )
    city_changed = _skip_or_select(
        "form-field-city",
        city,
        "city/quận",
        wait_sec=12.0 if state_changed else 4.0,
    )
    if district:
        _skip_or_select(
            "form-field-district",
            district,
            "phường",
            wait_sec=12.0 if city_changed else 4.0,
        )

    # Save if Apple put a Lưu button (popup). Inline payment-page fields have none.
    t_save = time.perf_counter()
    save_btn = popup.locator('[data-autom="address-savebutton"]').first
    has_save = False
    try:
        has_save = save_btn.count() > 0 and save_btn.is_visible()
    except Exception:  # noqa: BLE001
        has_save = False
    if not has_save:
        try:
            alt = page.locator('[data-autom="address-savebutton"]').first
            has_save = alt.count() > 0 and alt.is_visible()
            if has_save:
                save_btn = alt
        except Exception:  # noqa: BLE001
            has_save = False
    if not has_save:
        try:
            by_text = page.locator("button").filter(
                has_text=re.compile(r"Lưu Thay Đổi|Save Changes", re.I)
            )
            if by_text.count() > 0 and by_text.first.is_visible():
                save_btn = by_text.first
                has_save = True
        except Exception:  # noqa: BLE001
            has_save = False
    if not has_save:
        log("No Lưu Thay Đổi button — billing fields stay on the payment form")
        if timer:
            timer.since(t0, "18b sync billing address", kind="fill")
        log(f"Billing address sync done ({(time.perf_counter() - t0) * 1000:.0f}ms)")
        return
    save_btn.click(timeout=5_000)
    log(
        f"CLICK  Lưu Thay Đổi (billing popup): "
        f"{(time.perf_counter() - t_save) * 1000:.0f}ms"
    )
    try:
        page.wait_for_function(
            """() => {
              const dlg = document.querySelector(
                '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
              );
              if (!dlg) return true;
              const r = dlg.getBoundingClientRect();
              return r.width < 2 || r.height < 2
                || !dlg.querySelector('[data-autom="address-savebutton"]');
            }""",
            timeout=12_000,
        )
        log("WAIT  Chỉnh Sửa Địa Chỉ popup closed")
    except Exception:  # noqa: BLE001
        errs = page.evaluate(
            """() => {
              const dlg = document.querySelector(
                '[role="dialog"][aria-modal="true"], .rc-overlay-popup'
              );
              const root = dlg || document;
              return Array.from(root.querySelectorAll(
                '.form-message, [aria-invalid="true"]'
              )).map((e) => (e.innerText || '').replace(/\\s+/g,' ').trim())
                .filter((t) => t && !/trước sáp nhập/i.test(t))
                .filter(Boolean).slice(0, 6);
            }"""
        )
        if errs:
            raise RuntimeError(f"Billing popup save still has errors: {errs!r}")
    if timer:
        timer.since(t0, "18b sync billing address", kind="fill")
    log(f"Billing address sync done ({(time.perf_counter() - t0) * 1000:.0f}ms)")


def _fill_cvv(
    page,
    cvv: str,
    *,
    timer: StageTimer | None = None,
    mount_timeout_ms: int = 20_000,
) -> None:
    """
    Fill saved-card security code (data-autom=security-code-input).
    Call this BEFORE opening the Chỉnh Sửa Địa Chỉ popup (popup covers CVV).
    """
    autom = "security-code-input"
    t_mount = time.perf_counter()
    cvv_present = False
    try:
        page.wait_for_function(
            """() => {
              const el = document.querySelector('[data-autom="security-code-input"]');
              if (!el || el.disabled) return false;
              return !!(el.offsetParent || el.getClientRects().length);
            }""",
            timeout=mount_timeout_ms,
        )
        cvv_present = True
    except Exception:  # noqa: BLE001
        cvv_present = False
    if not cvv_present:
        log(
            f"WAIT  no CVV field after {(time.perf_counter() - t_mount) * 1000:.0f}ms "
            "— continuing (Apple sometimes skips CVV after billing edit)"
        )
        if timer:
            timer.since(t_mount, "19 CVV field absent (skip)", kind="wait")
        return
    log(
        f"WAIT  CVV field mounted: "
        f"{(time.perf_counter() - t_mount) * 1000:.0f}ms"
    )
    _prefind_automs(page, {"cvv": autom, "reviewBtn": "continue-button-review"})

    cvv = re.sub(r"\D", "", (cvv or "").strip())
    t0 = time.perf_counter()
    if cvv:
        # Already filled? skip
        have = page.evaluate(
            """(a) => {
              const el = document.querySelector('[data-autom="' + a + '"]');
              return el ? (el.value || '').replace(/\\D/g, '') : '';
            }""",
            autom,
        )
        if have == cvv:
            log(f"FILL  CVV already set (len={len(cvv)})")
            if timer:
                timer.since(t0, "19 fill CVV (already set)", kind="fill")
            return
        _fill_fields_native(page, {autom: cvv})
        try:
            page.locator(f'[data-autom="{autom}"]').first.fill(
                cvv, timeout=1_500, force=True
            )
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_function(
            """(args) => {
              const el = document.querySelector('[data-autom="' + args.autom + '"]');
              if (!el) return false;
              const v = (el.value || '').replace(/\\D/g, '');
              return v.length >= 3 && v === args.cvv;
            }""",
            arg={"autom": autom, "cvv": cvv},
            timeout=4_000,
        )
        log(f"FILL  CVV via config ({(time.perf_counter() - t0) * 1000:.0f}ms, len={len(cvv)})")
    else:
        log("USER PICK  type CVV in Chrome NOW (security-code-input)…")
        beep()
        notify_macos("Assist — enter CVV", "Type CVV on the billing page — then we continue.")
        page.wait_for_function(
            """() => {
              const el = document.querySelector('[data-autom="security-code-input"]');
              if (!el) return false;
              return (el.value || '').replace(/\\D/g, '').length >= 3;
            }""",
            timeout=45_000,
        )
        log(f"USER PICK  CVV present ({(time.perf_counter() - t0) * 1000:.0f}ms)")
    if timer:
        timer.since(t0, "19 fill CVV (verified)", kind="fill")


def _click_review_and_stop_at_place_order(
    page, *, timer: StageTimer | None = None
) -> None:
    """
    Click "Xem Lại Đơn Hàng Của Bạn" → wait until Đặt hàng / placeOrder is visible.
    NEVER clicks place order.
    """
    timer = timer or StageTimer()
    # Ensure review enabled
    t_en = time.perf_counter()
    page.wait_for_function(
        """() => {
          const btn = document.querySelector('[data-autom="continue-button-review"]');
          return !!(btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true');
        }""",
        timeout=10_000,
    )
    timer.since(t_en, "19b review button enabled", kind="wait")

    review_ok = False
    t_phase = time.perf_counter()
    for attempt in range(3):
        try:
            click_ms = _click_autom(page, "continue-button-review", timeout_ms=5_000)
            if attempt == 0:
                timer.record(
                    "20 click Xem Lại Đơn Hàng", click_ms, kind="click"
                )
        except Exception as exc:  # noqa: BLE001
            log(f"Review click failed (attempt {attempt + 1}): {exc}")
            page.wait_for_timeout(150)
            continue
        try:
            _wait_js_heartbeat(
                page,
                """() => {
                  const place = document.querySelector(
                    '[data-autom="placeOrder"], [data-autom="place-order"], '
                    + '#rs-checkout-place-order-button, button[id*="place-order"]'
                  );
                  if (place) return true;
                  const buttons = Array.from(document.querySelectorAll('button'));
                  return buttons.some((b) => {
                    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    return t === 'đặt hàng' || t === 'place order' || t.startsWith('đặt hàng');
                  });
                }""",
                # Apple's graviton hops in this checkout all land ~10s (Shipping→Billing,
                # CVV panel mount, this one). A 5s first budget guaranteed a timeout and
                # made us re-submit the review form while Apple was still working.
                label=f"Apple hop Review→Đặt hàng (attempt {attempt + 1})",
                timeout_ms=25_000 if attempt == 0 else 15_000,
                snapshot_js=_SNAP_CHECKOUT,
                abort_js=_BILLING_PROMPT_JS,
            )
            review_ok = True
            break
        except CheckoutBlocked as blocked:
            errs = page.evaluate(
                """() => Array.from(document.querySelectorAll(
                  '[class*="error"], [aria-invalid="true"], .form-message-error, [role="alert"]'
                )).map((e) => (e.innerText || '').replace(/\\s+/g,' ').trim())
                  .filter(Boolean).slice(0, 8)"""
            )
            log(
                f"WAIT  review blocked by billing prompt "
                f"(attempt {attempt + 1}, _s={_checkout_step(page)}): {blocked}"
                + (f" errors={errs!r}" if errs else "")
            )
            raise RuntimeError(
                "Did not reach place-order page after review "
                f"(url={page.url}, _s={_checkout_step(page)}, billing address prompt)"
                + (f" errors={errs!r}" if errs else "")
            ) from blocked
        except Exception as hop_exc:  # noqa: BLE001
            errs = page.evaluate(
                """() => Array.from(document.querySelectorAll(
                  '[class*="error"], [aria-invalid="true"], .form-message-error, [role="alert"]'
                )).map((e) => (e.innerText || '').replace(/\\s+/g,' ').trim())
                  .filter(Boolean).slice(0, 8)"""
            )
            log(
                f"WAIT  place-order not reached (attempt {attempt + 1}, "
                f"_s={_checkout_step(page)}, url={page.url[:90]}): {hop_exc}"
                + (f" errors={errs!r}" if errs else "")
            )
            page.wait_for_timeout(200)

    if not review_ok:
        errs = page.evaluate(
            """() => Array.from(document.querySelectorAll(
              '[class*="error"], [aria-invalid="true"], .form-message-error, [role="alert"]'
            )).map((e) => (e.innerText || '').replace(/\\s+/g,' ').trim())
              .filter(Boolean).slice(0, 8)"""
        )
        raise RuntimeError(
            f"Did not reach place-order page after review "
            f"(url={page.url}, _s={_checkout_step(page)})"
            + (f" errors={errs!r}" if errs else "")
        )
    timer.since(t_phase, "21 wait place-order page", kind="wait")

    # Prefind place-order (must exist) — refuse to click it
    place_info = page.evaluate(
        """() => {
          const byAutom = document.querySelector(
            '[data-autom="placeOrder"], [data-autom="place-order"]'
          );
          const byText = Array.from(document.querySelectorAll('button')).find((b) => {
            const t = (b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            return t === 'đặt hàng' || t === 'place order' || t.startsWith('đặt hàng');
          });
          const el = byAutom || byText;
          if (!el) return null;
          return {
            autom: el.getAttribute('data-autom') || '',
            text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
            disabled: !!el.disabled,
          };
        }"""
    )
    log(
        f"Place-order control ready (NOT clicking): {place_info!r} "
        f"_s={_checkout_step(page)} url={page.url}"
    )
    # Bring Đặt Hàng into view so the user sees the stop point
    try:
        place_loc = page.locator(
            '[data-autom="placeOrder"], [data-autom="place-order"], '
            '#rs-checkout-place-order-button'
        )
        if place_loc.count() == 0:
            place_loc = page.get_by_role("button", name=re.compile(r"đặt hàng", re.I))
        place_loc.first.scroll_into_view_if_needed(timeout=3_000)
        log("SCROLL  Đặt Hàng button into view (not clicking)")
    except Exception as scroll_exc:  # noqa: BLE001
        log(f"SCROLL  Đặt Hàng skipped: {scroll_exc}")
    log("DRY-RUN STOP — at Đặt hàng. Do NOT click place order.")


def _click_autom(page, autom: str, *, timeout_ms: int = 8_000) -> float:
    """Click a data-autom control. Returns total wall ms (attach wait + click)."""
    if _is_forbidden_checkout_autom(autom):
        raise RuntimeError(f"Refusing to click forbidden control: {autom}")
    t0 = time.perf_counter()
    page.locator(f'[data-autom="{autom}"]').first.wait_for(
        state="attached", timeout=timeout_ms
    )
    attach_ms = (time.perf_counter() - t0) * 1000
    t_click = time.perf_counter()
    page.evaluate(
        """(autom) => {
          const el = document.querySelector('[data-autom="' + autom + '"]');
          if (!el) throw new Error('missing ' + autom);
          el.scrollIntoView({ block: 'center', inline: 'nearest' });
          const label = el.id
            ? document.querySelector('label[for="' + el.id + '"]')
            : null;
          (label || el).click();
        }""",
        autom,
    )
    click_ms = (time.perf_counter() - t_click) * 1000
    total = (time.perf_counter() - t0) * 1000
    log(
        f"CLICK  [{autom}] attach={attach_ms:.0f}ms click={click_ms:.0f}ms "
        f"total={total:.0f}ms"
    )
    return total


def wait_checkout_signin_if_needed(page, login_timeout_sec: int = 300) -> None:
    if looks_like_signin(page.url, page.title()):
        if is_silent_store_sso(page.url) and not is_interactive_signin(page):
            log("Checkout SSO hop (silent /signIn?ssi=) — waiting for auto-complete…")
        else:
            log("Checkout SSO — complete Apple ID in Chrome if prompted.")
        if not wait_for_signin(page, login_timeout_sec):
            raise RuntimeError("Timed out waiting for checkout sign-in")
        log(f"Checkout session ready: {page.url}")


def _bag_has_checkout(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => !!document.querySelector('[data-autom="checkout"]')"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _bag_is_empty(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => {
                  if (document.querySelector('[data-autom="bag-empty-continueshopping-button"]')) {
                    return true;
                  }
                  if (document.querySelector('[data-autom="checkout"]')) return false;
                  const t = (document.body && document.body.innerText) || '';
                  return /giỏ hàng của bạn đang trống|your bag is empty/i.test(t);
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _wait_bag_empty(page, *, timeout_ms: int = 4_000, reload_after_ms: int = 1_500) -> bool:
    """
    Poll for the empty-bag marker instead of trusting one instant read.

    Apple re-renders the bag a beat after the last removal. In that window the
    DOM has no remove control yet still shows the checkout button and no
    "giỏ hàng của bạn đang trống" text, so a single immediate check reads as
    "still full" and aborted the run at step zero. Reload once mid-wait because
    the bag occasionally needs a fresh fetch to drop a stale checkout button.
    """
    t0 = time.perf_counter()
    reloaded = False
    while (time.perf_counter() - t0) * 1000 < timeout_ms:
        if _bag_is_empty(page):
            return True
        if not reloaded and (time.perf_counter() - t0) * 1000 >= reload_after_ms:
            reloaded = True
            log("Bag not settled — reloading bag page once")
            try:
                goto_resilient(page, "https://www.apple.com/vn/shop/bag")
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(150)
    return _bag_is_empty(page)


def empty_bag(page, *, max_rounds: int = 12, reason: str = "start clean") -> None:
    """Remove every line item from /vn/shop/bag so the timed run starts clean."""
    goto_resilient(page, "https://www.apple.com/vn/shop/bag")
    if _bag_is_empty(page):
        log("Bag already empty")
        return

    log(f"Emptying bag ({reason})…")
    for round_i in range(1, max_rounds + 1):
        if _bag_is_empty(page):
            break
        # Prefer Apple's bag remove autom (label is "Xóa <product name>", not bare "Xóa")
        try:
            btn = page.locator('[data-autom="bag-item-remove-button"]').first
            if btn.count() > 0:
                btn.click(force=True, timeout=2_500, no_wait_after=True)
                page.wait_for_timeout(900)
                if "/shop/bag" not in page.url.lower():
                    goto_resilient(page, "https://www.apple.com/vn/shop/bag")
                    page.wait_for_timeout(400)
                continue
        except Exception:  # noqa: BLE001
            pass

        removed = page.evaluate(
            """() => {
              const preferred = document.querySelector(
                '[data-autom="bag-item-remove-button"]'
              );
              if (preferred) { preferred.click(); return 'autom'; }
              const pick = [
                ...document.querySelectorAll(
                  '[data-autom*="remove"], [data-autom*="Remove"], [data-autom*="deleteItem"]'
                )
              ].filter((el) => {
                const t = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                // skip search-clear "Xóa tìm kiếm"
                return !/tìm kiếm|search/i.test(t);
              });
              const byText = [...document.querySelectorAll('button, a')].filter((el) => {
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                return /^xóa\\b/i.test(t) || /^remove\\b/i.test(t);
              });
              const el = pick.find((e) => e.offsetParent !== null)
                || byText.find((e) => e.offsetParent !== null);
              if (!el) return '';
              el.click();
              return 'fallback';
            }"""
        )
        if not removed:
            log(f"WARNING: could not find bag remove control (round {round_i})")
            # Apple hides the Xóa button while the line is re-rendering. Checkout
            # still present means the item is still there — wait and try again
            # instead of giving up mid-update.
            if _bag_has_checkout(page) or not _bag_is_empty(page):
                page.wait_for_timeout(800)
                continue
            break
        page.wait_for_timeout(900)
        if "/shop/bag" not in page.url.lower():
            goto_resilient(page, "https://www.apple.com/vn/shop/bag")
            page.wait_for_timeout(400)

    if _wait_bag_empty(page):
        log("Bag emptied — ready for timed run")
    else:
        # Last resort: still no empty marker but checkout gone
        if not _bag_has_checkout(page):
            log("Bag looks clear (no checkout button)")
        else:
            leftover = ""
            try:
                leftover = page.evaluate(
                    """() => {
                      const n = document.querySelector('[data-autom="bag-item-name"]');
                      return n ? (n.innerText || '').replace(/\\s+/g, ' ').trim() : '';
                    }"""
                )
            except Exception:  # noqa: BLE001
                leftover = ""
            hint = f" (still in bag: {leftover})" if leftover else ""
            raise RuntimeError(
                "Could not empty bag — click Xóa in Chrome, then re-run"
                f"{hint}"
            )


def _add_product_to_bag_untimed(
    page,
    *,
    product_url: str,
    no_trade: str,
    no_care: str,
    timeout_ms: int,
) -> None:
    """Declines + add + bag (untimed) — used only for SSO warm."""
    open_product_page(page, product_url)
    page.locator(
        '#noTradeIn, [data-autom="choose-noTradeIn"], input[value="noTradeIn"]'
    ).first.wait_for(state="attached", timeout=min(timeout_ms, 20_000))
    warm_timer = StageTimer()
    select_declines(page, no_trade, no_care, timeout_ms, warm_timer)
    click_xem_gio_hang_now(page, warm_timer)


def warm_checkout_sso(
    page,
    *,
    product_url: str,
    no_trade: str,
    no_care: str,
    login_timeout_sec: int = 300,
    timeout_ms: int = 45_000,
    checkout_cfg: dict | None = None,
) -> None:
    """
    Pre-warm secure*.store.apple.com checkout SSO (separate from account login).

    Launch-day reality: basket starts empty. So warm must:
      1) clear bag
      2) add one practice iPhone
      3) Thanh Toán → pass /signIn to Fulfillment (sets checkout SSO cookies)
      4) put the delivery address on the Apple account (saved match, else type new)
      5) empty bag again
    Does not touch the card — you save that on the Apple account yourself.
    Timed sprint then starts with an empty bag + warm SSO + address already there.
    """
    log("Pre-warm checkout SSO (secure store) — not timed…")
    log("Plan: empty bag → add iPhone → checkout SSO → delivery address → empty bag")
    t0 = time.perf_counter()

    # Start clean so we don't stack leftovers from prior practice
    empty_bag(page, reason="SSO warm start")

    log("Adding practice iPhone for SSO warm…")
    _add_product_to_bag_untimed(
        page,
        product_url=product_url,
        no_trade=no_trade,
        no_care=no_care,
        timeout_ms=timeout_ms,
    )
    if not _bag_has_checkout(page):
        goto_resilient(page, "https://www.apple.com/vn/shop/bag")
        page.wait_for_timeout(500)
    if not _bag_has_checkout(page):
        raise RuntimeError("SSO warm: bag has no Thanh Toán after product add")
    if _is_apple_404(page):
        raise RuntimeError("Checkout SSO warm aborted — bag/product hit Apple 404")

    click_thanh_toan_now(page, StageTimer())
    if _is_apple_404(page):
        raise RuntimeError("Checkout SSO warm failed — Thanh Toán returned Apple 404")
    wait_checkout_signin_if_needed(page, login_timeout_sec)

    try:
        page.wait_for_function(
            """() => {
              const u = location.href;
              if (/\\/shop\\/404\\b/i.test(u)) return false;
              return u.includes('Fulfillment') || u.includes('Shipping')
                || u.includes('Billing')
                || (u.includes('/checkout') && !u.includes('/shop/404'));
            }""",
            timeout=15_000,
        )
    except Exception:  # noqa: BLE001
        pass

    if _is_apple_404(page):
        raise RuntimeError("Checkout SSO warm failed — Apple 404")
    if looks_like_signin(page.url, page.title()):
        raise RuntimeError("Checkout SSO warm failed — still on sign-in")
    step = _checkout_step(page)
    if not any(
        s in page.url or s in step
        for s in ("Fulfillment", "Shipping", "Billing", "checkout")
    ):
        raise RuntimeError(f"Checkout SSO warm failed — unexpected url={page.url}")

    ms = (time.perf_counter() - t0) * 1000
    log(f"Checkout SSO cookies warm OK in {ms:.0f}ms → {page.url} (_s={step})")

    log("Warm: putting delivery address in place (card is yours — we do not type it)…")
    advance_checkout_to_payment(
        page,
        StageTimer(),
        login_timeout_sec=login_timeout_sec,
        checkout_cfg=checkout_cfg or {},
        until="shipping",
    )

    # Critical: leave basket empty for the real timed run
    empty_bag(page, reason="SSO warm done")
    log("SSO warm complete — bag empty, address saved, session kept. Ready for T-0.")


def _match_select_option_label(texts: list[str], label: str) -> str | None:
    """Pick best option label: exact > numbered-phường exact > whole-label substring."""
    want = label.strip().lower()
    if not want:
        return None
    cleaned = [(t or "").strip() for t in texts if (t or "").strip()]
    for tt in cleaned:
        if tt.lower() == want:
            return tt
    # "Phường 11" must not match "Phường 1" via loose substring
    m = re.fullmatch(r"(phường|phuong)\s*(\d+)", want, flags=re.I)
    if m:
        num = m.group(2)
        pat = re.compile(rf"^(phường|phuong)\s*{re.escape(num)}$", re.I)
        for tt in cleaned:
            if pat.match(tt):
                return tt
        return None
    for tt in cleaned:
        low = tt.lower()
        if want in low or low in want:
            return tt
    return None


def _select_option_by_label(
    page,
    autom: str,
    label: str,
    *,
    wait_sec: float = 12.0,
    poll_ms: int = 50,
    prefer_visible: bool = False,
) -> bool:
    """Select a <select data-autom=...> option by visible label.

    Uses Playwright select_option — React-controlled Apple checkout ignores
    synthetic input/change events on .value alone.
    """
    if not label:
        return False

    def _loc():
        loc = page.locator(f'select[data-autom="{autom}"]')
        if prefer_visible:
            idx = int((_pick_select_info(page, autom) or {}).get("idx") or -1)
            if idx >= 0:
                return loc.nth(idx)
            if loc.count() > 0:
                return loc.last
        return loc.first

    sel = _loc()
    try:
        sel.wait_for(state="attached", timeout=5_000)
    except Exception:  # noqa: BLE001
        return False

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        try:
            # Re-query each attempt — Apple remounts selects after cascade changes
            sel = _loc()
            sel.wait_for(state="attached", timeout=1_000)
            texts = sel.locator("option").all_text_contents()
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(poll_ms)
            continue
        matched = _match_select_option_label(texts, label)
        if not matched:
            page.wait_for_timeout(poll_ms)
            continue
        try:
            sel.select_option(label=matched, timeout=1_500)
        except Exception:  # noqa: BLE001
            try:
                sel.select_option(value=matched, timeout=1_200)
            except Exception:  # noqa: BLE001
                page.wait_for_timeout(poll_ms)
                continue
        # Confirm value stuck (React sometimes ignores a flaky select)
        try:
            current = (_pick_select_info(page, autom) if prefer_visible else None)
            if current is not None:
                current = current.get("label") or ""
            else:
                current = page.evaluate(
                    """(autom) => {
                      const s = document.querySelector('select[data-autom="' + autom + '"]');
                      if (!s) return '';
                      const opt = s.options[s.selectedIndex];
                      return ((opt && opt.textContent) || s.value || '').trim();
                    }""",
                    autom,
                )
        except Exception:  # noqa: BLE001
            current = ""
        cur_l = (current or "").lower()
        if cur_l == matched.lower() or matched.lower() in cur_l or label.strip().lower() in cur_l:
            return True
        page.wait_for_timeout(poll_ms)
    return False


def _fill_autom(page, autom: str, value: str) -> None:
    if value is None:
        return
    page.locator(f'[data-autom="{autom}"]').first.wait_for(state="attached", timeout=5_000)
    page.fill(f'[data-autom="{autom}"]', str(value), timeout=3_000)


def _fill_fields_native(page, mapping: dict[str, str]) -> dict[str, str]:
    """Batch-fill inputs via native value setter (same spirit as pre-SSO in-page clicks)."""
    clean = {k: str(v).strip() for k, v in mapping.items() if v}
    if not clean:
        return {}
    return (
        page.evaluate(
            """(mapping) => {
              const setVal = (el, value) => {
                const proto = el.tagName === 'TEXTAREA'
                  ? window.HTMLTextAreaElement.prototype
                  : window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                const tracker = el._valueTracker;
                if (tracker && typeof tracker.setValue === 'function') {
                  tracker.setValue('');
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              };
              const out = {};
              for (const [autom, value] of Object.entries(mapping)) {
                // Only real inputs — Apple also stamps data-autom on read-only <span>s
                const nodes = Array.from(
                  document.querySelectorAll(
                    'input[data-autom="' + autom + '"], textarea[data-autom="' + autom + '"]'
                  )
                );
                let stuck = '';
                for (const el of nodes) {
                  // Prefer visible; still try others (Apple duplicates contact fields)
                  const visible = !!(el.offsetParent || el.getClientRects().length);
                  if (!visible && nodes.length > 1) continue;
                  setVal(el, value);
                  stuck = (el.value || '').trim();
                }
                if (!stuck && nodes[0]) {
                  setVal(nodes[0], value);
                  stuck = (nodes[0].value || '').trim();
                }
                out[autom] = stuck;
              }
              return out;
            }""",
            clean,
        )
        or {}
    )


def _select_option_native(
    page,
    autom: str,
    label: str,
    *,
    wait_sec: float = 12.0,
    prefer_visible: bool = False,
) -> bool:
    """Select option in-page (native setter). Falls back to Playwright select_option."""
    if not label:
        return False
    want = label.strip()
    deadline = time.time() + wait_sec
    saw_option = False
    while time.time() < deadline:
        idx = -1
        if prefer_visible:
            idx = int((_pick_select_info(page, autom) or {}).get("idx") or -1)
        status = page.evaluate(
            """({ autom, label, idx }) => {
              const all = document.querySelectorAll('select[data-autom="' + autom + '"]');
              const sel = (idx >= 0 && all[idx]) ? all[idx] : all[0];
              if (!sel) return { ok: false, found: false };
              const want = String(label).trim().toLowerCase();
              const opts = Array.from(sel.options);
              let hit = opts.find((o) => (o.textContent || '').trim().toLowerCase() === want);
              if (!hit) {
                const m = want.match(/^(phường|phuong)\\s*(\\d+)$/i);
                if (m) {
                  const re = new RegExp('^(phường|phuong)\\\\s*' + m[2] + '$', 'i');
                  hit = opts.find((o) => re.test((o.textContent || '').trim()));
                } else {
                  hit = opts.find((o) => {
                    const t = (o.textContent || '').trim().toLowerCase();
                    return !!t && (t.includes(want) || (want.length > 3 && want.includes(t)));
                  });
                }
              }
              if (!hit) return { ok: false, found: false };
              const desc = Object.getOwnPropertyDescriptor(
                window.HTMLSelectElement.prototype, 'value'
              );
              if (desc && desc.set) desc.set.call(sel, hit.value);
              else sel.value = hit.value;
              sel.dispatchEvent(new Event('input', { bubbles: true }));
              sel.dispatchEvent(new Event('change', { bubbles: true }));
              const cur = (sel.options[sel.selectedIndex]?.textContent || sel.value || '').trim().toLowerCase();
              const hitT = (hit.textContent || '').trim().toLowerCase();
              return { ok: cur === hitT || cur.includes(want) || cur === String(hit.value).toLowerCase(), found: true };
            }""",
            {"autom": autom, "label": want, "idx": idx},
        ) or {}
        if status.get("ok"):
            return True
        if status.get("found"):
            saw_option = True
            # Option exists but React ignored native setter — Playwright once
            if _select_option_by_label(
                page,
                autom,
                want,
                wait_sec=1.5,
                poll_ms=40,
                prefer_visible=prefer_visible,
            ):
                return True
        page.wait_for_timeout(40)
    if saw_option:
        return _select_option_by_label(
            page,
            autom,
            want,
            wait_sec=2.0,
            poll_ms=40,
            prefer_visible=prefer_visible,
        )
    return False


def _select_current_label(page, autom: str) -> str:
    try:
        return (
            page.evaluate(
                """(autom) => {
                  const s = document.querySelector('select[data-autom="' + autom + '"]');
                  if (!s) return '';
                  return ((s.options[s.selectedIndex] && s.options[s.selectedIndex].textContent)
                    || s.value || '').trim();
                }""",
                autom,
            )
            or ""
        )
    except Exception:  # noqa: BLE001
        return ""


def _fill_contact_fields(page, autom: str, value: str) -> str:
    """Fill contact inputs in-page; return the value that stuck."""
    if not value:
        return ""
    stuck_map = _fill_fields_native(page, {autom: value})
    stuck = (stuck_map.get(autom) or "").strip()
    if stuck.lower() == value.strip().lower():
        return stuck
    # Masked Apple fields sometimes need a Playwright fill pass
    try:
        loc = page.locator(f'[data-autom="{autom}"]')
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                el.fill(value, timeout=1_500)
                got = (el.input_value(timeout=800) or "").strip()
                if got.lower() == value.strip().lower():
                    return got
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return stuck


def _click_visible_text(page, *candidates: str) -> str | None:
    """Click first visible matching text; return which candidate hit."""
    for candidate in candidates:
        if not candidate:
            continue
        try:
            loc = page.get_by_text(candidate, exact=False)
            n = loc.count()
            for i in range(min(n, 8)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    el.click(force=True, timeout=1_500, no_wait_after=True)
                    return candidate
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _fulfillment_shown_location(page) -> str:
    """Visible 'Giao hàng đến' / zip-edit label (Apple often city-only, no quận)."""
    try:
        return page.evaluate(
            """() => {
              const el = document.querySelector('[data-autom="checkout-zipcode-edit"]');
              const t = ((el && (el.innerText || el.textContent)) || '')
                .replace(/\\s+/g, ' ').trim();
              const body = (document.body && document.body.innerText) || '';
              const m = body.match(/Giao hàng đến[:\\s]+([^\\n]+)/i);
              const extra = m ? m[1].replace(/\\s+/g, ' ').trim() : '';
              return (t + ' ' + extra).replace(/\\s+/g, ' ').trim();
            }"""
        ) or ""
    except Exception:  # noqa: BLE001
        return ""


def _fulfillment_continue_ready(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => {
                  const btn = document.querySelector(
                    '[data-autom="fulfillment-continue-button"]'
                  );
                  const opt = document.querySelector(
                    'input[data-autom^="fulfillment-option-"]'
                  );
                  const contOk = !!(btn && !btn.disabled
                    && btn.getAttribute('aria-disabled') !== 'true');
                  return !!(contOk && opt);
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def _fulfillment_location_already_ok(shown: str, city: str) -> bool:
    """Skip editor when the label already has the configured city (usually HCM).

    Apple's control shows 'Thành phố Hồ Chí Minh (Thành phố Hồ Chí Minh)' and
    does not print the quận — re-opening the editor does not change that.
    """
    s = (shown or "").strip().lower()
    want = (city or "").strip().lower()
    if not s:
        return False
    if "hồ chí minh" in want or "ho chi minh" in want or not want:
        return "hồ chí minh" in s or "ho chi minh" in s
    return want in s


def _set_fulfillment_location(page, city: str, district: str) -> None:
    """
    Set HCM + quận only if the fulfillment label is not already that city.
    If 'Giao hàng đến' already shows HCM and Continue is enabled, skip the
    editor and let the caller click Tiếp tục đến Địa Chỉ Giao Hàng.
    """
    city = city or "Thành phố Hồ Chí Minh"
    district = district or "Quận Bình Thạnh"
    t_loc = time.perf_counter()
    shown = _fulfillment_shown_location(page)
    if _fulfillment_location_already_ok(shown, city) and _fulfillment_continue_ready(
        page
    ):
        log(
            f"Location already set ({shown[:80]!r}) — skip editor, "
            "click Tiếp tục đến Địa Chỉ Giao Hàng"
        )
        log(
            f"Location skip DONE in {(time.perf_counter() - t_loc) * 1000:.0f}ms"
        )
        return

    log(f"Location edit needed (shown={shown[:80]!r}): {city} → {district}")

    _click_autom(page, "checkout-zipcode-edit", timeout_ms=8_000)
    t_editor = time.perf_counter()
    page.locator('select[data-autom="form-field-state"]').first.wait_for(
        state="visible", timeout=8_000
    )
    log(
        f"WAIT  location editor visible: "
        f"{(time.perf_counter() - t_editor) * 1000:.0f}ms"
    )

    # 1) City / tỉnh-TP
    t_city = time.perf_counter()
    for autom in ("form-field-state", "checkout-zipcode-city", "checkout-zipcode-state"):
        if page.locator(f'select[data-autom="{autom}"]').count() > 0:
            if _select_option_by_label(page, autom, city, wait_sec=8.0):
                log(
                    f"FILL  location city via select {autom}: "
                    f"{(time.perf_counter() - t_city) * 1000:.0f}ms"
                )
                break
    else:
        hit = _click_visible_text(
            page,
            city,
            "Thành phố Hồ Chí Minh",
            "Hồ Chí Minh",
        )
        log(
            f"CLICK  location city via text={hit!r}: "
            f"{(time.perf_counter() - t_city) * 1000:.0f}ms"
        )

    # 2) Quận — wait on option list, not a fixed sleep
    t_dist_opts = time.perf_counter()
    page.wait_for_function(
        """(district) => {
          const sel = document.querySelector('select[data-autom="form-field-city"]');
          if (!sel) return false;
          const want = String(district || '').toLowerCase();
          return Array.from(sel.options).some((o) => {
            const t = (o.textContent || '').trim().toLowerCase();
            return t && (t === want || t.includes('bình thạnh') || t.includes(want));
          });
        }""",
        arg=district,
        timeout=8_000,
    )
    log(
        f"WAIT  quận options ready: "
        f"{(time.perf_counter() - t_dist_opts) * 1000:.0f}ms"
    )
    t_dist = time.perf_counter()
    for autom in ("form-field-city", "checkout-zipcode-district", "form-field-district"):
        if page.locator(f'select[data-autom="{autom}"]').count() > 0:
            if _select_option_by_label(page, autom, district, wait_sec=6.0) or _select_option_by_label(
                page, autom, "Bình Thạnh", wait_sec=3.0
            ):
                log(
                    f"FILL  location quận via select {autom}: "
                    f"{(time.perf_counter() - t_dist) * 1000:.0f}ms"
                )
                break
    else:
        hit = _click_visible_text(
            page,
            district,
            "Quận Bình Thạnh",
            "Bình Thạnh",
        )
        if not hit:
            raise RuntimeError(
                f"Could not select quận {district!r} in location editor"
            )
        log(
            f"CLICK  location quận via text={hit!r}: "
            f"{(time.perf_counter() - t_dist) * 1000:.0f}ms"
        )

    # Apply / save — Apple VN uses deliveryOptionApply ("Áp dụng")
    applied = False
    for autom in (
        "deliveryOptionApply",
        "checkout-zipcode-apply",
        "zipcode-apply",
        "apply",
        "continue",
        "checkout-zipcode-done",
    ):
        try:
            if page.locator(f'[data-autom="{autom}"]').count() > 0:
                _click_autom(page, autom, timeout_ms=2_000)
                applied = True
                log(f"Location apply via {autom}")
                break
        except Exception:  # noqa: BLE001
            continue
    if not applied:
        for label in ("Áp Dụng", "Áp dụng", "Apply", "Xong", "Done", "Lưu"):
            try:
                t_btn = time.perf_counter()
                btn = page.get_by_role("button", name=re.compile(f"^{label}$", re.I))
                if btn.count() > 0:
                    btn.first.click(force=True, timeout=1_200, no_wait_after=True)
                    applied = True
                    log(
                        f"CLICK  location apply button {label!r}: "
                        f"{(time.perf_counter() - t_btn) * 1000:.0f}ms"
                    )
                    break
            except Exception:  # noqa: BLE001
                continue
    if not applied:
        log("WARNING: no location Apply button found")

    # Proceed as soon as delivery options / continue are usable again
    t_settle = time.perf_counter()
    try:
        page.wait_for_function(
            """() => {
              const apply = document.querySelector('[data-autom="deliveryOptionApply"]');
              const applyOpen = !!(apply && apply.offsetParent !== null);
              const opt = document.querySelector('input[data-autom^="fulfillment-option-"]');
              const cont = document.querySelector('[data-autom="fulfillment-continue-button"]');
              const contOk = !!(cont && !cont.disabled && cont.getAttribute('aria-disabled') !== 'true');
              return (!applyOpen && !!opt) || contOk;
            }""",
            timeout=8_000,
        )
    except Exception:  # noqa: BLE001
        pass
    log(
        f"WAIT  location apply settle: "
        f"{(time.perf_counter() - t_settle) * 1000:.0f}ms"
    )
    try:
        shown = page.locator('[data-autom="checkout-zipcode-edit"]').first.inner_text(
            timeout=1_500
        )
        log(f"Location control now shows: {shown.strip()[:80]}")
        if "bình thạnh" not in (shown or "").lower() and "binh thanh" not in (
            shown or ""
        ).lower():
            log("WARNING: location text may not show Bình Thạnh yet — continuing")
    except Exception:  # noqa: BLE001
        pass
    log(
        f"Location edit DONE in {(time.perf_counter() - t_loc) * 1000:.0f}ms "
        "(our clicks). Next hop Fulfillment→Shipping is Apple (~10s) — watch heartbeats."
    )


def _fold_addr_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _list_saved_shipping_addresses(page) -> list[dict]:
    """Visible saved-address radios: title = name, .form-label-small = street."""
    try:
        rows = page.evaluate(
            """() => Array.from(
              document.querySelectorAll('input[data-autom="saved-address"]')
            ).map((el) => {
              const label = el.id
                ? document.querySelector('label[for="' + el.id + '"]')
                : null;
              const titleEl = label && label.querySelector('.form-selector-title');
              const streetEl = label && label.querySelector('.form-label-small');
              return {
                value: el.value || '',
                id: el.id || '',
                name: ((titleEl && titleEl.innerText) || '').replace(/\\s+/g, ' ').trim(),
                street: ((streetEl && streetEl.innerText) || '').replace(/\\s+/g, ' ').trim(),
                text: ((label && label.innerText) || '').replace(/\\s+/g, ' ').trim(),
                checked: !!el.checked,
              };
            })"""
        )
    except Exception:  # noqa: BLE001
        return []
    return rows if isinstance(rows, list) else []


def _saved_address_matches(card: dict, addr: dict) -> bool:
    """Match config first/last + street against the visible saved-address card.

    Apple truncates the street on the radio (e.g. '... tp' vs config '... tphcm'),
    so substring / shared-prefix is enough. Name is the title ('Võ Văn Quân').
    """
    title = _fold_addr_text(str(card.get("name") or card.get("text") or ""))
    shown_street = _fold_addr_text(str(card.get("street") or ""))
    first = _fold_addr_text(str(addr.get("first_name") or ""))
    last = _fold_addr_text(str(addr.get("last_name") or ""))
    want_street = _fold_addr_text(str(addr.get("street") or ""))
    full_name = _fold_addr_text(f"{first} {last}".strip())

    name_ok = False
    if full_name and full_name in title:
        name_ok = True
    elif first and last and first in title and last in title:
        name_ok = True
    elif first and not last and first in title:
        name_ok = True

    street_ok = False
    if want_street and shown_street:
        if shown_street in want_street or want_street in shown_street:
            street_ok = True
        else:
            n = min(len(want_street), len(shown_street))
            common = 0
            for i in range(n):
                if want_street[i] != shown_street[i]:
                    break
                common += 1
            street_ok = common >= 12

    if want_street and (first or last):
        return name_ok and street_ok
    if want_street:
        return street_ok
    if first or last:
        return name_ok
    return False


def _select_matching_saved_address(
    page,
    addr: dict,
    timer: StageTimer | None = None,
) -> bool:
    """Click the saved-address radio whose visible name+street match config.

    Returns True if a match was selected (or already checked).
    """
    cards = _list_saved_shipping_addresses(page)
    if not cards:
        log("No saved-address radios on Shipping — will fill new")
        return False
    for card in cards:
        log(
            f"Saved address {card.get('value')}: "
            f"name={card.get('name')!r} street={card.get('street')!r}"
            f"{' [checked]' if card.get('checked') else ''}"
        )
    match = next((c for c in cards if _saved_address_matches(c, addr)), None)
    if not match:
        log("No saved address matches shipping_address name+street — will fill new")
        return False
    value = str(match.get("value") or "").strip()
    if not value:
        log("Matched saved address has empty value — will fill new")
        return False
    selector = f'input[data-autom="saved-address"][value="{value}"]'
    hint = (match.get("street") or match.get("name") or "").strip()
    t0 = time.perf_counter()
    _select_radio_until_checked(
        page,
        selector,
        label=f"saved-address {value}",
        text_hints=[hint] if hint else None,
        timeout_ms=6_000,
        attempts=5,
    )
    if not _radio_is_checked(page, selector):
        log(f"WARNING: saved address {value} did not stay checked")
        return False
    shown = f"{match.get('name') or ''} / {match.get('street') or ''}".strip(" /")
    log(f"Selected saved shipping address {value}: {shown}")
    if timer:
        timer.since(t0, f"15 select saved address ({value})", kind="click")
    return True


def _fill_new_shipping_address(
    page,
    addr: dict,
    contact: dict | None = None,
    timer: StageTimer | None = None,
) -> None:
    """Fill shipping address + contact.

    Returning Apple IDs get a 'Sử dụng địa chỉ mới' radio. A brand-new account
    has no radios — the form is already open (firstName/email already in DOM).
    """
    contact = contact or {}
    log("Filling shipping address + contact")
    page.wait_for_function(
        """() => !!(
          document.querySelector('input[data-autom="newAddress"]')
          || document.querySelector('[data-autom="form-field-firstName"]')
          || document.querySelector('[data-autom="form-field-street"]')
        )""",
        timeout=15_000,
    )
    radio = page.locator('input[data-autom="newAddress"]')
    if radio.count() > 0:
        log("Selecting Sử dụng địa chỉ mới")
        _select_radio_until_checked(
            page,
            'input[data-autom="newAddress"]',
            label="newAddress",
            text_hints=["Sử dụng địa chỉ mới"],
            timeout_ms=6_000,
            attempts=5,
        )
        try:
            page.wait_for_function(
                """() => !!document.querySelector(
                  'select[data-autom="form-field-state"], [data-autom="form-field-street"]'
                )""",
                timeout=5_000,
            )
        except Exception:  # noqa: BLE001
            pass
        if timer:
            timer.mark("15a newAddress selected (verified)")
    else:
        log(
            "No newAddress radio — first-time form is already open "
            "(email/name fields present). Filling it."
        )
        if timer:
            timer.mark("15a first-time address form")

    state = addr.get("state") or "Thành phố Hồ Chí Minh"
    city = addr.get("city") or "Quận Bình Thạnh"
    district = addr.get("district") or ""

    # Skip cascade selects when fulfillment already seeded the right values,
    # or when this is a first-time form with no tỉnh/quận dropdowns.
    has_state = page.locator('select[data-autom="form-field-state"]').count() > 0
    has_city = page.locator('select[data-autom="form-field-city"]').count() > 0
    cur_state = _select_current_label(page, "form-field-state") if has_state else ""
    if not has_state:
        log("No state/tỉnh select on this shipping form — skip")
        if timer:
            timer.mark("15b1 no state select (skip)")
    elif state.lower() not in cur_state.lower() and "hồ chí minh" not in cur_state.lower():
        if not _select_option_native(page, "form-field-state", state, wait_sec=8.0):
            raise RuntimeError(f"Could not select state/tỉnh {state!r}")
        log(f"Selected state: {state}")
        if timer:
            timer.mark("15b1 select state/tỉnh")
    else:
        log(f"State already set: {cur_state}")
        if timer:
            timer.mark("15b1 state already set (skip)")

    cur_city = _select_current_label(page, "form-field-city") if has_city else ""
    if not has_city:
        log("No city/quận select on this shipping form — skip")
        if timer:
            timer.mark("15b2 no city select (skip)")
    elif city.lower() not in cur_city.lower() and "bình thạnh" not in cur_city.lower():
        if not _select_option_native(page, "form-field-city", city, wait_sec=12.0):
            raise RuntimeError(f"Could not select city/quận {city!r}")
        log(f"Selected city/quận: {city}")
        if timer:
            timer.mark("15b2 select city/quận")
    else:
        log(f"City already set: {cur_city}")
        if timer:
            timer.mark("15b2 city already set (skip)")

    # Fill each text field individually so the timer shows name/street/email/phone
    first_name = (addr.get("first_name") or "").strip()
    last_name = (addr.get("last_name") or "").strip()
    street = (addr.get("street") or "").strip()
    street2 = (addr.get("street2") or "").strip()
    postal = (addr.get("postal_code") or "").strip()
    email = (contact.get("email") or "").strip()
    phone = (contact.get("phone") or "").strip()

    def _fill_one(autom: str, value: str, label: str) -> None:
        if not value:
            return
        t0 = time.perf_counter()
        _fill_fields_native(page, {autom: value})
        try:
            page.locator(f'[data-autom="{autom}"]').first.fill(str(value), timeout=1_500)
        except Exception:  # noqa: BLE001
            pass
        got = page.evaluate(
            """(a) => {
              const el = document.querySelector('[data-autom="' + a + '"]');
              return el ? (el.value || '') : '';
            }""",
            autom,
        )
        ms = (time.perf_counter() - t0) * 1000
        log(f"Filled {label}: {got!r} ({ms:.0f}ms)")
        if timer:
            timer.mark(f"15c {label}")

    _fill_one("form-field-firstName", first_name, "firstName")
    _fill_one("form-field-lastName", last_name, "lastName")
    _fill_one("form-field-street", street, "street")
    if street2:
        _fill_one("form-field-street2", street2, "street2")
    if postal:
        _fill_one("form-field-postalCode", postal, "postalCode")

    if email:
        t0 = time.perf_counter()
        got = _fill_contact_fields(page, "form-field-emailAddress", email)
        if got.lower() != email.lower():
            raise RuntimeError(f"Email did not stick: wanted {email!r}, got {got!r}")
        ms = (time.perf_counter() - t0) * 1000
        log(f"Filled email: {got} ({ms:.0f}ms)")
        if timer:
            timer.mark("15c email")
    if phone:
        t0 = time.perf_counter()
        got = _fill_contact_fields(page, "form-field-fullDaytimePhone", phone)
        digits = lambda s: re.sub(r"\D", "", s or "")
        if digits(got) != digits(phone) and got != phone:
            raise RuntimeError(f"Phone did not stick: wanted {phone!r}, got {got!r}")
        ms = (time.perf_counter() - t0) * 1000
        log(f"Filled phone: {got} ({ms:.0f}ms)")
        if timer:
            timer.mark("15c phone")

    if district:
        has_district = (
            page.locator('select[data-autom="form-field-district"]').count() > 0
        )
        if not has_district:
            log("No phường select on this shipping form — skip")
            if timer:
                timer.mark("15d no phường select (skip)")
        else:
            t0 = time.perf_counter()
            cur_d = _select_current_label(page, "form-field-district")
            if district.lower() not in cur_d.lower():
                _wait_js_heartbeat(
                    page,
                    """(want) => {
                      const sel = document.querySelector(
                        'select[data-autom="form-field-district"]'
                      );
                      if (!sel || sel.disabled) return false;
                      const w = String(want || '').toLowerCase();
                      return Array.from(sel.options).some((o) => {
                        const t = (o.textContent || '').trim().toLowerCase();
                        return t && (t === w || t.includes(w));
                      });
                    }""",
                    arg=district,
                    label="phường options after quận (Apple cascade)",
                    timeout_ms=12_000,
                    snapshot_js=_SNAP_CHECKOUT,
                )
                # Playwright select_option — React commits this reliably
                if not _select_option_by_label(
                    page, "form-field-district", district, wait_sec=4.0
                ):
                    opts = page.evaluate(
                        """() => {
                          const s = document.querySelector(
                            'select[data-autom="form-field-district"]'
                          );
                          return s
                            ? Array.from(s.options).map(
                                (o) => (o.textContent || '').trim()
                              )
                            : [];
                        }"""
                    )
                    raise RuntimeError(
                        f"Could not select phường/district {district!r}; "
                        f"options={opts!r}"
                    )
                log(f"Selected phường trước sáp nhập: {district}")
            else:
                # Re-select anyway so React validation sees a change event
                _select_option_by_label(
                    page, "form-field-district", district, wait_sec=2.0
                )
                log(f"Phường confirmed: {district}")
            ms = (time.perf_counter() - t0) * 1000
            log(f"Phường step done ({ms:.0f}ms)")
            if timer:
                timer.mark("15d phường")


def _advance_fulfillment_to_shipping(
    page, timer: StageTimer, loc_city: str, loc_district: str
) -> None:
    """HCM + quận, then Continue onto the street-address page."""
    t_fulfill = time.perf_counter()
    page.wait_for_function(
        """() => {
          const u = location.href;
          if (u.includes('Fulfillment') || u.includes('_s=Fulfillment')) return true;
          return !!document.querySelector('[data-autom="fulfillment-continue-button"]');
        }""",
        timeout=20_000,
    )
    # Zip editor must be ready before we time "fulfillment page"
    page.locator('[data-autom="checkout-zipcode-edit"]').first.wait_for(
        state="attached", timeout=10_000
    )
    timer.since(t_fulfill, "11 fulfillment page", kind="wait")
    log(f"Fulfillment at: {page.url} (_s={_checkout_step(page)})")
    _prefind_automs(
        page,
        {
            "zipEdit": "checkout-zipcode-edit",
            "fulfillContinue": "fulfillment-continue-button",
            "deliveryS1": "fulfillment-option-S1",
        },
    )

    t_loc = time.perf_counter()
    _set_fulfillment_location(page, loc_city, loc_district)
    timer.since(t_loc, "12 fulfillment location (skip or edit)", kind="fill")

    # Ensure a delivery option is selected (prefer S1)
    t_opt = time.perf_counter()
    page.evaluate(
        """() => {
          const prefer = document.querySelector('input[data-autom="fulfillment-option-S1"]');
          const checked = document.querySelector(
            'input[data-autom^="fulfillment-option-"]:checked'
          );
          const target = prefer || checked || document.querySelector(
            'input[data-autom^="fulfillment-option-"]'
          );
          if (!target || target.checked) return;
          const label = target.id
            ? document.querySelector('label[for="' + target.id + '"]')
            : null;
          (label || target).click();
        }"""
    )
    log(
        f"CLICK  fulfillment delivery option: "
        f"{(time.perf_counter() - t_opt) * 1000:.0f}ms"
    )

    t_en = time.perf_counter()
    page.wait_for_function(
        """() => {
          const btn = document.querySelector('[data-autom="fulfillment-continue-button"]');
          return !!(btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true');
        }""",
        timeout=10_000,
    )
    timer.since(t_en, "12b fulfillment continue enabled", kind="wait")

    shipping_ok = False
    t_ship_phase = time.perf_counter()
    for attempt in range(3):
        try:
            click_ms = _click_autom(page, "fulfillment-continue-button", timeout_ms=3_000)
            if attempt == 0:
                timer.record("13 click fulfillment continue", click_ms, kind="click")
        except Exception as exc:  # noqa: BLE001
            log(f"Fulfillment continue click failed (attempt {attempt + 1}): {exc}")
            page.wait_for_timeout(120)
            continue
        try:
            # Apple keeps "Chúng tôi giao hàng..." on screen ~10s after Continue.
            _wait_js_heartbeat(
                page,
                """() => {
                  const u = location.href;
                  const onShip = u.includes('Shipping') || u.includes('_s=Shipping');
                  const form = !!document.querySelector(
                    'input[data-autom="newAddress"], [data-autom="shipping-continue-button"]'
                  );
                  return onShip && form;
                }""",
                label=f"Apple hop Fulfillment→Shipping (attempt {attempt + 1})",
                timeout_ms=15_000,
                snapshot_js=_SNAP_CHECKOUT,
            )
            shipping_ok = True
            break
        except Exception as hop_exc:  # noqa: BLE001
            log(
                f"WAIT  Shipping not reached (attempt {attempt + 1}, "
                f"url={page.url}, _s={_checkout_step(page)}): {hop_exc}"
            )
            page.wait_for_timeout(150)
    if not shipping_ok:
        raise RuntimeError(
            f"Did not reach shipping after fulfillment (url={page.url}, _s={_checkout_step(page)})"
        )
    timer.since(t_ship_phase, "13b wait Shipping page + form", kind="wait")
    log(f"Shipping at: {page.url} (_s={_checkout_step(page)})")


def advance_checkout_to_payment(
    page,
    timer: StageTimer | None = None,
    *,
    login_timeout_sec: int = 300,
    checkout_cfg: dict | None = None,
    until: str = "payment",
) -> None:
    """
    Apple VN path (dry-run stop at payment):
      Fulfillment (HCM + Bình Thạnh) → Shipping (matching saved address, else new)
      → Billing (saved card)
    Never clicks review / Đặt hàng.

    until:
      shipping — stop after the address is on the account (used by --warm-only)
      payment  — continue to saved card + CVV
    """
    timer = timer or StageTimer()
    checkout_cfg = checkout_cfg or {}
    loc_city = checkout_cfg.get("location_city") or "Thành phố Hồ Chí Minh"
    loc_district = checkout_cfg.get("location_district") or "Quận Bình Thạnh"
    # shipping_address = delivery; billing_address = card. Legacy: address = shipping.
    ship_addr = checkout_cfg.get("shipping_address") or checkout_cfg.get("address") or {}
    if not isinstance(ship_addr, dict):
        ship_addr = {}
    bill_addr = checkout_cfg.get("billing_address") or ship_addr
    if not isinstance(bill_addr, dict):
        bill_addr = ship_addr
    contact = checkout_cfg.get("contact") or {}
    if not isinstance(contact, dict):
        contact = {}

    t_sso = time.perf_counter()
    wait_checkout_signin_if_needed(page, login_timeout_sec)
    timer.since(t_sso, "10b checkout SSO ready", kind="wait")

    already_shipping = False
    try:
        already_shipping = bool(
            page.evaluate(
                """() => {
                  const u = location.href || '';
                  return u.includes('Shipping') || u.includes('_s=Shipping');
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        already_shipping = False

    # ===== Fulfillment =====
    if already_shipping:
        log("Already on Shipping — skip fulfillment location")
    else:
        _advance_fulfillment_to_shipping(page, timer, loc_city, loc_district)
    _prefind_automs(
        page,
        {
            "newAddress": "newAddress",
            "savedAddress": "saved-address",
            "shipContinue": "shipping-continue-button",
            "email": "form-field-emailAddress",
            "phone": "form-field-fullDaytimePhone",
            "firstName": "form-field-firstName",
        },
    )

    force_new = bool(checkout_cfg.get("use_new_address", False))
    used_saved = False
    if not force_new:
        used_saved = _select_matching_saved_address(page, ship_addr, timer=timer)
    else:
        log("use_new_address=true — skipping saved-address match")

    if used_saved:
        t_verify = time.perf_counter()
        page.wait_for_function(
            """() => {
              const el = document.querySelector(
                'input[data-autom="saved-address"]:checked'
              );
              return !!el;
            }""",
            timeout=3_000,
        )
        timer.since(t_verify, "15e saved address verified", kind="wait")
    else:
        _fill_new_shipping_address(page, ship_addr, contact, timer=timer)
        # Quick verify (fields already checked during fill)
        t_verify = time.perf_counter()
        page.wait_for_function(
            """() => {
              const n = document.querySelector('input[data-autom="newAddress"]');
              const street = document.querySelector('[data-autom="form-field-street"]');
              const first = document.querySelector('[data-autom="form-field-firstName"]');
              const district = document.querySelector(
                'select[data-autom="form-field-district"]'
              );
              const radioOk = !n || !!n.checked;
              const districtOk = !district || !!(district.value && district.value.trim());
              const streetOk = !!(street && (street.value || '').trim().length > 3);
              const firstOk = !!(first && (first.value || '').trim().length > 1);
              return radioOk && districtOk && (streetOk || firstOk);
            }""",
            timeout=8_000,
        )
        timer.since(t_verify, "15e address verified", kind="wait")

    # Prefind → wait enabled → click (in-page then Playwright force, like pre-SSO)
    t_ship_en = time.perf_counter()
    page.wait_for_function(
        """() => {
          const btn = document.querySelector('[data-autom="shipping-continue-button"]');
          return !!(btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true');
        }""",
        timeout=10_000,
    )
    timer.since(t_ship_en, "16a shipping continue enabled", kind="wait")
    _prefind_automs(page, {"shipContinue": "shipping-continue-button"})

    billing_ok = False
    t_bill_phase = time.perf_counter()
    for attempt in range(3):
        try:
            click_ms = _click_autom(page, "shipping-continue-button", timeout_ms=3_000)
            if attempt == 0:
                timer.record(
                    "16b click Tiếp tục đến Thanh Toán", click_ms, kind="click"
                )
        except Exception as exc:  # noqa: BLE001
            log(f"Shipping continue click failed (attempt {attempt + 1}): {exc}")
            try:
                t_force = time.perf_counter()
                page.locator('[data-autom="shipping-continue-button"]').first.click(
                    force=True, timeout=1_500, no_wait_after=True
                )
                log(
                    f"CLICK  shipping-continue force: "
                    f"{(time.perf_counter() - t_force) * 1000:.0f}ms"
                )
            except Exception:  # noqa: BLE001
                page.wait_for_timeout(120)
                continue
        try:
            _wait_js_heartbeat(
                page,
                """() => !!document.querySelector(
                  '[data-autom="checkout-billingOptions-SAVED_CARD"], [data-autom^="checkout-billingOptions-"]'
                )""",
                label=f"Apple hop Shipping→Billing (attempt {attempt + 1})",
                timeout_ms=15_000,
                snapshot_js=_SNAP_CHECKOUT,
            )
            billing_ok = True
            break
        except Exception as hop_exc:  # noqa: BLE001
            errs = page.evaluate(
                """() => Array.from(document.querySelectorAll(
                  '[class*="error"], [aria-invalid="true"], .form-message-error, [data-autom*="error"]'
                )).map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 6)"""
            )
            step = _checkout_step(page)
            log(
                f"WAIT  still not billing (attempt {attempt + 1}, _s={step}): {hop_exc}"
                + (f" errors={errs!r}" if errs else "")
            )
            page.wait_for_timeout(200)

    if not billing_ok:
        raise RuntimeError(
            f"Did not reach billing/payment page (url={page.url}, _s={_checkout_step(page)})"
        )
    timer.since(t_bill_phase, "17 wait billing + payment options", kind="wait")
    log(f"Billing at: {page.url} (_s={_checkout_step(page)})")
    if (until or "payment").strip().lower() == "shipping":
        log(
            "Delivery address is in place — stopping before payment "
            "(card is yours; we do not type it)"
        )
        return
    _prefind_automs(
        page,
        {
            "savedCard": "checkout-billingOptions-SAVED_CARD",
            "reviewBtn": "continue-button-review",
        },
    )

    # Prefind → click → verify (same as declines)
    t_card = time.perf_counter()
    _click_autom(page, "checkout-billingOptions-SAVED_CARD", timeout_ms=5_000)
    t_card_verify = time.perf_counter()
    _verify_checked(page, "checkout-billingOptions-SAVED_CARD", timeout_ms=5_000)
    log(
        f"WAIT  saved card verified: "
        f"{(time.perf_counter() - t_card_verify) * 1000:.0f}ms"
    )
    timer.since(t_card, "18 select saved card (verified)", kind="click")
    log("Selected SAVED_CARD — billing sync (if needed) → CVV → review → stop at Đặt hàng.")
    # CVV + Chỉnh sửa / address block mount slowly after SAVED_CARD expand
    t_mount = time.perf_counter()
    try:
        page.wait_for_function(
            """() => {
              const edit = document.querySelector(
                'button[id*="editBillingAddress"], button.rf-creditcard-editaddress'
              );
              const cvv = document.querySelector('[data-autom="security-code-input"]');
              const open = !!document.querySelector(
                '[data-autom="address-savebutton"], input[id*="editSavedBillingAddress"]'
              );
              const editOk = !!(edit && (edit.offsetParent || edit.getClientRects().length));
              const cvvOk = !!(cvv && !cvv.disabled
                && (cvv.offsetParent || cvv.getClientRects().length));
              // Need both when possible — Apple sometimes shows CVV before Chỉnh sửa
              return open || (editOk && cvvOk) || editOk || cvvOk;
            }""",
            timeout=20_000,
        )
        log(
            f"WAIT  billing CVV/address panel mount: "
            f"{(time.perf_counter() - t_mount) * 1000:.0f}ms"
        )
    except Exception as mount_exc:  # noqa: BLE001
        log(
            f"WAIT  billing card details mount "
            f"({(time.perf_counter() - t_mount) * 1000:.0f}ms): {mount_exc}"
        )
    if timer:
        timer.since(t_mount, "18a billing CVV/address mount", kind="wait")

    # CVV first — field is on the card panel BEFORE the address popup covers it
    cvv = ""
    if isinstance(checkout_cfg, dict):
        cvv = str(checkout_cfg.get("cvv") or checkout_cfg.get("security_code") or "")
    _fill_cvv(page, cvv, timer=timer, mount_timeout_ms=3_000)

    # Only fill billing when Apple actually asks (popup or empty on-page form).
    # Machines that go straight to Đặt hàng are left alone.
    sync_billing = bool(
        isinstance(checkout_cfg, dict) and checkout_cfg.get("sync_billing_address")
    )
    force_billing = bool(
        isinstance(checkout_cfg, dict)
        and checkout_cfg.get("force_sync_billing_address")
    )
    if not sync_billing:
        log(
            "Billing sync OFF — using Apple Account card address "
            "(set sync_billing_address: true to fill it from config when Apple asks)"
        )
    prompt = _billing_prompt_visible(page)
    incomplete = bool(sync_billing) and _billing_card_incomplete(page, bill_addr)
    if sync_billing and not prompt and incomplete and not force_billing:
        log("Billing card address incomplete — opening editor before review")
        try:
            _open_billing_address_edit(page)
            prompt = _billing_prompt_visible(page)
        except Exception as open_exc:  # noqa: BLE001
            log(f"Could not open billing editor early: {open_exc}")
    if sync_billing and not prompt and not force_billing:
        log("No billing address prompt — skipping fill")
    if sync_billing and (prompt or force_billing):
        if force_billing and not prompt:
            try:
                _open_billing_address_edit(page)
            except Exception as open_exc:  # noqa: BLE001
                log(f"force_sync_billing_address could not open editor: {open_exc}")
        _sync_billing_address_from_checkout(page, bill_addr, timer=timer)
        # Popup can wipe CVV — force re-fill without long visibility wait
        if cvv:
            still = page.evaluate(
                """(want) => {
                  const el = document.querySelector(
                    '[data-autom="security-code-input"]'
                  );
                  if (!el) return { ok: false, len: 0 };
                  const v = (el.value || '').replace(/\\D/g, '');
                  if (v === want) return { ok: true, len: v.length };
                  const proto = window.HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(el, want);
                  else el.value = want;
                  const tracker = el._valueTracker;
                  if (tracker && typeof tracker.setValue === 'function') {
                    tracker.setValue('');
                  }
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  const v2 = (el.value || '').replace(/\\D/g, '');
                  return { ok: v2 === want, len: v2.length };
                }""",
                re.sub(r"\D", "", cvv),
            ) or {}
            if still.get("ok"):
                log(f"FILL  CVV re-assert after address save (len={still.get('len')})")
            else:
                log("CVV missing after address save — short remount re-fill")
                _fill_cvv(page, cvv, timer=timer, mount_timeout_ms=1_500)

    try:
        _click_review_and_stop_at_place_order(page, timer=timer)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if sync_billing and (
            "không hợp lệ" in msg
            or "billing" in msg
            or "address" in msg
            or "định dạng" in msg
            or "error" in msg
        ):
            log(
                f"Review blocked — filling billing address now: {exc}"
            )
            if not _billing_prompt_visible(page):
                appeared = False
                deadline = time.perf_counter() + 3.0
                while time.perf_counter() < deadline:
                    if _billing_prompt_visible(page):
                        appeared = True
                        break
                    page.wait_for_timeout(100)
                if not appeared:
                    log(
                        "Review error but no billing prompt — "
                        "not clicking Chỉnh sửa; leaving the card address alone"
                    )
                    raise
            _sync_billing_address_from_checkout(page, bill_addr, timer=timer)
            _fill_cvv(page, cvv, timer=timer, mount_timeout_ms=1_500)
            _click_review_and_stop_at_place_order(page, timer=timer)
        else:
            raise


def click_next_steps(
    page,
    timer: StageTimer | None = None,
    *,
    login_timeout_sec: int = 300,
    checkout_cfg: dict | None = None,
) -> None:
    """
    After add-to-cart: bag → Thanh Toán → Fulfillment → Shipping →
    Billing (saved card + CVV) → Review → STOP at Đặt hàng (never clicks it).
    """
    timer = timer or StageTimer()
    click_xem_gio_hang_now(page, timer)

    if "/shop/bag" in page.url.lower() or "checkout" not in page.url.lower():
        qty = _checkout_quantity(checkout_cfg)
        set_bag_quantity(page, qty, timer=timer)
        click_thanh_toan_now(page, timer, quantity=qty)

    advance_checkout_to_payment(
        page,
        timer,
        login_timeout_sec=login_timeout_sec,
        checkout_cfg=checkout_cfg,
    )

    timer.report("EFFICIENCY TIMER")
    log("DRY-RUN STOP — at Đặt hàng page. Do NOT click place order.")
    notify_macos(
        "Assist — stopped at Đặt hàng",
        "Review done. YOU click Đặt hàng if you want — assist will not.",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        require_dry_run(cfg)
        product_url = validate_store_url(cfg.get("product_url", ""), "product_url")
        session_url = validate_store_url(
            cfg.get("session_url") or DEFAULT_SESSION_URL,
            "session_url",
        )
        label = cfg.get("label") or "Apple VN product"
        no_trade, no_care = click_labels(cfg)
        family_url_raw = (cfg.get("family_url") or "").strip()
        family_url = (
            validate_store_url(family_url_raw, "family_url") if family_url_raw else ""
        )
        family_urls_cfg = cfg.get("family_urls") or []
        if not isinstance(family_urls_cfg, list):
            family_urls_cfg = []
        family_candidates = []
        seen_family: set[str] = set()
        if family_url:
            family_candidates.append(family_url)
            seen_family.add(family_url.rstrip("/"))
        for u in family_urls_cfg:
            if isinstance(u, str) and u.strip():
                vu = validate_store_url(u.strip(), "family_urls")
                key = vu.rstrip("/")
                if key in seen_family:
                    continue
                family_candidates.append(vu)
                seen_family.add(key)
        hub_raw = (cfg.get("family_hub_url") or "https://www.apple.com/vn/shop/buy-iphone/").strip()
        family_hub_url = validate_store_url(hub_raw, "family_hub_url") if hub_raw else ""
        family_match_cfg = cfg.get("family_match") or []
        if not isinstance(family_match_cfg, list):
            family_match_cfg = []
        # Keep nesting intact: a list of token lists means "try strictest first".
        family_match = _normalize_match_sets(family_match_cfg)
        target = load_order_target(cfg)
        derived = _derive_match_sets(target)
        if not family_match:
            family_match = derived
            log(f"family_match not set — derived {family_match!r} from target")
        else:
            # Explicit config wins; only append looser sets to try after it.
            for tokens in derived:
                if tokens not in family_match:
                    family_match.append(tokens)
                    log(f"family_match — added fallback {tokens!r}")
        product_prefs = cfg.get("product_prefs") if isinstance(cfg.get("product_prefs"), dict) else {}
        use_dynamic = bool(product_prefs) or bool(family_candidates)
        if str(target.get("year")) == "18":
            # Never fall back to a leftover iPhone 17 product_url on launch night.
            use_dynamic = True
        # Phase A budget only (hub + user-pick are separate). Default 20s — not minutes.
        unlock_timeout = args.unlock_timeout_sec or int(cfg.get("unlock_timeout_sec") or 20)
        family_user_pick_sec = float(
            cfg.get("family_user_pick_timeout_sec")
            or (product_prefs.get("user_pick_timeout_sec") if product_prefs else None)
            or 45
        )
        # Warm should use a known-live practice SKU (not the launch family page)
        warm_url_raw = (cfg.get("warm_product_url") or "").strip()
        warm_product_url = (
            validate_store_url(warm_url_raw, "warm_product_url")
            if warm_url_raw
            else product_url
        )
    except ConfigError as exc:
        die(str(exc))

    print_checklist_reminder()
    log("DRY-RUN assist — declines only; never purchases")
    if str(target.get("year")) == "18":
        log(
            f"ORDER LOCK: {_target_pretty(target)} — "
            "timed run will REFUSE 17 / Fold / Air"
        )
    else:
        log(
            f"TEST MODE: {_target_pretty(target)} "
            "(config mode: test — set mode: launch on launch night)"
        )
    log(f"Persistent profile: {PROFILE_DIR}")
    log("Warm Chrome via CDP — we DISCONNECT only, never quit Chrome (keeps SSO / skips 2FA).")
    if use_dynamic:
        log(
            f"Dynamic SKU mode ON — family={family_candidates or [_product_family_url(product_url)]} "
            f"prefs={ {k: product_prefs.get(k) for k in ('screensizes','sizes','colors','storages') if product_prefs.get(k)} }"
        )

    sync_playwright = _require_playwright()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        meta = None
        page = None
        exit_code = 0
        try:
            browser, context, page, meta = launch_assist_browser(p, profile_dir=PROFILE_DIR)

            # IMPORTANT: /shop/goto/account ALWAYS bounces through
            # secure*.store.apple.com/signIn/account?ssi=… (~8–10s) even when already
            # logged in. Only do that for setup/warm — timed T-0 path skips it.
            need_account_gate = args.setup_login or args.warm_only
            if need_account_gate:
                log(f"Opening session URL (setup/warm only): {session_url}")
                goto_resilient(page, session_url)
                url = page.url
                title = page.title()
                if looks_like_signin(url, title) or args.setup_login:
                    if is_silent_store_sso(url) and not is_interactive_signin(page):
                        log("Account silent SSO hop — waiting (not necessarily 2FA)…")
                    else:
                        log("Need Apple ID session — complete sign-in + 2FA if shown.")
                    ok = wait_for_signin(page, args.login_timeout_sec)
                    if not ok:
                        goto_resilient(page, session_url)
                        ok = looks_signed_in(page.url, page.title()) or not looks_like_signin(
                            page.url, page.title()
                        )
                    if not ok:
                        raise RuntimeError(
                            f"Timed out waiting for sign-in after {args.login_timeout_sec}s. "
                            "Re-run: python assist.py --setup-login"
                        )
                    notify_macos("Assist — signed in", "Apple ID session looks ready.")
                    log("Sign-in complete for assist profile.")
            else:
                log(
                    "Timed run: skipping /account gate "
                    "(avoids extra silent signIn hop every launch)"
                )

            # Timed wait AFTER warm Chrome is attached — then sprint immediately at T-0
            wait_then_sprint = False
            if not (args.setup_login or args.warm_only):
                tz = config_timezone(cfg)
                if args.in_duration:
                    t_go = now_in_tz(tz) + parse_duration(args.in_duration)
                    log(f"Timer practice: sprint at {format_ts(t_go)} (--in {args.in_duration})")
                    wait_then_sprint = True
                elif args.at_launch:
                    t_go = parse_launch_at(cfg)
                    log(f"Launch timer: sprint at {format_ts(t_go)} (--at-launch)")
                    wait_then_sprint = True
                else:
                    log("No --at-launch / --in — sprinting immediately (--now default)")
                # Empty off the T-0 clock so GO opens the buy page, not /bag.
                # --now still empties as the first timed step (no countdown).
                if wait_then_sprint:
                    empty_bag(page, reason="before countdown")
                    log("Bag empty — at T-0 we go straight to buy")
                    if args.in_duration:
                        wait_until(t_go, tz, "T-0 practice")
                    elif now_in_tz(tz) < t_go:
                        wait_until(t_go, tz, "T-0")
                    else:
                        log(f"launch_at already past ({format_ts(t_go)}) — sprinting NOW")

            do_warm = args.setup_login or args.warm_only or args.warmup_before_run
            if do_warm:
                log(
                    "Warming account + checkout SSO NOW (before timed sprint / T-0)…"
                )
                try:
                    checkout_cfg = (
                        cfg.get("checkout") if isinstance(cfg.get("checkout"), dict) else {}
                    )
                    warm_checkout_sso(
                        page,
                        product_url=warm_product_url,
                        no_trade=no_trade,
                        no_care=no_care,
                        login_timeout_sec=args.login_timeout_sec,
                        timeout_ms=args.timeout_ms,
                        checkout_cfg=checkout_cfg,
                    )
                except Exception as warm_exc:  # noqa: BLE001
                    if args.warm_only or args.setup_login:
                        raise
                    log(f"Checkout SSO warm failed (continuing timed run): {warm_exc}")

            if args.setup_login or args.warm_only:
                log(
                    "WARM DONE (SSO + delivery address + bag emptied). "
                    "Leave Chrome open. At T-0:  python assist.py"
                )
                notify_macos(
                    "Assist warm done",
                    "Address saved, bag empty. Leave Chrome open; run assist.py at T-0.",
                )
            else:
                if not args.warmup_before_run:
                    log(
                        "Timed run — no SSO warm in this path "
                        "(pre-warm earlier: python assist.py --warm-only)"
                    )

                timer = StageTimer()
                if not wait_then_sprint:
                    empty_bag(page, reason="timed run")
                    timer.mark("0 empty bag", kind="nav")
                if use_dynamic:
                    candidates = list(family_candidates)
                    if not candidates:
                        if str(target.get("year")) == "18":
                            raise RuntimeError(
                                "ORDER LOCK: family_url / family_urls missing. "
                                "Refusing leftover product_url (that is iPhone 17 SSO fuel only)."
                            )
                        candidates = [_product_family_url(product_url)]
                    log(f"Dynamic timed run — poll/select on {candidates}")
                    wait_family_configure_ready(
                        page,
                        candidates,
                        timer=timer,
                        timeout_sec=unlock_timeout,
                        hub_url=family_hub_url,
                        family_match=family_match,
                        user_pick_timeout_sec=family_user_pick_sec,
                        target=target,
                    )
                    assert_family_is_order_target(page, target)
                    select_product_dimensions(
                        page,
                        product_prefs or {},
                        timer=timer,
                        target=target,
                    )
                    assert_sku_is_pro_max(page, target)
                    assert_sku_matches_model(page, target)
                else:
                    log(f"Opening product (timed run): {product_url}")
                    open_product_page(page, product_url)
                    timer.mark("0 product page navigation")

                    if looks_like_signin(page.url, page.title()):
                        log("Product flow redirected to sign-in — waiting (2FA if needed)…")
                        if not wait_for_signin(page, args.login_timeout_sec):
                            raise RuntimeError(
                                "Sign-in required again; finish 2FA then re-run assist.py"
                            )
                        open_product_page(page, product_url)
                        timer.mark("0b re-login + product reload")

                page.locator(
                    '#noTradeIn, [data-autom="choose-noTradeIn"], input[value="noTradeIn"]'
                ).first.wait_for(state="attached", timeout=8_000)
                timer.mark("0c trade-in radio ready")

                select_declines(page, no_trade, no_care, args.timeout_ms, timer)
                checkout_cfg = cfg.get("checkout") if isinstance(cfg.get("checkout"), dict) else {}
                click_next_steps(
                    page,
                    timer,
                    login_timeout_sec=args.login_timeout_sec,
                    checkout_cfg=checkout_cfg,
                )
                beep()
                print_manual_clicks(cfg)
                notify_macos(
                    "Assist done — stopped at Đặt hàng",
                    f"{label}: at place-order page. Do NOT click Đặt hàng (dry-run).",
                )
                log("SUCCESS: reached Đặt hàng page (place-order visible, not clicked).")
                log("STOPPING before Đặt hàng / Place Order.")

            # Brief pause so you can see the page — Chrome stays open either way
            page.wait_for_timeout(max(args.keep_open_sec, 3) * 1000)
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            log(f"Assist failed: {exc}")
            notify_macos("Assist failed", str(exc)[:120])
            if page is not None:
                try:
                    page.wait_for_timeout(max(args.keep_open_sec, 3) * 1000)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            # CRITICAL: disconnect only — do NOT quit Chrome (preserves login + checkout SSO)
            disconnect_assist_browser(browser, meta)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
