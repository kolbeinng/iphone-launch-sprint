#!/usr/bin/env python3
"""Timed Apple VN launch helper — opens a preconfigured deep link. Never purchases."""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

from sprint_common import (
    DEFAULT_CONFIG,
    DEFAULT_SESSION_URL,
    DEFAULT_WARM_URL,
    ConfigError,
    beep,
    config_timezone,
    die,
    format_ts,
    load_config,
    log,
    notify_macos,
    now_in_tz,
    open_url,
    parse_duration,
    parse_launch_at,
    print_checklist_reminder,
    print_manual_clicks,
    print_session_debug,
    require_dry_run,
    validate_store_url,
    wait_until,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Warm the Apple VN session, then open your exact product deep link at T-0. "
            "Dry-run only — you complete checkout manually; this never pays."
        )
    )
    p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--now",
        action="store_true",
        help="Fire immediately (still runs warm step first unless --skip-warm)",
    )
    group.add_argument(
        "--in",
        dest="in_duration",
        metavar="DURATION",
        help="Fake launch in DURATION from now (e.g. 2m, 90s) — for dry-run practice",
    )
    p.add_argument(
        "--skip-warm",
        action="store_true",
        help="Do not open warm/session URL before T-0",
    )
    p.add_argument(
        "--warm-only",
        action="store_true",
        help="Only open warm/session URL, then exit",
    )
    p.add_argument(
        "--check-session",
        action="store_true",
        help="Open Apple VN account page to verify sign-in in the configured browser",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        require_dry_run(cfg)
        product_url = validate_store_url(cfg.get("product_url", ""), "product_url")
        warm_url = validate_store_url(
            cfg.get("warm_url") or DEFAULT_WARM_URL,
            "warm_url",
        )
        session_url = validate_store_url(
            cfg.get("session_url") or DEFAULT_SESSION_URL,
            "session_url",
        )
        browser = cfg.get("browser") or "Safari"
        label = cfg.get("label") or "Apple VN launch"
        warm_minutes = int(cfg.get("warm_minutes_before", 10))
        tz = config_timezone(cfg)

        if args.now or args.check_session or args.warm_only:
            launch_at = now_in_tz(tz)
        elif args.in_duration:
            launch_at = now_in_tz(tz) + parse_duration(args.in_duration)
        else:
            launch_at = parse_launch_at(cfg)
    except ConfigError as exc:
        die(str(exc))

    print_checklist_reminder()
    print_session_debug(browser)

    if args.check_session:
        log("Opening account page — this is the real sign-in check")
        open_url(session_url, browser)
        notify_macos(
            "Launch Sprint — session check",
            f"In {browser}: if you see Đăng nhập, sign in now. Then stay in this browser.",
        )
        log("LOOK at the page that opened:")
        log("  • Signed in  → you see account / order history style content")
        log("  • Signed out → Apple asks you to Đăng nhập / Sign In")
        log("Sign-in must be in THIS browser app before launch.py can help.")
        log("If you normally use Chrome, set browser: \"Google Chrome\" in config.yaml")
        return 0

    log("DRY-RUN mode — will never place an order")
    log(f"Target: {label}")
    log(f"Product deep link: {product_url}")
    log(f"Launch at: {format_ts(launch_at)}")
    log(f"Browser: {browser}")
    print_manual_clicks(cfg)

    warm_at = launch_at - timedelta(minutes=max(warm_minutes, 0))
    now = now_in_tz(tz)

    if args.warm_only:
        open_url(warm_url, browser)
        notify_macos("Launch Sprint", "Account/warm URL opened — confirm you are signed in.")
        log("Warm-only done. Confirm Apple ID session, then re-run without --warm-only.")
        return 0

    if not args.skip_warm:
        if now < warm_at:
            wait_until(warm_at, tz, "warm-up")
        log(f"T-{warm_minutes}m warm-up: opening account page to verify session")
        open_url(warm_url, browser)
        notify_macos(
            "Launch Sprint — warm-up",
            f"Confirm signed-in in {browser}. If Đăng nhập appears, sign in now.",
        )
        log("Confirm you are signed in. Payment/shipping should already be saved.")
    else:
        log("Skipping warm-up (--skip-warm)")

    if now_in_tz(tz) < launch_at:
        wait_until(launch_at, tz, "T-0")

    log("T-0 — opening preconfigured product URL (model/color/storage skipped)")
    open_url(product_url, browser)
    beep()
    print_manual_clicks(cfg)
    notify_macos(
        "GO — no trade-in, no AppleCare, then YOU buy",
        f"{label}. Decline trade-in + AppleCare. Do not place order in dry-run.",
    )
    log("Opened product page. DRY-RUN STOP — complete remaining clicks yourself.")
    log("Would purchase: NO (dry_run=true). Do not click Place Order during practice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
