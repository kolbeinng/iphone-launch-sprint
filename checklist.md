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

**Never type `python`.** A Mac does not have that command. Type **`python3`**.  
**Never run `launch.py`.** The only script is `assist.py`.  
**Never run `python3 assist.py` until the line in Terminal starts with `(.venv)`.** That is why you got `No module named 'yaml'`.

The two failures that stop people: **Command Line Tools never installed**, and **`python3` without `source .venv/bin/activate`**.

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

Stay in the project folder. Run **exactly** these lines, in this order:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m playwright install chrome
```

The Terminal line **must** start with `(.venv)` after `source`. If it does not, stop — step E failed.

Prove it worked:

```bash
python3 -c "from playwright.sync_api import sync_playwright; print('playwright ok')"
```

You must see `playwright ok`. If that line fails, redo step E. Do not continue.

**Every new Terminal window**, before any `assist.py` command:

```bash
cd ~/Projects/iphone-launch-sprint
source .venv/bin/activate
```

Wait until you see `(.venv)`, then:

```bash
python3 assist.py --now
```

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

### H. `--warm-only` (first Chrome and every later warm)

This is the only warm command. Do not run `--setup-login`.

```bash
cd ~/Projects/iphone-launch-sprint
source .venv/bin/activate
python3 assist.py --warm-only
```

A Chrome window opens (new the first time). Sign in + 2FA **in that window** if Apple asks. It then adds a practice phone, reaches checkout, **empties the bag**. Leave Chrome open. **Never quit Chrome** (no ⌘Q).

If everyday Chrome is already open and the script cannot start: **⌘Q Chrome once**, then run `--warm-only` again. After that, never quit it.

Later, same open Chrome: run `--warm-only` again. You should not get a password box.

### I. Practice until it is boring

```bash
source .venv/bin/activate
python3 assist.py --now
```

It must stop at **Đặt hàng**. You do **not** click Đặt hàng. Do this 2–3 times until it is boring. The second run should click the **saved** shipping radio.

### K. Later updates from this repo

After this Mac pushes a change:

```bash
cd ~/Projects/iphone-launch-sprint
git pull
```

Then `source .venv/bin/activate` and `python3` again.

### L. Launch night (same Mac, same open Chrome)

1. In `config.yaml`: `mode: launch` and confirm `launch_at`.
2. **T−10:** `source .venv/bin/activate` then `python3 assist.py --warm-only` if Chrome is not already warm. Phone in hand. Bag must end empty.
3. **T−0:** `python3 assist.py --at-launch`
4. **You** click Đặt hàng.

---

### If it breaks

| What you see | What you do |
|---|---|
| `command not found: python` | You typed `python`. Type `python3` after `source .venv/bin/activate`. |
| `no such file or directory: .venv/bin/python` | You skipped step E, or you are in the wrong folder. Run step E in this folder. |
| `No module named 'yaml'` or `Playwright not installed` | You used `python3` without `(.venv)` in the prompt. Run `source .venv/bin/activate` first. |
| `git: command not found` or `xcode-select` | Step B. Do not continue until `git --version` works. |
| Clone 404 | Wrong GitHub account. Step C. |
| Apple ID every run | You quit Chrome. Sign in again (H). Leave it open. |
| Script sits on sign-in | Finish 2FA in **that** Chrome. It is waiting. |

---

## New Windows 11 — do this in this order (no shortcuts)

RDP is fine. **Disconnect** RDP when you leave (the X). Do **not** Sign out / Log off — that kills Chrome and the Apple session.

Do not install extra tools. Do not use Edge as the script browser. Do not use Cursor or any AI for this.

**On Windows type `python` after `(.venv)`.** Do not type `python3` (that is the Mac command).  
**Never run `launch.py`.** The only script is `assist.py`.  
**Never run `python assist.py` until the PowerShell line starts with `(.venv)`.**

### A. Google Chrome

On the Windows PC, install **Google Chrome** from Google. Not Edge. Not Chromium.

### B. Git + Python (do this before PowerShell commands)

1. Install **Git for Windows** from https://git-scm.com/download/win  
   Next, Next, Next is fine. When it asks about PATH, keep **Git from the command line**.
2. Install **Python 3.12** from https://www.python.org/downloads/windows/  
   On the first installer screen, check **Add python.exe to PATH**. Then Install.

Close PowerShell if it was open. Open a **new** PowerShell. Check:

```powershell
git --version
py -3 --version
```

Both must work. If `py -3` fails, Python is not on PATH. Redo the Python installer with **Add python.exe to PATH**.

If `.\.venv\Scripts\Activate.ps1` is later blocked, run this **once**, then open a new PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### C. GitHub (the repo is private)

In Chrome on that Windows PC, sign in to GitHub as **kolbeinng**. Open:

https://github.com/kolbeinng/iphone-launch-sprint

If you see 404, you are on the wrong GitHub account. Stop and fix that.

### D. Download the project

```powershell
cd $env:USERPROFILE
mkdir Projects -ErrorAction SilentlyContinue
cd Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
git status
```

Git may open a browser to sign in. Use **kolbeinng**.

You must be on `cursor/dynamic-sku-select`. If it says `main`:

```powershell
git checkout cursor/dynamic-sku-select
```

### E. Python environment + Playwright

Stay in `iphone-launch-sprint`. Run **exactly** these lines:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chrome
```

