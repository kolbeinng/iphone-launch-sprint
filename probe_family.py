"""
Read-only probe of an Apple VN buy-iphone family page.

Answers two questions without running a sprint:
  1. Is this URL live and configure-ready, or a dead slug?
  2. Would the product_prefs in config.yaml actually match what Apple is showing?

On launch night, run this the moment the page goes live. If a colour or size
pref reports NO MATCH, fix config.yaml before you sprint instead of finding out
at T-0 when the script stops and waits for you to click.

    python3 probe_family.py                       # URLs from config.yaml
    python3 probe_family.py <url> [<url> ...]     # explicit URLs

Never adds to bag, never checks out, and disconnects without quitting Chrome.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from assist import (
    _configure_unlocked,
    _dimension_pretty,
    _format_dimension_options,
    _is_apple_404,
    _list_dimension_options,
    _pref_matches,
    goto_resilient,
)
from sprint_common import (
    ASSIST_PROFILE_DIR,
    ConfigError,
    die,
    disconnect_assist_browser,
    launch_assist_browser,
    load_config,
    log,
    validate_store_url,
)

# (dimension radio name, config key, alternate config key, fallback prefs)
DIMENSIONS = (
    ("dimensionScreensize", "screensizes", "sizes", ["Pro Max", "6,9", "6.9", "6_9inch", "6_9"]),
    ("dimensionColor", "colors", None, []),
    ("dimensionCapacity", "storages", "capacities", []),
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("urls", nargs="*", help="Family URLs to probe (default: from config)")
    p.add_argument("-c", "--config", type=Path, default=None)
    return p


def config_urls(cfg: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    candidates = [cfg.get("family_url") or ""]
    raw = cfg.get("family_urls")
    if isinstance(raw, list):
        candidates.extend(str(u) for u in raw)
    for raw_url in candidates:
        url = str(raw_url).strip()
        if not url:
            continue
        url = validate_store_url(url, "family_url")
        key = url.rstrip("/")
        if key not in seen:
            out.append(url)
            seen.add(key)
    return out


def report_prefs(opts: list[dict], prefs: list[str], pretty: str) -> bool:
    """Log whether any pref matches an ENABLED option. Returns True on match."""
    if not prefs:
        log(f"    prefs: (none configured) — script would use its built-in default")
        return True
    enabled = [o for o in opts if not o.get("disabled")]
    pool = enabled or opts
    note = "" if enabled else "  (all disabled right now — matching against labels anyway)"
    for pref in prefs:
        hit = next((o for o in pool if _pref_matches(o, pref)), None)
        if hit:
            log(
                f"    prefs: MATCH on {pref!r} → "
                f"{hit.get('autom') or hit.get('value')}{note}"
            )
            return True
    available = ", ".join(
        f"{o.get('autom') or o.get('value')}={(o.get('label') or '')[:28]!r}" for o in pool
    )
    log(f"    prefs: NO MATCH for {pretty} — tried {prefs!r}")
    log(f"           available: [{available}]")
    return False


def probe(page, url: str, prefs_cfg: dict) -> bool:
    log("=" * 72)
    log(f"PROBE {url}")
    try:
        goto_resilient(page, url)
    except Exception as exc:  # noqa: BLE001
        log(f"  navigation FAILED: {exc}")
        return False

    h1 = page.evaluate(
        "() => { const h = document.querySelector('h1');"
        " return h ? h.innerText.replace(/\\s+/g, ' ').trim() : ''; }"
    )
    landed = page.url
    unlocked = _configure_unlocked(page)
    log(f"  landed:  {landed}")
    log(f"  h1:      {h1!r}")
    log(f"  soft404: {_is_apple_404(page)}    configure_unlocked: {unlocked}")
    if landed.rstrip("/") != url.rstrip("/"):
        log("  NOTE: Apple redirected — this slug is probably retired.")
    if not unlocked:
        log("  VERDICT: not a live configure page. The sprint would skip this URL.")
        return False

    all_ok = True
    for dim, key, alt, fallback in DIMENSIONS:
        opts = _list_dimension_options(page, dim)
        pretty = _dimension_pretty(dim)
        if not opts:
            log(f"  {pretty}: (no radios on this page)")
            continue
        log(f"  {pretty}: [{_format_dimension_options(opts)}]")
        prefs = [str(x) for x in (prefs_cfg.get(key) or (prefs_cfg.get(alt) if alt else None) or fallback)]
        if not report_prefs(opts, prefs, pretty):
            all_ok = False

    log(f"  VERDICT: {'config would sprint clean' if all_ok else 'config would STOP and wait for you'}")
    return all_ok


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        die(str(exc))
    prefs_cfg = cfg.get("product_prefs") if isinstance(cfg.get("product_prefs"), dict) else {}

    urls = args.urls or config_urls(cfg)
    if not urls:
        die("No URLs given and no family_url / family_urls in config.")
    log(f"Probing {len(urls)} URL(s) — read-only, never adds to bag.")

    from playwright.sync_api import sync_playwright  # noqa: WPS433

    clean = True
    with sync_playwright() as p:
        browser = None
        meta = None
        try:
            browser, context, page, meta = launch_assist_browser(
                p, profile_dir=ASSIST_PROFILE_DIR
            )
            for url in urls:
                if not probe(page, url, prefs_cfg):
                    clean = False
        finally:
            disconnect_assist_browser(browser, meta)

    log("=" * 72)
    log("ALL CLEAR — prefs match every live page probed." if clean
        else "ATTENTION — at least one page is dead or would stop the sprint (see above).")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
