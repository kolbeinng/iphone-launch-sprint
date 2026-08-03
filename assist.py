#!/usr/bin/env python3
"""
Dry-run browser assist for Apple VN buy flow.

- Uses a dedicated persistent Chromium profile (sign in once, reuse)
- Waits for Apple ID sign-in instead of hanging on selectors
- Opens the product deep link
- Selects NO trade-in and NO AppleCare+
- Continues checkout: fulfillment → saved shipping → saved card
- STOPS at payment method (before review / Đặt hàng)

Never places an order.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

from sprint_common import (
    ASSIST_PROFILE_DIR,
    DEFAULT_CONFIG,
    DEFAULT_SESSION_URL,
    ROOT,
    ConfigError,
    beep,
    click_labels,
    die,
    disconnect_assist_browser,
    launch_assist_browser,
    load_config,
    log,
    notify_macos,
    print_checklist_reminder,
    print_manual_clicks,
    require_dry_run,
    validate_store_url,
)

PROFILE_DIR = ASSIST_PROFILE_DIR


class StageTimer:
    """Simple wall-clock stage timer for efficiency debugging."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.marks: list[tuple[str, float]] = []
        self._last = self.t0

    def mark(self, name: str) -> float:
        now = time.perf_counter()
        delta_ms = (now - self._last) * 1000
        total_ms = (now - self.t0) * 1000
        self.marks.append((name, delta_ms))
        self._last = now
        log(f"⏱  {name}: {delta_ms:.0f}ms (total {total_ms:.0f}ms)")
        return delta_ms

    def report(self, title: str = "TIMER REPORT") -> None:
        total = (time.perf_counter() - self.t0) * 1000
        log("=" * 48)
        log(title)
        for name, ms in self.marks:
            bar = "█" * min(int(ms / 25), 40)
            log(f"  {ms:7.0f}ms  {name}  {bar}")
        log(f"  {total:7.0f}ms  TOTAL")
        log("=" * 48)


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
        help="Seconds to leave the browser open after success/failure (default 30)",
    )
    p.add_argument(
        "--stay-open",
        action="store_true",
        help="Do not auto-close Chrome; keep session warm until YOU close the window",
    )
    return p


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: WPS433
    except ImportError:
        die(
            "Playwright not installed. Run:\n"
            "  pip install -r requirements.txt\n"
            "  playwright install chromium"
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


def _pref_matches(option: dict, pref: str) -> bool:
    """True if preference matches autom / value / label of a dimension option."""
    want = _norm_pref(pref)
    if not want:
        return False
    haystacks = [
        _norm_pref(option.get("autom") or ""),
        _norm_pref(option.get("value") or ""),
        _norm_pref(option.get("label") or ""),
    ]
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
            return {
              autom: el.getAttribute('data-autom') || '',
              value: el.value || '',
              checked: !!el.checked,
              disabled: !!el.disabled,
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
) -> str:
    """
    Pick first enabled option matching ordered prefs.
    Returns selected autom/value. Raises if none match.
    """
    deadline = time.perf_counter() + timeout_ms / 1000
    last_opts: list[dict] = []
    while time.perf_counter() < deadline:
        opts = _list_dimension_options(page, dimension_name)
        last_opts = opts
        enabled = [o for o in opts if not o.get("disabled")]
        if not enabled:
            page.wait_for_timeout(120)
            continue
        for o in enabled:
            if o.get("checked") and any(_pref_matches(o, p) for p in prefs):
                picked = o.get("autom") or o.get("value") or "?"
                log(f"Dimension {dimension_name} already selected: {picked}")
                if timer and mark:
                    timer.mark(mark)
                return str(picked)
        chosen = None
        for pref in prefs:
            for o in enabled:
                if _pref_matches(o, pref):
                    chosen = o
                    break
            if chosen:
                break
        if not chosen:
            chosen = enabled[0]
            log(
                f"WARNING: no pref matched for {dimension_name}; "
                f"failover → {chosen.get('autom') or chosen.get('value')}"
            )
        autom = chosen.get("autom") or ""
        sel = (
            f'[data-autom="{autom}"]'
            if autom
            else f'input[name="{dimension_name}"][value="{chosen.get("value")}"]'
        )
        hint = (chosen.get("label") or "").split(" Chú thích")[0].strip()
        _select_radio_until_checked(
            page,
            sel,
            label=f"{dimension_name}:{autom or chosen.get('value')}",
            text_hints=[hint] if hint else None,
            timeout_ms=5_000,
            attempts=4,
        )
        picked = autom or chosen.get("value") or "?"
        log(f"Selected {dimension_name}: {picked} ({(chosen.get('label') or '')[:40]})")
        if timer and mark:
            timer.mark(mark)
        return str(picked)
    raise RuntimeError(
        f"Dimension {dimension_name} never became selectable; last={last_opts!r}"
    )


def wait_family_configure_ready(
    page,
    family_urls: list[str],
    *,
    timer: StageTimer | None = None,
    poll_ms: int = 400,
    timeout_sec: int = 180,
) -> str:
    """
    Poll family buy URL(s) until dimension radios unlock (launch / configure ready).
    Returns the URL that became ready.
    """
    urls = [u for u in family_urls if u]
    if not urls:
        raise RuntimeError("No family_url candidates to poll")
    deadline = time.perf_counter() + timeout_sec
    log(f"Polling configure unlock ({len(urls)} URL(s), up to {timeout_sec}s)…")
    idx = 0
    while time.perf_counter() < deadline:
        url = urls[idx % len(urls)]
        idx += 1
        try:
            if urlparse(page.url).path.rstrip("/") != urlparse(url).path.rstrip("/"):
                goto_resilient(page, url)
            else:
                page.reload(wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            log(f"Poll nav issue: {exc}")
            page.wait_for_timeout(poll_ms)
            continue
        if _is_apple_404(page):
            page.wait_for_timeout(poll_ms)
            continue
        ready = page.evaluate(
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
        if ready:
            log(f"Configure unlocked: {page.url}")
            if timer:
                timer.mark("0a configure unlocked")
            notify_macos("Assist — configure unlocked", page.url[:80])
            return page.url
        page.wait_for_timeout(poll_ms)
    raise RuntimeError(f"Configure did not unlock within {timeout_sec}s (tried {urls})")


def select_product_dimensions(
    page,
    prefs: dict,
    *,
    timer: StageTimer | None = None,
) -> None:
    """
    Family-page SKU pick (order Apple uses):
      Screensize (Pro) → Color → Capacity
    Then trade-in becomes enabled for the existing decline path.
    """
    screensizes = [str(x) for x in (prefs.get("screensizes") or prefs.get("sizes") or [])]
    colors = [str(x) for x in (prefs.get("colors") or [])]
    storages = [str(x) for x in (prefs.get("storages") or prefs.get("capacities") or [])]
    if not colors and not storages and not screensizes:
        raise RuntimeError("product_prefs needs colors and/or storages (and sizes for Pro)")

    size_opts = _list_dimension_options(page, "dimensionScreensize")
    if size_opts:
        _select_dimension_by_prefs(
            page,
            "dimensionScreensize",
            screensizes or ["6.3", "6,3", "6_3"],
            timer=timer,
            mark="0d screensize",
        )
        page.wait_for_function(
            """() => {
              const els = document.querySelectorAll('input[name="dimensionColor"]');
              return Array.from(els).some((el) => !el.disabled);
            }""",
            timeout=10_000,
        )

    if colors or _list_dimension_options(page, "dimensionColor"):
        _select_dimension_by_prefs(
            page,
            "dimensionColor",
            colors or ["black", "Đen"],
            timer=timer,
            mark="0e color",
        )
        page.wait_for_function(
            """() => {
              const els = document.querySelectorAll('input[name="dimensionCapacity"]');
              return Array.from(els).some((el) => !el.disabled);
            }""",
            timeout=10_000,
        )

    if storages or _list_dimension_options(page, "dimensionCapacity"):
        _select_dimension_by_prefs(
            page,
            "dimensionCapacity",
            storages or ["256gb", "256"],
            timer=timer,
            mark="0f capacity",
        )

    page.wait_for_function(
        """() => {
          const t = document.querySelector(
            '[data-autom="choose-noTradeIn"], #noTradeIn, input[value="noTradeIn"]'
          );
          return !!(t && !t.disabled);
        }""",
        timeout=12_000,
    )
    log(f"Dimensions done — trade-in ready at {page.url}")
    if timer:
        timer.mark("0g dimensions complete (trade-in ready)")


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


def goto_resilient(page, url: str, attempts: int = 3) -> None:
    """Apple sometimes aborts the first navigation right after login redirects."""
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc)
            log(f"Navigation attempt {i}/{attempts} failed: {msg.splitlines()[0][:160]}")
            # If the abort still landed us on (or near) the target, continue.
            current = page.url
            if "buy-iphone" in current or urlparse(current).path.rstrip("/") == urlparse(url).path.rstrip("/"):
                log(f"Already on a usable page after abort: {current}")
                return
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


def _click_radio_strategies(page, input_selector: str, *, text_hints: list[str] | None = None) -> str:
    """
    Try several real-UI click strategies (never fake .checked).
    Returns which strategy was attempted last.
    """
    page.locator(input_selector).first.wait_for(state="attached", timeout=5_000)
    hints = text_hints or []

    # 1) Associated label / form-selector click (in-page — beats sticky intercept)
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
    if _radio_is_checked(page, input_selector):
        return via

    # 2) Visible Vietnamese/English text on page (AppleCare often needs this)
    for hint in hints:
        try:
            loc = page.get_by_text(hint, exact=False)
            if loc.count() == 0:
                continue
            target = loc.first
            target.scroll_into_view_if_needed(timeout=800)
            target.click(force=True, timeout=1_200, no_wait_after=True)
            if _radio_is_checked(page, input_selector):
                return f"text:{hint}"
        except Exception:  # noqa: BLE001
            continue

    # 3) Playwright force-click on the input itself
    try:
        page.locator(input_selector).first.click(
            force=True, timeout=1_200, no_wait_after=True
        )
        if _radio_is_checked(page, input_selector):
            return "pw-force-input"
    except Exception:  # noqa: BLE001
        pass

    # 4) Playwright force-click label[for=id]
    try:
        input_id = page.locator(input_selector).first.get_attribute("id")
        if input_id:
            page.locator(f'label[for="{input_id}"]').first.click(
                force=True, timeout=1_200, no_wait_after=True
            )
            if _radio_is_checked(page, input_selector):
                return "pw-force-label"
    except Exception:  # noqa: BLE001
        pass

    return via or "failed"


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
            log(f"{label}: already checked (attempt {i + 1})")
            return (time.perf_counter() - t0) * 1000
        last_via = _click_radio_strategies(page, input_selector, text_hints=text_hints)
        # Short poll for React to commit checked state
        try:
            page.wait_for_function(
                """(selector) => {
                  const el = document.querySelector(selector);
                  return !!(el && el.checked);
                }""",
                arg=input_selector,
                timeout=min(900, max(200, int((deadline - time.perf_counter()) * 1000))),
            )
            log(f"{label}: checked via {last_via} (attempt {i + 1})")
            return (time.perf_counter() - t0) * 1000
        except Exception:  # noqa: BLE001
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
    timer.marks.append(("0 prefind handles", prefind_ms))
    timer._last = time.perf_counter()
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
    timer.marks.append(("1 no trade-in (verify)", trade_ms))
    timer._last = time.perf_counter()

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
    timer.marks.append(("2 wait AppleCare mount", care_mount_ms))
    timer._last = time.perf_counter()

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
    timer.marks.append(("3 no AppleCare (verify)", care_ms))
    timer._last = time.perf_counter()

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
    timer.marks.append(("4 wait add ready (both verified)", add_ready_ms))
    timer._last = time.perf_counter()
    log("Verified before add: trade=Y applecare=Y both=Y")

    # Brief settle: Apple enables add before purchase-option AJAX finishes.
    t = time.perf_counter()
    page.wait_for_timeout(50)
    settle_ms = (time.perf_counter() - t) * 1000
    timer.marks.append(("4b settle after verify", settle_ms))
    timer._last = time.perf_counter()

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
    timer.marks.append(("5 click add-to-cart", add_click_ms))
    timer._last = time.perf_counter()
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
        timer.marks.append(
            ("6 add → 404 (blocked/bad session)", (time.perf_counter() - t_attach) * 1000)
        )
        timer._last = time.perf_counter()
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
            page.wait_for_url(
                re.compile(r".*(step=attach|/shop/bag).*", re.I),
                timeout=6_000,
            )
            attach_ok = True
        except Exception as exc:  # noqa: BLE001
            log(f"Retry add failed: {exc}")
        timer.marks.append(
            ("5b retry add → attach", (time.perf_counter() - t_retry) * 1000)
        )
        timer._last = time.perf_counter()

    if attach_ok:
        if "/shop/404" in page.url.lower():
            raise RuntimeError(f"Landed on 404 after add: {page.url}")
        first_leg = (time.perf_counter() - t_attach) * 1000
        # If we already recorded retry, mark 6 as confirmation only
        timer.mark("6 attach/bag confirmed after add")
        log(f"Verified after add ({first_leg:.0f}ms since first add click): {page.url}")
    else:
        timer.marks.append(
            ("6 attach page (miss → bag fallback)", (time.perf_counter() - t_attach) * 1000)
        )
        timer._last = time.perf_counter()
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
        timer.mark("7 already on bag")
        return

    # Direct bag URL from attach — skip proceed hop
    goto_resilient(page, "https://www.apple.com/vn/shop/bag")
    timer.mark("7 bag page (direct)")
    log(f"At bag ({(time.perf_counter() - t0) * 1000:.0f}ms): {page.url}")


def click_thanh_toan_now(page, timer: StageTimer | None = None) -> None:
    """From bag, enter checkout form. Never place the order."""
    timer = timer or StageTimer()
    checkout = page.locator('[data-autom="checkout"]')
    checkout.first.wait_for(state="attached", timeout=5_000)
    for attempt in range(3):
        page.evaluate(
            """() => {
              const btn = document.querySelector('[data-autom="checkout"]');
              if (btn) btn.click();
            }"""
        )
        if attempt == 0:
            timer.mark("9 click Thanh Toán")
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
        if _is_apple_404(page):
            log(f"Thanh Toán hit Apple 404 (attempt {attempt + 1}) — retry from bag")
            goto_resilient(page, "https://www.apple.com/vn/shop/bag")
            page.locator('[data-autom="checkout"]').first.wait_for(
                state="attached", timeout=8_000
            )
            continue
        break
    timer.mark("10 checkout-ready page")
    log(f"Checkout-ready at: {page.url}")
    if _is_apple_404(page):
        raise RuntimeError(f"Thanh Toán landed on Apple 404: {page.url}")


# Hard stop — never click these (purchase / review commit)
_CHECKOUT_FORBIDDEN_AUTOM = {
    "continue-button-review",  # "Xem Lại Đơn Hàng" — past payment stop point
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
    return bool(re.search(r"placeorder|place-order|place_order", a, re.I))


def _click_autom(page, autom: str, *, timeout_ms: int = 8_000) -> None:
    if _is_forbidden_checkout_autom(autom):
        raise RuntimeError(f"Refusing to click forbidden control: {autom}")
    page.locator(f'[data-autom="{autom}"]').first.wait_for(
        state="attached", timeout=timeout_ms
    )
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


def empty_bag(page, *, max_rounds: int = 12) -> None:
    """Remove every line item from /vn/shop/bag so the timed run starts clean."""
    goto_resilient(page, "https://www.apple.com/vn/shop/bag")
    page.wait_for_timeout(400)
    if _bag_is_empty(page):
        log("Bag already empty")
        return

    log("Emptying bag after SSO warm…")
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
            break
        page.wait_for_timeout(900)
        if "/shop/bag" not in page.url.lower():
            goto_resilient(page, "https://www.apple.com/vn/shop/bag")
            page.wait_for_timeout(400)

    if _bag_is_empty(page):
        log("Bag emptied — ready for timed run")
    else:
        # Last resort: still no empty marker but checkout gone
        if not _bag_has_checkout(page):
            log("Bag looks clear (no checkout button)")
        else:
            raise RuntimeError(
                "Could not empty bag after SSO warm — remove items manually, then re-run"
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
) -> None:
    """
    Pre-warm secure*.store.apple.com checkout SSO (separate from account login).

    Launch-day reality: basket starts empty. So warm must:
      1) clear bag
      2) add one practice iPhone
      3) Thanh Toán → pass /signIn to Fulfillment (sets checkout SSO cookies)
      4) empty bag again
    Timed sprint then starts with an empty bag + warm SSO.
    """
    log("Pre-warm checkout SSO (secure store) — not timed…")
    log("Plan: empty bag → add iPhone → checkout SSO → empty bag again")
    t0 = time.perf_counter()

    # Start clean so we don't stack leftovers from prior practice
    empty_bag(page)

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

    # Critical: leave basket empty for the real timed run
    empty_bag(page)
    log("SSO warm complete — bag empty, secure-store session kept. Ready for T-0.")


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
    page, autom: str, label: str, *, wait_sec: float = 12.0, poll_ms: int = 50
) -> bool:
    """Select a <select data-autom=...> option by visible label.

    Uses Playwright select_option — React-controlled Apple checkout ignores
    synthetic input/change events on .value alone.
    """
    if not label:
        return False
    sel = page.locator(f'select[data-autom="{autom}"]').first
    try:
        sel.wait_for(state="attached", timeout=5_000)
    except Exception:  # noqa: BLE001
        return False

    deadline = time.time() + wait_sec
    while time.time() < deadline:
        try:
            # Re-query each attempt — Apple remounts selects after cascade changes
            sel = page.locator(f'select[data-autom="{autom}"]').first
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
                const nodes = Array.from(
                  document.querySelectorAll('[data-autom="' + autom + '"]')
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


def _select_option_native(page, autom: str, label: str, *, wait_sec: float = 12.0) -> bool:
    """Select option in-page (native setter). Falls back to Playwright select_option."""
    if not label:
        return False
    want = label.strip()
    deadline = time.time() + wait_sec
    saw_option = False
    while time.time() < deadline:
        status = page.evaluate(
            """({ autom, label }) => {
              const sel = document.querySelector('select[data-autom="' + autom + '"]');
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
            {"autom": autom, "label": want},
        ) or {}
        if status.get("ok"):
            return True
        if status.get("found"):
            saw_option = True
            # Option exists but React ignored native setter — Playwright once
            if _select_option_by_label(page, autom, want, wait_sec=1.5, poll_ms=40):
                return True
        page.wait_for_timeout(40)
    if saw_option:
        return _select_option_by_label(page, autom, want, wait_sec=2.0, poll_ms=40)
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


def _set_fulfillment_location(page, city: str, district: str) -> None:
    """
    ALWAYS open location editor and pick city + quận (HCM + Quận Bình Thạnh).
    Never skip — city-only HCM is not enough.
    """
    city = city or "Thành phố Hồ Chí Minh"
    district = district or "Quận Bình Thạnh"
    log(f"Location edit REQUIRED: {city} → {district}")

    _click_autom(page, "checkout-zipcode-edit", timeout_ms=8_000)
    page.locator('select[data-autom="form-field-state"]').first.wait_for(
        state="visible", timeout=8_000
    )

    # 1) City / tỉnh-TP
    for autom in ("form-field-state", "checkout-zipcode-city", "checkout-zipcode-state"):
        if page.locator(f'select[data-autom="{autom}"]').count() > 0:
            if _select_option_by_label(page, autom, city, wait_sec=8.0):
                log(f"Location city via select {autom}")
                break
    else:
        hit = _click_visible_text(
            page,
            city,
            "Thành phố Hồ Chí Minh",
            "Hồ Chí Minh",
        )
        log(f"Location city via text: {hit}")

    # 2) Quận — wait on option list, not a fixed sleep
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
    for autom in ("form-field-city", "checkout-zipcode-district", "form-field-district"):
        if page.locator(f'select[data-autom="{autom}"]').count() > 0:
            if _select_option_by_label(page, autom, district, wait_sec=6.0) or _select_option_by_label(
                page, autom, "Bình Thạnh", wait_sec=3.0
            ):
                log(f"Location quận via select {autom}")
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
        log(f"Location quận via text: {hit}")

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
                btn = page.get_by_role("button", name=re.compile(f"^{label}$", re.I))
                if btn.count() > 0:
                    btn.first.click(force=True, timeout=1_200, no_wait_after=True)
                    applied = True
                    log(f"Location apply via button {label!r}")
                    break
            except Exception:  # noqa: BLE001
                continue
    if not applied:
        log("WARNING: no location Apply button found")

    # Proceed as soon as delivery options / continue are usable again
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


def _fill_new_shipping_address(
    page,
    addr: dict,
    contact: dict | None = None,
    timer: StageTimer | None = None,
) -> None:
    """Select 'Sử dụng địa chỉ mới', fill address + contact (prefind/verify style)."""
    contact = contact or {}
    log("Selecting Sử dụng địa chỉ mới + filling address + contact")
    page.locator('input[data-autom="newAddress"]').first.wait_for(
        state="attached", timeout=15_000
    )
    _select_radio_until_checked(
        page,
        'input[data-autom="newAddress"]',
        label="newAddress",
        text_hints=["Sử dụng địa chỉ mới"],
        timeout_ms=6_000,
        attempts=5,
    )
    page.wait_for_function(
        """() => !!document.querySelector('select[data-autom="form-field-state"]')""",
        timeout=5_000,
    )
    if timer:
        timer.mark("15a newAddress selected (verified)")

    state = addr.get("state") or "Thành phố Hồ Chí Minh"
    city = addr.get("city") or "Quận Bình Thạnh"
    district = addr.get("district") or ""

    # Skip cascade selects when fulfillment already seeded the right values
    cur_state = _select_current_label(page, "form-field-state")
    if state.lower() not in cur_state.lower() and "hồ chí minh" not in cur_state.lower():
        if not _select_option_native(page, "form-field-state", state, wait_sec=8.0):
            raise RuntimeError(f"Could not select state/tỉnh {state!r}")
        log(f"Selected state: {state}")
        if timer:
            timer.mark("15b1 select state/tỉnh")
    else:
        log(f"State already set: {cur_state}")
        if timer:
            timer.mark("15b1 state already set (skip)")

    cur_city = _select_current_label(page, "form-field-city")
    if city.lower() not in cur_city.lower() and "bình thạnh" not in cur_city.lower():
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
        t0 = time.perf_counter()
        cur_d = _select_current_label(page, "form-field-district")
        if district.lower() not in cur_d.lower():
            # Playwright select_option — React commits this reliably
            if not _select_option_by_label(
                page, "form-field-district", district, wait_sec=12.0
            ):
                opts = page.evaluate(
                    """() => {
                      const s = document.querySelector('select[data-autom="form-field-district"]');
                      return s ? Array.from(s.options).map((o) => (o.textContent || '').trim()) : [];
                    }"""
                )
                raise RuntimeError(
                    f"Could not select phường/district {district!r}; options={opts!r}"
                )
            log(f"Selected phường trước sáp nhập: {district}")
        else:
            # Re-select anyway so React validation sees a change event
            _select_option_by_label(page, "form-field-district", district, wait_sec=2.0)
            log(f"Phường confirmed: {district}")
        ms = (time.perf_counter() - t0) * 1000
        log(f"Phường step done ({ms:.0f}ms)")
        if timer:
            timer.mark("15d phường")


def advance_checkout_to_payment(
    page,
    timer: StageTimer | None = None,
    *,
    login_timeout_sec: int = 300,
    checkout_cfg: dict | None = None,
) -> None:
    """
    Apple VN path (dry-run stop at payment):
      Fulfillment (HCM + Bình Thạnh) → Shipping (new address) → Billing (saved card)
    Never clicks review / Đặt hàng.
    """
    timer = timer or StageTimer()
    checkout_cfg = checkout_cfg or {}
    loc_city = checkout_cfg.get("location_city") or "Thành phố Hồ Chí Minh"
    loc_district = checkout_cfg.get("location_district") or "Quận Bình Thạnh"
    addr = checkout_cfg.get("address") or {}
    if not isinstance(addr, dict):
        addr = {}
    contact = checkout_cfg.get("contact") or {}
    if not isinstance(contact, dict):
        contact = {}

    wait_checkout_signin_if_needed(page, login_timeout_sec)
    timer.mark("10b checkout SSO ready")

    # ===== Fulfillment =====
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
    timer.mark("11 fulfillment page")
    log(f"Fulfillment at: {page.url} (_s={_checkout_step(page)})")
    _prefind_automs(
        page,
        {
            "zipEdit": "checkout-zipcode-edit",
            "fulfillContinue": "fulfillment-continue-button",
            "deliveryS1": "fulfillment-option-S1",
        },
    )

    _set_fulfillment_location(page, loc_city, loc_district)
    timer.mark("12 set location HCM/Bình Thạnh")

    # Ensure a delivery option is selected (prefer S1)
    page.evaluate(
        """() => {
          const prefer = document.querySelector('input[data-autom="fulfillment-option-S1"]');
          const checked = document.querySelector(
            'input[data-autom^="fulfillment-option-"]:checked'
          );
          const target = prefer || checked || document.querySelector('input[data-autom^="fulfillment-option-"]');
          if (!target || target.checked) return;
          const label = target.id
            ? document.querySelector('label[for="' + target.id + '"]')
            : null;
          (label || target).click();
        }"""
    )

    page.wait_for_function(
        """() => {
          const btn = document.querySelector('[data-autom="fulfillment-continue-button"]');
          return !!(btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true');
        }""",
        timeout=10_000,
    )
    timer.mark("12b fulfillment continue enabled")

    # Same pattern as bag → Thanh Toán: click immediately (label/el), then wait for next step
    shipping_ok = False
    for attempt in range(3):
        try:
            _click_autom(page, "fulfillment-continue-button", timeout_ms=3_000)
        except Exception as exc:  # noqa: BLE001
            log(f"Fulfillment continue click failed (attempt {attempt + 1}): {exc}")
            page.wait_for_timeout(120)
            continue
        if attempt == 0:
            timer.mark("13 click fulfillment continue")
        try:
            # Wait for Shipping URL AND form controls (URL can flip before React mounts)
            page.wait_for_function(
                """() => {
                  const u = location.href;
                  const onShip = u.includes('Shipping') || u.includes('_s=Shipping');
                  const form = !!document.querySelector(
                    'input[data-autom="newAddress"], [data-autom="shipping-continue-button"]'
                  );
                  return onShip && form;
                }""",
                timeout=15_000,
            )
            shipping_ok = True
            break
        except Exception:  # noqa: BLE001
            log(
                f"Shipping not reached (attempt {attempt + 1}, "
                f"url={page.url}, _s={_checkout_step(page)})"
            )
            page.wait_for_timeout(150)
    if not shipping_ok:
        raise RuntimeError(
            f"Did not reach shipping after fulfillment (url={page.url}, _s={_checkout_step(page)})"
        )
    timer.mark("13b Shipping page + form")
    log(f"Shipping at: {page.url} (_s={_checkout_step(page)})")
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

    if checkout_cfg.get("use_new_address", True):
        _fill_new_shipping_address(page, addr, contact, timer=timer)
        # Quick verify (fields already checked during fill)
        page.wait_for_function(
            """() => {
              const n = document.querySelector('input[data-autom="newAddress"]');
              const street = document.querySelector('[data-autom="form-field-street"]');
              const district = document.querySelector('select[data-autom="form-field-district"]');
              const districtOk = !district || !!(district.value && district.value.trim());
              return !!(n && n.checked && street && street.value && street.value.length > 3 && districtOk);
            }""",
            timeout=3_000,
        )
        timer.mark("15e address verified")
    else:
        page.evaluate(
            """() => {
              const el = document.querySelector('input[data-autom="saved-address"]');
              if (!el) return;
              const label = el.id
                ? document.querySelector('label[for="' + el.id + '"]') : null;
              (label || el).click();
            }"""
        )
        timer.mark("15 select saved address")

    # Prefind → wait enabled → click (in-page then Playwright force, like pre-SSO)
    page.wait_for_function(
        """() => {
          const btn = document.querySelector('[data-autom="shipping-continue-button"]');
          return !!(btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true');
        }""",
        timeout=10_000,
    )
    timer.mark("16a shipping continue enabled")
    _prefind_automs(page, {"shipContinue": "shipping-continue-button"})

    billing_ok = False
    for attempt in range(3):
        try:
            _click_autom(page, "shipping-continue-button", timeout_ms=3_000)
        except Exception as exc:  # noqa: BLE001
            log(f"Shipping continue click failed (attempt {attempt + 1}): {exc}")
            try:
                page.locator('[data-autom="shipping-continue-button"]').first.click(
                    force=True, timeout=1_500, no_wait_after=True
                )
            except Exception:  # noqa: BLE001
                page.wait_for_timeout(120)
                continue
        if attempt == 0:
            timer.mark("16b click Tiếp tục đến Thanh Toán")
        try:
            # Require payment options mounted — Billing URL alone is ~0.8s early
            page.wait_for_function(
                """() => !!document.querySelector(
                  '[data-autom="checkout-billingOptions-SAVED_CARD"], [data-autom^="checkout-billingOptions-"]'
                )""",
                timeout=15_000,
            )
            billing_ok = True
            break
        except Exception:  # noqa: BLE001
            errs = page.evaluate(
                """() => Array.from(document.querySelectorAll(
                  '[class*="error"], [aria-invalid="true"], .form-message-error, [data-autom*="error"]'
                )).map((e) => (e.innerText || '').trim()).filter(Boolean).slice(0, 6)"""
            )
            step = _checkout_step(page)
            log(
                f"Still not on billing (attempt {attempt + 1}, _s={step})"
                + (f" errors={errs!r}" if errs else "")
            )
            page.wait_for_timeout(200)

    if not billing_ok:
        raise RuntimeError(
            f"Did not reach billing/payment page (url={page.url}, _s={_checkout_step(page)})"
        )
    timer.mark("17 billing + payment options")
    log(f"Billing at: {page.url} (_s={_checkout_step(page)})")
    _prefind_automs(
        page,
        {
            "savedCard": "checkout-billingOptions-SAVED_CARD",
            "reviewBtn": "continue-button-review",
        },
    )

    # Prefind → click → verify (same as declines)
    _click_autom(page, "checkout-billingOptions-SAVED_CARD", timeout_ms=5_000)
    _verify_checked(page, "checkout-billingOptions-SAVED_CARD", timeout_ms=5_000)
    timer.mark("18 select saved card (verified)")
    log("Selected SAVED_CARD — STOPPING before review / Đặt hàng.")


def click_next_steps(
    page,
    timer: StageTimer | None = None,
    *,
    login_timeout_sec: int = 300,
    checkout_cfg: dict | None = None,
) -> None:
    """
    After add-to-cart: Xem Giỏ Hàng → bag → Thanh Toán →
    Fulfillment (HCM/Bình Thạnh) → new shipping address → Billing (saved card).
    STOP before review / place order.
    """
    timer = timer or StageTimer()
    click_xem_gio_hang_now(page, timer)

    if "/shop/bag" in page.url.lower() or "checkout" not in page.url.lower():
        click_thanh_toan_now(page, timer)

    advance_checkout_to_payment(
        page,
        timer,
        login_timeout_sec=login_timeout_sec,
        checkout_cfg=checkout_cfg,
    )

    timer.report("EFFICIENCY TIMER")
    log("DRY-RUN STOP — at payment method (saved card). Do NOT click review / Đặt hàng.")
    notify_macos(
        "Assist — stopped at payment",
        "Saved card selected. YOU continue / place order — assist will not.",
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
        if family_url:
            family_candidates.append(family_url)
        for u in family_urls_cfg:
            if isinstance(u, str) and u.strip():
                family_candidates.append(validate_store_url(u.strip(), "family_urls"))
        product_prefs = cfg.get("product_prefs") if isinstance(cfg.get("product_prefs"), dict) else {}
        use_dynamic = bool(product_prefs) or bool(family_candidates)
        unlock_timeout = args.unlock_timeout_sec or int(cfg.get("unlock_timeout_sec") or 180)
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

            do_warm = args.setup_login or args.warm_only or args.warmup_before_run
            if do_warm:
                log(
                    "Warming account + checkout SSO NOW (before timed sprint / T-0)…"
                )
                try:
                    warm_checkout_sso(
                        page,
                        product_url=warm_product_url,
                        no_trade=no_trade,
                        no_care=no_care,
                        login_timeout_sec=args.login_timeout_sec,
                        timeout_ms=args.timeout_ms,
                    )
                except Exception as warm_exc:  # noqa: BLE001
                    if args.warm_only or args.setup_login:
                        raise
                    log(f"Checkout SSO warm failed (continuing timed run): {warm_exc}")

            if args.setup_login or args.warm_only:
                log(
                    "WARM DONE (SSO warm + bag emptied). Leave Chrome open. "
                    "At T-0:  python assist.py"
                )
                notify_macos(
                    "Assist warm done",
                    "SSO warm, bag empty. Leave Chrome open; run assist.py at T-0.",
                )
            else:
                if not args.warmup_before_run:
                    log(
                        "Timed run — no SSO warm in this path "
                        "(pre-warm earlier: python assist.py --warm-only)"
                    )

                timer = StageTimer()
                if use_dynamic:
                    candidates = family_candidates or [_product_family_url(product_url)]
                    log(f"Dynamic timed run — poll/select on {candidates}")
                    wait_family_configure_ready(
                        page,
                        candidates,
                        timer=timer,
                        timeout_sec=unlock_timeout,
                    )
                    select_product_dimensions(
                        page,
                        product_prefs or {},
                        timer=timer,
                    )
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
                    "Assist done — stopped at payment",
                    f"{label}: at saved-card payment. Do NOT place order (dry-run).",
                )
                log("SUCCESS: reached billing with saved card selected.")
                log("STOPPING before review / Đặt hàng.")

            # Brief pause so you can see the page — Chrome stays open either way
            page.wait_for_timeout(max(args.keep_open_sec, 3) * 1000)
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            log(f"Assist failed: {exc}")
            notify_macos("Assist failed", str(exc)[:120])
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
