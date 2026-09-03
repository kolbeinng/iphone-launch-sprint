#!/usr/bin/env python3
"""
Checkout probe for Apple VN — map steps AFTER bag until place-order is visible.

Goal: learn selectors/URLs so assist can stop one click before purchase.
Never clicks place-order / Đặt hàng / Complete Order.

Modes:
  python probe_checkout.py                 # bag → checkout → auto-continue probe
  python probe_checkout.py --watch         # YOU fill address/delivery; we log each step
  python probe_checkout.py --from-assist   # product → bag → checkout → probe
  python probe_checkout.py --setup-login   # sign in only
  python probe_checkout.py --stay-open     # keep Chrome warm until you close it
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from sprint_common import (
    ASSIST_PROFILE_DIR,
    DEFAULT_CONFIG,
    DEFAULT_SESSION_URL,
    ROOT,
    ConfigError,
    die,
    disconnect_assist_browser,
    launch_assist_browser,
    load_config,
    log,
    notify_macos,
    require_dry_run,
    validate_store_url,
)

PROFILE_DIR = ASSIST_PROFILE_DIR
REPORT_PATH = ROOT / "probe-report.json"

# Hard stop — never click these (purchase)
PURCHASE_AUTOM = re.compile(
    r"placeorder|place-order|place_order|completeorder|confirmorder|submitorder",
    re.I,
)
PURCHASE_TEXT = re.compile(
    r"đặt hàng|place order|complete order|confirm order|pay now|thanh toán ngay|"
    r"hoàn tất đơn|mua ngay$",
    re.I,
)

# Safe-ish continues to advance checkout (still never purchase)
CONTINUE_AUTOM = (
    "continue",
    "continue-button",
    "continueButton",
    "continueButton-continue",
    "shipping-continue",
    "billing-continue",
    "payment-continue",
    "fulfillment-continue",
    "guest-checkout",
    "proceed",
    "checkout-continue",
    "address-continue",
    "select-address",
)
CONTINUE_TEXT = re.compile(
    r"^tiếp tục$|^continue$|^tiếp$|^áp dụng$|^apply$|^lưu$|^save$|"
    r"^giao đến địa chỉ này$|^use this address$|^dùng địa chỉ này$|"
    r"^sử dụng địa chỉ này$|^giao hàng đến địa chỉ này$|"
    r"^tiếp tục đến thanh toán$|^continue to payment$",
    re.I,
)
LOCATION_AUTOM = (
    "checkout-zipcode-edit",
    "checkout-zipcode",
    "fulfillment-zipcode",
    "zipCode",
    "postalCode",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Probe Apple VN checkout pages up to (but not including) place order."
    )
    p.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--from-assist",
        action="store_true",
        help="Run product declines + add-to-cart first (via assist helpers), then probe",
    )
    p.add_argument(
        "--setup-login",
        action="store_true",
        help="Only complete Apple ID sign-in in the assist profile",
    )
    p.add_argument("--login-timeout-sec", type=int, default=600)
    p.add_argument(
        "--max-steps",
        type=int,
        default=8,
        help="Max safe continue clicks while probing (default 8)",
    )
    p.add_argument(
        "--keep-open-sec",
        type=int,
        default=90,
        help="Leave browser open so you can inspect (default 90)",
    )
    p.add_argument(
        "--stay-open",
        action="store_true",
        help="Do not auto-close Chrome; keep checkout SSO warm until YOU close the window",
    )
    p.add_argument(
        "--watch",
        action="store_true",
        help="Manual mode: you fill location/address/pay; probe logs each page change",
    )
    p.add_argument(
        "--watch-sec",
        type=int,
        default=600,
        help="How long --watch listens for page changes (default 600s)",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=REPORT_PATH,
        help=f"Write JSON report path (default {REPORT_PATH.name})",
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


def snapshot_page(page) -> dict:
    """Capture URL + actionable checkout controls (read-only)."""
    return page.evaluate(
        """() => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
          const visible = (el) => {
            if (!el) return false;
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
          };
          const pick = (el) => ({
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            autom: el.getAttribute('data-autom') || '',
            id: el.id || '',
            name: el.getAttribute('name') || '',
            text: norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').slice(0, 80),
            disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
            href: (el.getAttribute('href') || '').slice(0, 120),
          });

          const nodes = Array.from(document.querySelectorAll(
            'button, a[role="button"], input[type="submit"], input[type="button"], [data-autom]'
          ));
          const controls = [];
          const seen = new Set();
          for (const el of nodes) {
            if (!visible(el)) continue;
            const info = pick(el);
            const key = [info.autom, info.id, info.text, info.tag].join('|');
            if (seen.has(key)) continue;
            seen.add(key);
            controls.push(info);
          }

          const fields = Array.from(document.querySelectorAll('input, select, textarea'))
            .filter(visible)
            .slice(0, 60)
            .map((el) => ({
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || '',
              autom: el.getAttribute('data-autom') || '',
              id: el.id || '',
              name: el.getAttribute('name') || '',
              placeholder: el.getAttribute('placeholder') || '',
              autocomplete: el.getAttribute('autocomplete') || '',
              checked: !!el.checked,
              value: (el.type === 'password' ? '' : String(el.value || '')).slice(0, 40),
            }));

          const radios = Array.from(document.querySelectorAll('input[type="radio"]'))
            .filter(visible)
            .slice(0, 30)
            .map((el) => {
              const label = el.id
                ? document.querySelector('label[for="' + el.id + '"]')
                : el.closest('label');
              return {
                autom: el.getAttribute('data-autom') || '',
                id: el.id || '',
                name: el.getAttribute('name') || '',
                value: el.value || '',
                checked: !!el.checked,
                label: norm(label ? label.innerText : '').slice(0, 80),
              };
            });

          const headings = Array.from(document.querySelectorAll('h1, h2, h3, [role="heading"]'))
            .filter(visible)
            .map((el) => norm(el.innerText).slice(0, 100))
            .filter(Boolean)
            .slice(0, 12);

          const body = norm(document.body ? document.body.innerText : '').slice(0, 800);
          const step = (location.search.match(/[?&]_s=([^&]+)/) || [])[1] || '';

          return {
            url: location.href,
            title: document.title,
            checkoutStep: step,
            headings,
            controls,
            fields,
            radios,
            bodySnippet: body,
            hasPlaceOrderHint: /đặt hàng|place order|complete order/i.test(body),
            hasSignInHint: /đăng nhập|sign in|apple id/i.test(body),
            hasLocationHint: /chọn địa điểm|mã bưu chính|zip|địa chỉ giao hàng|fulfillment/i.test(body),
          };
        }"""
    )


def classify_controls(snap: dict) -> dict:
    purchase, continues, other = [], [], []
    for c in snap.get("controls") or []:
        autom = c.get("autom") or ""
        text = c.get("text") or ""
        if PURCHASE_AUTOM.search(autom) or PURCHASE_TEXT.search(text):
            purchase.append(c)
        elif autom in CONTINUE_AUTOM or (text and CONTINUE_TEXT.search(text)):
            continues.append(c)
        else:
            other.append(c)
    return {"purchase": purchase, "continues": continues, "other": other}


def is_purchase_control(c: dict) -> bool:
    autom = c.get("autom") or ""
    text = c.get("text") or ""
    return bool(PURCHASE_AUTOM.search(autom) or PURCHASE_TEXT.search(text))


def wait_signin_if_needed(page, timeout_sec: int) -> None:
    from assist import looks_like_signin, wait_for_signin

    if looks_like_signin(page.url, page.title()):
        log("Sign-in required — complete Apple ID + 2FA in the browser.")
        notify_macos("Probe — sign in", "Finish Apple ID in Chromium window")
        if not wait_for_signin(page, timeout_sec):
            raise RuntimeError("Timed out waiting for sign-in")
        log(f"Signed in — now at {page.url}")


def click_safe_continue(page, classified: dict) -> str | None:
    """Click one safe continue control. Returns description or None."""
    for c in classified["continues"]:
        if is_purchase_control(c):
            continue
        if c.get("disabled"):
            continue
        autom = c.get("autom") or ""
        text = c.get("text") or ""
        # Prefer data-autom
        try:
            if autom:
                page.locator(f'[data-autom="{autom}"]').first.click(
                    force=True, timeout=2_000, no_wait_after=True
                )
                return f'data-autom="{autom}" text="{text}"'
            if text:
                page.get_by_text(text, exact=True).first.click(
                    force=True, timeout=2_000, no_wait_after=True
                )
                return f'text="{text}"'
        except Exception as exc:  # noqa: BLE001
            log(f"  continue click failed ({autom or text}): {exc}")
            continue
    return None


def print_snapshot(step: int, snap: dict, classified: dict) -> None:
    log("-" * 56)
    log(f"STEP {step}: {snap.get('url')}")
    log(f"  title: {snap.get('title')}")
    if snap.get("headings"):
        log(f"  headings: {snap['headings'][:5]}")
    log(f"  place-order visible? {'YES' if classified['purchase'] or snap.get('hasPlaceOrderHint') else 'no'}")
    if classified["purchase"]:
        for c in classified["purchase"]:
            log(
                f"  ★ PURCHASE CTRL: autom={c.get('autom')!r} text={c.get('text')!r} "
                f"disabled={c.get('disabled')}"
            )
    if classified["continues"]:
        for c in classified["continues"][:8]:
            log(
                f"  → continue: autom={c.get('autom')!r} text={c.get('text')!r} "
                f"disabled={c.get('disabled')}"
            )
    else:
        log("  → continue: (none matched)")
    # Highlight likely checkout-specific automs
    interesting = [
        c
        for c in classified["other"]
        if any(
            k in ((c.get("autom") or "") + " " + (c.get("text") or "")).lower()
            for k in (
                "ship",
                "bill",
                "pay",
                "fulfill",
                "address",
                "delivery",
                "pickup",
                "checkout",
                "zip",
                "giỏ",
                "giao",
                "thanh",
                "địa",
                "nhận",
            )
        )
    ]
    if snap.get("checkoutStep"):
        log(f"  checkout _s=: {snap.get('checkoutStep')}")
    for c in interesting[:16]:
        log(f"  · other: autom={c.get('autom')!r} text={c.get('text')!r}")
    radios = snap.get("radios") or []
    if radios:
        log(f"  radios ({len(radios)}):")
        for r in radios[:12]:
            mark = "●" if r.get("checked") else "○"
            log(
                f"    {mark} autom={r.get('autom')!r} name={r.get('name')!r} "
                f"label={r.get('label')!r}"
            )
    fields = snap.get("fields") or []
    if fields:
        log(f"  fields ({len(fields)}):")
        for f in fields[:20]:
            log(
                f"    - {f.get('tag')}/{f.get('type')} autom={f.get('autom')!r} "
                f"name={f.get('name')!r} id={f.get('id')!r} ph={f.get('placeholder')!r}"
            )


def page_fingerprint(snap: dict) -> str:
    heads = "|".join((snap.get("headings") or [])[:3])
    step = snap.get("checkoutStep") or ""
    purchase = "P" if snap.get("hasPlaceOrderHint") else "-"
    return f"{snap.get('url')}|{step}|{heads}|{purchase}"


def try_open_location_picker(page, snap: dict) -> bool:
    """Open Apple VN location/zip editor if fulfillment is stuck on 'Chọn Địa Điểm'."""
    for autom in LOCATION_AUTOM:
        try:
            loc = page.locator(f'[data-autom="{autom}"]')
            if loc.count() == 0:
                continue
            loc.first.click(force=True, timeout=1_500, no_wait_after=True)
            log(f"Opened location picker via data-autom={autom!r}")
            page.wait_for_timeout(500)
            return True
        except Exception:  # noqa: BLE001
            continue
    if snap.get("hasLocationHint"):
        try:
            page.get_by_text("Chọn Địa Điểm", exact=False).first.click(
                force=True, timeout=1_500, no_wait_after=True
            )
            log("Opened location picker via text 'Chọn Địa Điểm'")
            page.wait_for_timeout(500)
            return True
        except Exception:  # noqa: BLE001
            pass
    return False


def probe_loop(page, *, max_steps: int, login_timeout_sec: int) -> list[dict]:
    """Snapshot pages and click safe continues until place-order or stuck."""
    history: list[dict] = []
    opened_location = False
    for step in range(max_steps + 1):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(400)

        wait_signin_if_needed(page, login_timeout_sec)

        snap = snapshot_page(page)
        classified = classify_controls(snap)
        print_snapshot(step, snap, classified)
        history.append({"step": step, "snapshot": snap, "classified": classified})

        if classified["purchase"]:
            log("REACHED purchase controls — stopping (will NOT click them).")
            notify_macos(
                "Probe — place order visible",
                "Stopped before purchase. Inspect Chrome window.",
            )
            break

        if step >= max_steps:
            log(f"Max probe steps ({max_steps}) reached — stopping.")
            break

        via = click_safe_continue(page, classified)
        if not via and not opened_location and (
            snap.get("hasLocationHint")
            or "Fulfillment" in (snap.get("checkoutStep") or "")
        ):
            opened_location = try_open_location_picker(page, snap)
            if opened_location:
                # Re-snapshot after opening picker; user/config may need to fill it
                continue
        if not via:
            log(
                "No safe continue control — switching to manual. "
                "Fill address/location in Chrome; re-run with --watch if needed."
            )
            notify_macos("Probe — needs address", "Fill location/address in Chrome")
            break
        log(f"Clicked safe continue: {via}")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=6_000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(600)

    return history


def watch_loop(
    page, *, watch_sec: int, login_timeout_sec: int, report_path: Path
) -> list[dict]:
    """
    You drive checkout (location, address, payment).
    We only observe + log whenever the page/step changes. Never purchases.
    """
    log("=" * 56)
    log("WATCH MODE — fill location / address / delivery / payment yourself.")
    log("Probe logs each change and STOPS when Place Order / Đặt hàng appears.")
    log("It will NEVER click purchase.")
    log("=" * 56)
    notify_macos("Probe watch", "Fill address in Chrome — we are logging steps")

    history: list[dict] = []
    last_fp = ""
    opened_location = False
    deadline = time.time() + max(watch_sec, 30)
    step = 0
    next_ping = time.time() + 30

    while time.time() < deadline:
        wait_signin_if_needed(page, login_timeout_sec)
        try:
            snap = snapshot_page(page)
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(500)
            continue

        fp = page_fingerprint(snap)
        if fp != last_fp:
            last_fp = fp
            classified = classify_controls(snap)
            print_snapshot(step, snap, classified)
            history.append(
                {
                    "step": step,
                    "snapshot": snap,
                    "classified": classified,
                    "mode": "watch",
                }
            )
            write_report(report_path, history)
            step += 1

            if not opened_location and (
                snap.get("hasLocationHint")
                or "Fulfillment" in (snap.get("checkoutStep") or "")
            ):
                # One gentle open of the location editor to surface fields
                opened_location = try_open_location_picker(page, snap)

            if classified["purchase"]:
                log("REACHED purchase controls — watch stopping (will NOT click).")
                notify_macos(
                    "Probe — place order visible",
                    "You are one click from purchase. Script will not click it.",
                )
                break

        if time.time() >= next_ping:
            left = int(deadline - time.time())
            log(f"Watching… {left}s left — fill location/address in Chrome")
            next_ping = time.time() + 30
        page.wait_for_timeout(1_200)

    if not history:
        log("Watch ended with no snapshots — was checkout still loading?")
    return history


def ensure_bag_has_item(page) -> None:
    page.goto("https://www.apple.com/vn/shop/bag", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    info = page.evaluate(
        """() => {
          const text = (document.body.innerText || '');
          const empty = /giỏ hàng của bạn trống|your bag is empty|túi của bạn trống/i.test(text);
          const checkout = !!document.querySelector('[data-autom="checkout"]');
          return { empty, checkout, url: location.href };
        }"""
    )
    log(f"Bag: empty={info.get('empty')} checkout={info.get('checkout')} url={info.get('url')}")
    if info.get("empty") or not info.get("checkout"):
        raise RuntimeError(
            "Bag is empty — run with --from-assist first, or python assist.py then probe again."
        )


def enter_checkout(page) -> None:
    page.locator('[data-autom="checkout"]').first.wait_for(state="attached", timeout=8_000)
    page.evaluate(
        """() => {
          const btn = document.querySelector('[data-autom="checkout"]');
          if (btn) btn.click();
        }"""
    )
    try:
        page.wait_for_url(
            re.compile(r".*/(checkout|signIn|signin|shipping|billing|fulfillment).*", re.I),
            timeout=10_000,
        )
    except Exception:  # noqa: BLE001
        pass
    log(f"Entered checkout flow at: {page.url}")


def run_from_assist(page, cfg: dict, login_timeout_sec: int) -> None:
    from assist import (
        StageTimer,
        click_next_steps,
        goto_resilient,
        looks_like_signin,
        select_declines,
        wait_for_signin,
    )
    from sprint_common import click_labels

    product_url = validate_store_url(cfg.get("product_url", ""), "product_url")
    no_trade, no_care = click_labels(cfg)

    log(f"Assist path — opening product: {product_url}")
    timer = StageTimer()
    goto_resilient(page, product_url)
    timer.mark("0 product page navigation")
    if looks_like_signin(page.url, page.title()):
        if not wait_for_signin(page, login_timeout_sec):
            raise RuntimeError("Sign-in required before assist path")
        goto_resilient(page, product_url)

    page.locator(
        '#noTradeIn, [data-autom="choose-noTradeIn"], input[value="noTradeIn"]'
    ).first.wait_for(state="attached", timeout=10_000)
    timer.mark("0c trade-in radio ready")
    select_declines(page, no_trade, no_care, 45_000, timer)
    # click through to bag+checkout (stops at checkout-ready)
    click_next_steps(page, timer)


def write_report(path: Path, history: list[dict]) -> None:
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "steps": len(history),
        "final_url": (history[-1]["snapshot"]["url"] if history else None),
        "place_order_seen": any(
            (h.get("classified") or {}).get("purchase") for h in history
        ),
        "history": history,
    }
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Wrote probe report: {path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        require_dry_run(cfg)
        session_url = validate_store_url(
            cfg.get("session_url") or DEFAULT_SESSION_URL, "session_url"
        )
    except ConfigError as exc:
        die(str(exc))

    log("CHECKOUT PROBE — maps steps until place-order; NEVER purchases")
    log(f"Profile: {PROFILE_DIR}")
    log("Warm Chrome via CDP — disconnect only, never quit (keeps SSO).")

    sync_playwright = _require_playwright()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    from assist import goto_resilient, looks_like_signin, wait_for_signin

    with sync_playwright() as p:
        browser = None
        meta = None
        exit_code = 0
        history: list[dict] = []

        try:
            browser, context, page, meta = launch_assist_browser(p, profile_dir=PROFILE_DIR)
            log(f"Opening session: {session_url}")
            goto_resilient(page, session_url)
            if looks_like_signin(page.url, page.title()) or args.setup_login:
                wait_signin_if_needed(page, args.login_timeout_sec)

            if args.setup_login:
                log("SETUP DONE. Leave Chrome open. Next: python probe_checkout.py")
            else:
                if args.from_assist:
                    run_from_assist(page, cfg, args.login_timeout_sec)
                else:
                    ensure_bag_has_item(page)
                    enter_checkout(page)

                wait_signin_if_needed(page, args.login_timeout_sec)
                if args.watch:
                    history = watch_loop(
                        page,
                        watch_sec=args.watch_sec,
                        login_timeout_sec=args.login_timeout_sec,
                        report_path=args.report,
                    )
                else:
                    history = probe_loop(
                        page,
                        max_steps=args.max_steps,
                        login_timeout_sec=args.login_timeout_sec,
                    )
                    write_report(args.report, history)

                final = history[-1] if history else None
                if final and (final.get("classified") or {}).get("purchase"):
                    log("SUCCESS: found place-order control(s). Assist can aim to stop here.")
                else:
                    log(
                        "Probe finished without a clear place-order control. "
                        "Use the open browser + probe-report.json to inspect."
                    )

            page.wait_for_timeout(max(args.keep_open_sec, 3) * 1000)
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            log(f"Probe failed: {exc}")
            notify_macos("Probe failed", str(exc)[:120])
            if history:
                write_report(args.report, history)
            try:
                page.wait_for_timeout(max(args.keep_open_sec, 3) * 1000)
            except Exception:  # noqa: BLE001
                pass
        finally:
            disconnect_assist_browser(browser, meta)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
