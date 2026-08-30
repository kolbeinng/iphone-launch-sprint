# Don't forget

Scripts never enter your Apple password and never click **Đặt hàng**.

## What this orders

**TEST (`mode: test` now):** iPhone 17 Pro Max · 6.9" · 256GB · Cam Vũ Trụ — dry-runs.

**LAUNCH (`mode: launch`):** iPhone 18 Pro Max. Fold is not listed.

Switch in `config.yaml` only — one line, no commenting:

1. Set `mode: launch`
2. Confirm `launch_at`

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

## New Mac — do this in this order (no shortcuts)

This is the only path. Do not skip a letter. Do not install Homebrew. Do not use Safari. Do not use Cursor or any AI for this.

**Never type `python`.** A Mac does not have that command. You will get `zsh: command not found: python`.  
**Never run `launch.py`.** That is the old script. The only command is `assist.py`.

The three failures that stop people: **Command Line Tools never installed**, **typed `python` instead of `.venv/bin/python`**, and **Playwright installed in the wrong Python**.

### A. Google Chrome

Install **Google Chrome** from Google. Not Safari. Not Chromium. Not “Chrome for Testing.”

### B. Apple Command Line Tools (do this before anything else in Terminal)

Open **Terminal**. Paste this and press Return:

```bash
xcode-select --install
```

A Mac window pops up. Click **Install**. Wait until it is fully done (often 5–15 minutes). If it says already installed, continue.

Then run both of these. You need **git** and **Python 3.10 or newer**:

```bash
git --version
python3 --version
```

If either command fails, Command Line Tools is not finished. Redo step B. Do not continue.

If `python3 --version` is older than 3.10, install Python from https://www.python.org/downloads/ (the macOS installer). Then **quit Terminal and open it again**, and check `python3 --version` once more.

### C. GitHub (the repo is private)

In a browser, sign in to GitHub as **kolbeinng**. Open:

https://github.com/kolbeinng/iphone-launch-sprint

If you see 404, you are on the wrong GitHub account. Stop and fix that.

### D. Download the project

In Terminal:

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
git status
```

You must be on `cursor/dynamic-sku-select`. If `git status` says `main` (old copy):

```bash
git checkout cursor/dynamic-sku-select
```

### E. Python environment + Playwright (this is the step people skip)

Stay in `~/Projects/iphone-launch-sprint`. Run **exactly** these lines, in this order:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chrome
```

Do not type `pip` or `playwright` or `python` alone. Always `.venv/bin/python`.

Prove it worked:

```bash
.venv/bin/python -c "from playwright.sync_api import sync_playwright; print('playwright ok')"
```

You must see `playwright ok`. If that line fails, redo step E. Do not continue.

**Every new Terminal window**, the first two lines are always:

```bash
cd ~/Projects/iphone-launch-sprint
```

Then the command, always with `.venv/bin/python` — you do not need `source` or activate.

### F. `config.yaml` on this Mac

`config.yaml` is never on GitHub (it has the CVV).

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and set:

- `mode: test` for practice
- `dry_run: true`
- **`checkout.cvv`** — the 3 digits for the card on **this** machine

Name, street, email, phone, and product can match the other computer. **CVV is the one field you type on this Mac.** Do not email it. Do not put it on GitHub.

### G. Apple card billing (normal Chrome or Safari, days before launch)

Apple ID → Payment → card **billing** already Vietnam (HCM, quận/phường, no junk postal). Do this days before. The in-checkout billing popup costs ~40 seconds.

### H. Sign in inside the script’s Chrome

```bash
cd ~/Projects/iphone-launch-sprint
.venv/bin/python assist.py --setup-login
```

A **new** Chrome window opens. Sign in + 2FA **in that window**. Leave it open. **Never quit Chrome** (no ⌘Q) after this.

If Chrome is already open from everyday use and the script says it cannot start: **⌘Q Chrome once**, then run `--setup-login` again. After that, never quit it.

### I. Warm checkout (same open Chrome)

```bash
.venv/bin/python assist.py --warm-only
```

Finish checkout 2FA if asked. It adds a practice phone, reaches checkout, **empties the bag**. Chrome stays open.

### J. Practice until it is boring

```bash
.venv/bin/python assist.py --now
```

It must stop at **Đặt hàng**. You do **not** click Đặt hàng. Do this 2–3 times until it is boring. The second run should click the **saved** shipping radio.

### K. Later updates from this repo

After this Mac pushes a change:

```bash
cd ~/Projects/iphone-launch-sprint
git pull
```

Then run with `.venv/bin/python` again. Do not type `python`.

### L. Launch night (same Mac, same open Chrome)

1. In `config.yaml`: `mode: launch` and confirm `launch_at`.
2. **T−10:** `.venv/bin/python assist.py --warm-only` if Chrome is not already warm. Phone in hand. Bag must end empty.
3. **T−0:** `.venv/bin/python assist.py --at-launch`
4. **You** click Đặt hàng.

---

### If it breaks

| What you see | What you do |
|---|---|
| `command not found: python` | You typed `python`. On a Mac that does not exist. Use `.venv/bin/python` after step E. |
| `git: command not found` or `xcode-select` | Step B. Do not continue until `git --version` works. |
| `Playwright not installed` | Redo step E. You are not using `.venv/bin/python`. |
| Clone 404 | Wrong GitHub account. Step C. |
| Apple ID every run | You quit Chrome. Sign in again (H). Leave it open. |
| Script sits on sign-in | Finish 2FA in **that** Chrome. It is waiting. |

Windows (PowerShell), only if you are not on a Mac — after clone:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chrome
copy config.example.yaml config.yaml
```

---

## Per-computer setup (once, days before — not at 18:50)

Do this on **the computer you will use at T-0**. Practice on another machine does not count.

1. Clone + `config.yaml` as above.
2. In a normal browser: Apple ID → Payment → card **billing** already correct (Vietnam quận/phường, no junk postal). Not at T-0 (popup ~40s).
3. `.venv/bin/python assist.py --setup-login`  
   Sign in + 2FA in the **script’s Chrome** (not everyday Chrome). Leave that window open. Never ⌘Q / quit Chrome after this.
4. `.venv/bin/python assist.py --warm-only`  
   Second login: **checkout** SSO. 2FA again if asked. Adds a practice iPhone, reaches checkout, **empties the bag**. Leave Chrome open.
5. `.venv/bin/python assist.py --now`  
   Full dry-run → stop at Đặt hàng (not clicked). Repeat until it is boring (2–3 clean runs). Second run should select the saved shipping radio, not type a new address.

The script always applies `shipping_address` from config:

- If Apple already shows a saved radio whose **visible name + street** match → click it (~20ms).
- If not → fill **Sử dụng địa chỉ mới** from the same config (slower, still the right address).

Same Apple ID on a new computer: after login, the saved address usually appears by itself.

---

## Launch day (same computer, same open Chrome)

- **T−10:** `.venv/bin/python assist.py --warm-only` if Chrome isn’t already warm. 2FA phone in hand. Bag must end empty.
- **T−0:** `.venv/bin/python assist.py --at-launch` (sleeps until `launch_at`). Do not add extra seconds after 19:00.
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

- [ ] Phone picker: `mode: launch` in `config.yaml` (both `test:` and `launch:` blocks stay as-is)
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
