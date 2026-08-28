# Don't forget

Scripts never enter your Apple password and never click **Đặt hàng**.

## What this orders

**TEST (on now in config.yaml):** iPhone 17 Pro Max · 6.9" · 256GB · Cam Vũ Trụ — dry-runs.

**LAUNCH (commented out):** iPhone 18 Pro Max. Fold is not listed.

Switch in `config.yaml` only — no code changes:

1. Comment out the whole **TEST — iPhone 17** block
2. Uncomment the whole **LAUNCH — iPhone 18** block
3. Confirm `launch_at`

`--warm-only` always uses a live iPhone 17 link to stay signed in, then **empties the bag**. That is not the order.

When LAUNCH is on, the script **refuses** to bag 17 / Fold / Air.

---

## This Mac vs GitHub (read this once)

Think of it like a router and a TFTP server:

| Where | What it is |
|-------|------------|
| **This computer** | Running config. Edits live here immediately. |
| **GitHub** | Backup copy for other PCs. **Does not update by itself.** |

**Saving a file in Cursor is not enough.** GitHub only changes after two steps:

1. **Commit** = snapshot this change (write mem of the diff)
2. **Push** = send that snapshot to GitHub (`copy running-config tftp`)

On another computer: **pull** (or clone once) = download from GitHub.

Ask Cursor: “commit and push to GitHub.” Until that happens, other machines still have the old copy.

Repo (private, your account): https://github.com/kolbeinng/iphone-launch-sprint

`config.yaml` is **never** on GitHub (CVV, address, email). Copy it yourself or recreate from `config.example.yaml`.

---

## New computer — get the app from GitHub

Sign in to GitHub as **kolbeinng** first (private repo). Then:

```bash
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Windows (PowerShell), after clone:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chrome
copy config.example.yaml config.yaml
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chrome
cp config.example.yaml config.yaml
```

Edit `config.yaml` on **this** machine:

- Same **name, street, email, phone, product** as the other PC
- **CVV is the one field that changes** (that card / that machine)
- Do not email CVV; do not put it on GitHub

Later updates from this Mac (after a push): on the other PC, `git pull`.

---

## Per-computer setup (once, days before — not at 18:50)

Do this on **the computer you will use at T-0**. Practice on another machine does not count.

1. Clone + `config.yaml` as above.
2. In a normal browser: Apple ID → Payment → card **billing** already correct (Vietnam quận/phường, no junk postal). Not at T-0 (popup ~40s).
3. `python assist.py --setup-login`  
   Sign in + 2FA in the **script’s Chrome** (not everyday Chrome). Leave that window open. Never ⌘Q / quit Chrome after this.
4. `python assist.py --warm-only`  
   Second login: **checkout** SSO. 2FA again if asked. Adds a practice iPhone, reaches checkout, **empties the bag**. Leave Chrome open.
5. `python assist.py --now`  
   Full dry-run → stop at Đặt hàng (not clicked). Repeat until it is boring (2–3 clean runs). Second run should select the saved shipping radio, not type a new address.

The script always applies `shipping_address` from config:

- If Apple already shows a saved radio whose **visible name + street** match → click it (~20ms).
- If not → fill **Sử dụng địa chỉ mới** from the same config (slower, still the right address).

Same Apple ID on a new computer: after login, the saved address usually appears by itself.

---

## Launch day (same computer, same open Chrome)

- **T−10:** `python assist.py --warm-only` if Chrome isn’t already warm. 2FA phone in hand. Bag must end empty.
- **T−0:** `python assist.py --at-launch` (sleeps until `launch_at`). Do not add extra seconds after 19:00.
- **You** click Đặt hàng.

`--now` = same product path as launch, no clock. `--at-launch` = waits until `launch_at`. Don’t quit Chrome between warm and sprint.

Keep `warm_product_url` on a **live iPhone 17** link. Keep `dry_run: true`. Confirm `launch_at` when Apple announces the VN time.

---

## Two different “where do we deliver?” screens

Apple will not let you skip these. They are not the same thing.

| Page | What it is | Do we need it? |
|------|------------|----------------|
| **Fulfillment** (“Giao hàng đến”) | City so delivery slots exist | **The page, not the editor.** If it already shows HCM, we skip re-selecting and click **Tiếp tục đến Địa Chỉ Giao Hàng**. The ~10s after Continue is Apple loading Shipping. |
| **Shipping** — “Chúng tôi giao hàng cho bạn đến địa chỉ nào?” | Pick the **street** (saved radio vs new) | **Yes.** This is where the right house is chosen. Saved match ~20ms. |

We only open the city/quận editor if the label is **not** already HCM (wrong city).

---

## Night-before extras

- [ ] Phone picker: **LAUNCH** uncommented, **TEST** commented (`target.year: 18`)
- [ ] `checkout.cvv` is the card on **this** machine
- [ ] `dry_run: true` until you are ready to click Đặt hàng yourself
- [ ] Alarm `Asia/Ho_Chi_Minh`; notifications on; Focus / DND off

If you are not signed in, speed does not matter — you already lost.

---

## Timer (read WAIT vs CLICK)

End of each `--now` log:

- **US (CLICK/FILL)** — our clicks. Should stay small (under ~1s).
- **APPLE (WAIT/NAV/POLL)** — page load / checkout hop. ~10s each is Apple. Do not “fix” this by clicking more.

If a long pause has a `WAIT  Apple hop … heartbeat` line, we are idle. The page is loading.