The line **must** start with `(.venv)`. If it does not, stop.

Prove it:

```powershell
python -c "from playwright.sync_api import sync_playwright; print('playwright ok')"
```

You must see `playwright ok`.

**Every new PowerShell window:**

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
.\.venv\Scripts\Activate.ps1
```

Wait for `(.venv)`, then use `python`.

### F. `config.yaml` on this PC

```powershell
copy config.example.yaml config.yaml
```

Open `config.yaml` and set:

- `mode: test` for practice
- `dry_run: true`
- **`checkout.cvv`** — the 3 digits for the card on **this** PC

Do not email the CVV. Do not put it on GitHub.

### G. Apple card billing (days before)

Apple ID → Payment → card **billing** already Vietnam. Do this days before.

### H. `--warm-only` (only warm command)

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
.\.venv\Scripts\Activate.ps1
python assist.py --warm-only
```

Sign in + 2FA in **that** Chrome if Apple asks (phone in your hand). It adds a practice phone, reaches checkout, **empties the bag**. Leave Chrome open. Never close it. Never Sign out of Windows.

If the script cannot start because normal Chrome is already open: close Chrome fully once, then `--warm-only` again. After that, never close it.

### I. Practice

```powershell
python assist.py --now
```

Stops at **Đặt hàng**. You do not click it. Repeat until it is boring.

### J. If you disconnect RDP

**Disconnect.** Do not Sign out. Chrome must still be running when you reconnect. If Chrome was closed, run `--warm-only` again.

### K. Launch night (same PC, same open Chrome)

1. `mode: launch` and confirm `launch_at`
2. **T−10:** `python assist.py --warm-only` if Chrome is not already warm
3. **T−0:** `python assist.py --at-launch`
4. **You** click Đặt hàng

---

### If it breaks (Windows)

| What you see | What you do |
|---|---|
| `python3` not found | You are on Windows. Type `python` after `(.venv)`. |
| `running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` then new PowerShell, then Activate. |
| `No module named 'yaml'` | You skipped Activate. `.\.venv\Scripts\Activate.ps1` first. |
| Clone 404 | Wrong GitHub account. |
| Apple ID every run | You closed Chrome or Signed out of Windows. `--warm-only` again. Leave it open. |
| Script sits on sign-in | Finish 2FA in **that** Chrome. |

---

## Per-computer setup (once, days before — not at 18:50)

Do this on **the computer you will use at T-0**. Practice on another machine does not count.

1. Clone + `config.yaml` as above.
2. In a normal browser: Apple ID → Payment → card **billing** already correct (Vietnam quận/phường, no junk postal). Not at T-0 (popup ~40s).
3. `source .venv/bin/activate` then `python3 assist.py --warm-only`  
   Sign in + 2FA in the **script’s Chrome** if asked (not everyday Chrome). Adds a practice iPhone, reaches checkout, **empties the bag**. Leave that window open. Never ⌘Q.
4. `python3 assist.py --now`  
   Full dry-run → stop at Đặt hàng (not clicked). Repeat until it is boring (2–3 clean runs). Second run should select the saved shipping radio, not type a new address.

The script always applies `shipping_address` from config:

- If Apple already shows a saved radio whose **visible name + street** match → click it (~20ms).
- If not → fill **Sử dụng địa chỉ mới** from the same config (slower, still the right address).

Same Apple ID on a new computer: after login, the saved address usually appears by itself.

---

## Launch day (same computer, same open Chrome)

- **T−10:** `source .venv/bin/activate` then `python3 assist.py --warm-only` if Chrome isn’t already warm. 2FA phone in hand. Bag must end empty.
- **T−0:** `python3 assist.py --at-launch` (sleeps until `launch_at`). Do not add extra seconds after 19:00.
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
