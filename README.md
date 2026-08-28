# iPhone Launch Sprint

Local **dry-run** kit for Apple Store Vietnam.

**Goal:** be ready for **iPhone 18** launch on apple.com/vn.  
**Practice now:** iPhone 17 config (no purchase).

**It never places an order.** `dry_run: true` is required.

## What the deep link actually skips

| Skipped by URL | Still required on the page |
|----------------|----------------------------|
| Model / size | **Trade In** → `Không đổi cũ lấy mới` |
| Storage | **AppleCare** → `Không có bảo hành AppleCare+` |
| Color | **You** click bag / buy |

There is no reliable public URL param to pre-decline trade-in or AppleCare on apple.com/vn.

## Sign-in (the usual failure)

Login is **per browser**. `launch.py` uses whatever `browser` is in `config.yaml` (default Safari). If you are signed into Chrome only, Safari looks “logged out”.

```bash
python launch.py --check-session
```

- See **Đăng nhập** → you are signed out in that browser. Sign in, then re-check.
- Prefer Chrome? Set `browser: "Google Chrome"` in `config.yaml`.

Stronger path (dedicated **Google Chrome** profile — not Chrome for Testing, not Safari):

```bash
pip install -r requirements.txt
playwright install chrome   # uses installed Google Chrome via channel=chrome
python assist.py --setup-login   # waits up to 10 min while you finish Apple ID + 2FA
python assist.py                 # then declines trade-in + AppleCare and stops
python assist.py --stay-open     # keep Chrome warm (close the window yourself)
```

If assist lands on sign-in, it is **waiting**, not frozen — finish 2FA in the Chrome window.

Checkout SSO (`secure*.store.apple.com`) is separate from storefront login. Best warm path: sign in once at account **and** once at checkout in the same open Chrome, then use `--stay-open` so the process doesn’t tear down that session.

## Setup (Windows — primary)

Prereqs: **Windows 10/11**, **Google Chrome**, **Python 3.10+**.

```powershell
cd path\to\iphone-launch-sprint-dynamic
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chrome
copy config.example.yaml config.yaml
# edit config.yaml — address, contact, product_prefs
```

If Chrome is not in the default path:

```powershell
$env:CHROME_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

### Fresh-machine workflow

```powershell
python assist.py --setup-login   # Apple ID + 2FA in assist Chrome (leave it open)
python assist.py --warm-only     # prime checkout SSO, bag emptied; leave Chrome open
python assist.py                 # timed dry-run → stops at saved card (never Đặt hàng)
```

Do **not** fully quit Chrome between warm and timed run (that drops SSO / forces 2FA again). Scripts only disconnect from CDP.

Copy name / street / email / phone / product between computers; **edit CVV on that machine**. Full don’t-forget (setup 3–5, two delivery screens, timer): **checklist.md**.

macOS/Linux also work (Chrome auto-detected). Practice product: **iPhone 17 Pro Max · Cam Vũ Trụ · 256GB**.

## Commands

### 1) Prove sign-in

```bash
python launch.py --check-session
```

### 2) Timed deep-link practice

```bash
python launch.py --in 2m
```

### 3) Assist dry-run (through checkout form, no purchase)

```bash
python assist.py --setup-login   # first time: sign in inside Chromium
python assist.py                 # trade-in → AppleCare → bag → Thanh Toán, then STOP
```

### 4) Checkout probe (map path to place-order)

Assists stops at checkout/sign-in. Use the probe to walk **shipping / payment** and find the final **Đặt hàng / Place Order** control — it never clicks purchase.

```bash
# Bag already has an item — YOU fill address; probe logs each checkout step:
python probe_checkout.py --watch --stay-open

# Or auto-click safe continues (stops if location/address is required):
python probe_checkout.py --stay-open

# Full path including product declines (slower):
python probe_checkout.py --from-assist --watch --stay-open
```

Writes `probe-report.json` with URLs, `_s=` checkout steps, radios, fields, and `data-autom` controls. Never clicks Đặt hàng / Place Order.

### 5) Watcher

```bash
python watch.py --once
python watch.py --in 2m --open-on-change
```

### iPhone 18 launch day

1. Put the fully configured iPhone 18 `product_url` in `config.yaml`
2. Set `launch_at` (`Asia/Ho_Chi_Minh`)
3. Confirm signed-in assist profile + saved ship/pay (`checklist.md`)
4. Run `python assist.py` — **you** click the final place-order

## Files

- `launch.py` — session check, warm-up, timed deep link
- `assist.py` — fast buy-flow assist; never places the order
- `watch.py` — light page watcher
- `checklist.md` — night-before prep
- `config.example.yaml` — iPhone 17 practice config (swap for iPhone 18)

## Safety

- Refuses to run unless `dry_run: true`
- Only Apple VN / Apple account URLs
- Assist stops before placing the order
- Watcher minimum poll interval: 15s
