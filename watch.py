#!/usr/bin/env python3
"""Light Apple VN product-page watcher. Alerts only — never purchases."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from datetime import timedelta
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sprint_common import (
    USER_AGENT,
    ConfigError,
    DEFAULT_CONFIG,
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
    require_dry_run,
    validate_store_url,
)

MIN_POLL_SECONDS = 15

# Loose signals that something buy/fulfillment related changed on the page.
SIGNAL_PATTERNS = [
    re.compile(r"add-to-cart", re.I),
    re.compile(r"addToCart", re.I),
    re.compile(r"data-autom=\"add-to-cart\"", re.I),
    re.compile(r"Mua ngay", re.I),
    re.compile(r"Thêm vào túi", re.I),
    re.compile(r"Check Availability", re.I),
    re.compile(r"delivery", re.I),
    re.compile(r"giao hàng", re.I),
    re.compile(r"nhận hàng", re.I),
    re.compile(r"unavailable", re.I),
    re.compile(r"hết hàng", re.I),
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Poll the configured Apple VN product page every 15–30s around launch. "
            "Notify on content/signal changes. Never buys."
        )
    )
    p.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    p.add_argument(
        "--in",
        dest="in_duration",
        metavar="DURATION",
        help="Treat launch_at as now + DURATION (e.g. 2m) for a practice window",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Fetch once, print fingerprint/signals, exit",
    )
    p.add_argument(
        "--open-on-change",
        action="store_true",
        help="Open product_url in the browser when a change is detected",
    )
    return p


def fetch_page(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310 — user-configured Apple URL only
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def extract_signals(html: str) -> list[str]:
    found: list[str] = []
    for pattern in SIGNAL_PATTERNS:
        if pattern.search(html):
            found.append(pattern.pattern)
    return found


def fingerprint(html: str) -> str:
    """Hash a reduced slice of the page so trivial cookie noise is less noisy."""
    text = unescape(html)
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"\s+", " ", text)
    # Keep middle chunk bias toward product body if huge
    snippet = text[:200_000]
    return hashlib.sha256(snippet.encode("utf-8", errors="replace")).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        require_dry_run(cfg)
        product_url = validate_store_url(cfg.get("product_url", ""), "product_url")
        browser = cfg.get("browser") or "Safari"
        label = cfg.get("label") or "Apple VN product"
        tz = config_timezone(cfg)
        watcher = cfg.get("watcher") or {}
        if not isinstance(watcher, dict):
            raise ConfigError("watcher must be a mapping")
        poll_seconds = max(MIN_POLL_SECONDS, int(watcher.get("poll_seconds", 20)))
        before = int(watcher.get("window_minutes_before", 15))
        after = int(watcher.get("window_minutes_after", 30))

        if args.in_duration:
            launch_at = now_in_tz(tz) + parse_duration(args.in_duration)
        else:
            try:
                launch_at = parse_launch_at(cfg)
            except ConfigError:
                launch_at = now_in_tz(tz)
    except ConfigError as exc:
        die(str(exc))

    print_checklist_reminder()
    log("DRY-RUN watcher — alert only, never purchases")
    log(f"Watching: {label}")
    log(f"URL: {product_url}")
    log(f"Poll every {poll_seconds}s (minimum {MIN_POLL_SECONDS}s)")

    if args.once:
        try:
            html = fetch_page(product_url)
        except (HTTPError, URLError, TimeoutError) as exc:
            die(f"Fetch failed: {exc}")
        fp = fingerprint(html)
        signals = extract_signals(html)
        log(f"Fingerprint: {fp}")
        log(f"Signals: {', '.join(signals) if signals else '(none matched)'}")
        return 0

    window_start = launch_at - timedelta(minutes=max(before, 0))
    window_end = launch_at + timedelta(minutes=max(after, 0))
    log(f"Window: {format_ts(window_start)} → {format_ts(window_end)}")
    log(f"Launch anchor: {format_ts(launch_at)}")

    now = now_in_tz(tz)
    if now < window_start:
        sleep_for = (window_start - now).total_seconds()
        log(f"Before watch window — sleeping {int(sleep_for)}s")
        time.sleep(sleep_for)

    last_fp: str | None = None
    last_signals: list[str] | None = None
    change_count = 0

    while now_in_tz(tz) <= window_end:
        try:
            html = fetch_page(product_url)
            fp = fingerprint(html)
            signals = extract_signals(html)
        except (HTTPError, URLError, TimeoutError) as exc:
            log(f"Fetch error (will retry): {exc}")
            time.sleep(poll_seconds)
            continue

        if last_fp is None:
            log(f"Baseline fingerprint={fp} signals={signals or ['(none)']}")
            last_fp = fp
            last_signals = signals
        elif fp != last_fp or signals != last_signals:
            change_count += 1
            log(f"CHANGE detected #{change_count}")
            log(f"  fingerprint {last_fp} → {fp}")
            log(f"  signals {last_signals} → {signals}")
            beep()
            notify_macos(
                "Apple VN page changed",
                f"{label}. Check Buy / delivery. Complete checkout yourself.",
            )
            if args.open_on_change:
                open_url(product_url, browser)
            last_fp = fp
            last_signals = signals
        else:
            log(f"No change (fingerprint={fp})")

        # Sleep, but don't overshoot window by much
        remaining = (window_end - now_in_tz(tz)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(poll_seconds, remaining))

    log(f"Watch window ended. Changes seen: {change_count}. No purchase attempted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
