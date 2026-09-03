# iPhone Launch Sprint

A practice script for buying an iPhone on **apple.com/vn**.

It fills the whole checkout for you and then **stops at the Đặt hàng button**. It never clicks it. You do. `dry_run: true` is required and the script refuses to start without it.

- **Practice now:** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ
- **Launch night:** iPhone 18 Pro Max — one line in `config.yaml`

A full run takes about a minute. Roughly one second of that is our clicking; the rest is Apple's pages loading.

---

## Before you start

You need these four things. Get them now, not on launch night.

1. **An Apple ID** with your card and shipping address already saved, and the card's **billing address set to Vietnam**.
2. **Your phone**, for the Apple 2FA code.
3. **A GitHub account.** This repo is private, so the owner (**kolbeinng**) must add you under Settings → Collaborators.
4. **Your card's CVV** (the 3 digits). You type it on your own computer. It never goes to GitHub.

Then pick your computer:

- **[Mac](#mac-setup)** — commands start with `python3`
- **[Windows 11](#windows-11-setup)** — commands start with `python`

Do not mix them. A Mac has no `python` command; Windows has no `python3`.

---

# Mac setup

Open **Terminal** (press ⌘ Space, type `Terminal`, press Return).

Copy each step, paste it into Terminal, press Return. Do them in order.

### Step 1 — Install Chrome and Apple's developer tools

```bash
if [ -d "/Applications/Google Chrome.app" ]; then
  echo "Chrome: already installed - skipping"
else
  echo "Chrome: downloading, about 200 MB, this takes a minute or two..."
  curl -# -fSL -o /tmp/chrome.dmg "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg"
  echo "Chrome: installing..."
  yes | hdiutil attach -nobrowse -noverify /tmp/chrome.dmg
  cp -R "/Volumes/Google Chrome/Google Chrome.app" /Applications/
  hdiutil detach "/Volumes/Google Chrome"
  rm -f /tmp/chrome.dmg
  echo "Chrome: installed"
fi

if xcode-select -p >/dev/null 2>&1; then
  echo "Developer tools: already installed - skipping"
else
  echo "Developer tools: asking macOS to install - click Install in the popup"
  xcode-select --install
fi

echo
echo "Chrome:  $( [ -d '/Applications/Google Chrome.app' ] && echo OK || echo MISSING )"
echo "git:     $(git --version 2>/dev/null || echo MISSING)"
echo "python3: $(python3 --version 2>&1 || echo MISSING)"
```

This installs Google Chrome and asks macOS for the developer tools (that is where `git` comes from). Every line tells you what it is doing, and the last three lines are the summary that matters.

**Read the summary.** All three should say `OK` or print a version number:

```
Chrome:  OK
git:     git version 2.50.1 (Apple Git-155)
python3: Python 3.14.6
```

`already installed - skipping` is a success, not a problem — it means that part was done before you started.

**If a window pops up saying "Install the command line developer tools?"** — click **Install**, agree, and wait until it finishes. This can take 5 to 15 minutes. While it runs, `git` will still say `MISSING`; that is expected.

When it is done, **close Terminal and open a new one**, then paste the block again. This time `git` should print a version.

### Step 2 — Check the tools, then download the project

```bash
git --version
python3 --version
mkdir -p ~/Projects
cd ~/Projects
git clone -b cursor/dynamic-sku-select https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Both `git` and `python3` must print a version number. If either says "command not found", step 1 has not finished — wait for it and try again.

A browser window will open asking you to sign in to GitHub. Sign in as yourself. If you get **404**, you do not have access to the repo yet — ask the owner to add you.

### Step 3 — Install the Python packages

```bash
cd ~/Projects/iphone-launch-sprint
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -c "from playwright.sync_api import sync_playwright; print('all good')"
```

Two things must be true before you continue:

- The start of your Terminal line now shows **`(.venv)`**
- The last command printed **`all good`**

### Step 4 — Make your config file

```bash
cp config.example.yaml config.yaml
open -e config.yaml
```

TextEdit opens. Change these to **your own** details, then press ⌘S to save and close the window:

| Setting | What to put |
|---|---|
| `cvv` | your card's 3 digits |
| `shipping_address` | your name, street, city, district |
| `contact` | your email and phone |

Leave `mode: test` and `dry_run: true` alone.

**Which phone it picks** is four fields, and the file explains each one where you edit it:

| Field | What it does |
|---|---|
| `year` | The guard. A page from any other generation is refused before anything is added to the bag |
| `model` | `pro-max`, `pro`, `plus`, `base` or `air`. Picks the size tile and decides which check runs |
| `colors` | The one that breaks on launch night. Write the English name |
| `storages` | Forgiving — `"256"` matches `"256GB"` |

You do not set the screen size or the hub search words. Both are worked out from `year` and `model`.

`year` is really the word that has to appear in "iPhone ___", so a phone with no number in its name uses the family word instead. iPhone Air is `year: air` with `model: air`.

### Step 5 — Sign in to Apple

```bash
python3 assist.py --warm-only
```

A Chrome window opens. **Sign in to Apple in that window** and finish the 2FA code from your phone. The script waits for you — it is not frozen.

It then adds a test iPhone, goes to checkout, and **empties the bag** again. That is normal; it is only warming up your login.

**Leave that Chrome window open. Do not quit it.** Quitting it means signing in and doing 2FA all over again.

### Step 6 — Do a practice run

```bash
python3 assist.py --now
```

Watch it pick the phone, decline trade-in and AppleCare, and go through checkout. It stops at **Đặt hàng** and does not click it.

Run it a few times until it feels boring. That is the whole point.

### Opening Terminal again later

Every new Terminal window needs these two lines first:

```bash
cd ~/Projects/iphone-launch-sprint
source .venv/bin/activate
```

Wait for `(.venv)`, then run `python3 assist.py --now`.

---

# Windows 11 setup

Click **Start**, type `PowerShell`, open **Windows PowerShell**.

Copy each step, paste it, press Enter. Do them in order. If Windows asks for permission, click **Yes**.

### Step 1 — Install Chrome, Git and Python

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
winget install --id Google.Chrome -e --accept-package-agreements --accept-source-agreements
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
```

This takes a few minutes. When it finishes, **close PowerShell and open a new one.** The new programs only appear in a fresh window.

### Step 2 — Check the tools, then download the project

```powershell
git --version
py -3 --version
cd $env:USERPROFILE
mkdir Projects -ErrorAction SilentlyContinue
cd Projects
git clone -b cursor/dynamic-sku-select https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Both must print a version number. If not, you are still in the old PowerShell window — open a new one.

A browser window will open asking you to sign in to GitHub. Sign in as yourself. If you get **404**, you do not have access to the repo yet — ask the owner to add you.

### Step 3 — Install the Python packages

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "from playwright.sync_api import sync_playwright; print('all good')"
```

Two things must be true before you continue:

- The start of your PowerShell line now shows **`(.venv)`**
- The last command printed **`all good`**

### Step 4 — Make your config file

```powershell
copy config.example.yaml config.yaml
notepad config.yaml
```

Notepad opens. Change these to **your own** details, then Save and close:

| Setting | What to put |
|---|---|
| `cvv` | your card's 3 digits |
| `shipping_address` | your name, street, city, district |
| `contact` | your email and phone |

Leave `mode: test` and `dry_run: true` alone.

Which phone it picks is the same four fields as on the Mac side above: `year`, `model`, `colors`, `storages`. Write colours in English.

### Step 5 — Sign in to Apple

```powershell
python assist.py --warm-only
```

A Chrome window opens. **Sign in to Apple in that window** and finish the 2FA code from your phone. The script waits for you — it is not frozen.

It then adds a test iPhone, goes to checkout, and **empties the bag** again. That is normal; it is only warming up your login.

**Leave that Chrome window open. Do not close it.** Closing it means signing in and doing 2FA all over again.

### Step 6 — Do a practice run

```powershell
python assist.py --now
```

Watch it pick the phone, decline trade-in and AppleCare, and go through checkout. It stops at **Đặt hàng** and does not click it.

Run it a few times until it feels boring.

### Opening PowerShell again later

Every new PowerShell window needs these two lines first:

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
.\.venv\Scripts\Activate.ps1
```

Wait for `(.venv)`, then run `python assist.py --now`.

---

## The commands

That is the whole thing. Everything else is setup.

| Command | What it does |
|---|---|
| `assist.py --warm-only` | Signs in to Apple and checkout, then empties the bag. Run it before launch. |
| `assist.py --now` | The full run. Stops at Đặt hàng. |
| `assist.py --at-launch` | Same as `--now`, but waits until `launch_at` in your config first. |
| `probe_family.py` | Checks your config against the live Apple page. Changes nothing. |

On a Mac put `python3` in front. On Windows put `python` in front. So the full run on a Mac is `python3 assist.py --now`.

Launch-night steps and what each checkout screen means: **[checklist.md](checklist.md)**.

### What `probe_family.py` is for

The script has to click the colour, size and storage you asked for in `config.yaml`. It finds them by matching the words you wrote against the words Apple shows on the page. If Apple uses a different word, the script cannot find your choice, so it stops and waits for you to click it by hand.

That matters because Apple renames colours between iPhone generations. "Cam Vũ Trụ" exists on the iPhone 17 Pro but not on the iPhone 16, which uses "Hồng", "Trắng", "Đen" and so on.

`probe_family.py` tells you in about 5 seconds whether your words match, before it costs you anything:

```
COLOR: [dimensionColorsilver='Bạc', dimensionColorcosmicorange='Cam Vũ Trụ', dimensionColordeepblue='Xanh Đậm']
  prefs: MATCH on 'cosmicorange' → dimensionColorcosmicorange
VERDICT: config would sprint clean
```

That is what you want to see. If instead it says:

```
prefs: NO MATCH for COLOR — tried ['cherry', 're:cherry|burgundy|wine']
       available: [dimensionColorultramarine='Xanh Lưu Ly', dimensionColorpink='Hồng']
```

then copy one of the names it lists under `available` into the `colors` list in `config.yaml` and run it again until it says `ALL CLEAR`.

**Write colours in English.** Look at the line above: the label Apple shows you is Vietnamese, but the handle in front of it stays English. The script matches your word against both, so `cosmicorange` picks the tile that reads "Cam Vũ Trụ". Prefer English, because the Vietnamese names cannot be guessed in advance — Apple translated Ultramarine as "Xanh Lưu Ly" and Teal as "Xanh Mòng Két". Either works, but only the English one is predictable before the page exists.

**Before the new iPhone is announced, expect this instead — it is normal, not a fault:**

```
h1:      'Chúng tôi không tìm được trang mà bạn đang tìm.'
VERDICT: not a live configure page. The sprint would skip this URL.
ATTENTION — at least one page is dead or would stop the sprint
```

Apple has not published the page yet, so there is nothing to check. You cannot get a useful answer until the phone is on sale. Run it on launch night the moment the page goes live, before the real run.

### Trying a different phone without touching your real config

Every command takes `-c` to point at a different config file. Your own `config.yaml` is left alone:

```bash
python3 probe_family.py -c /tmp/mytest.yaml
python3 assist.py -c /tmp/mytest.yaml --now
```

Keep practice configs **outside** the project folder, as in `/tmp` above. Your config file contains your card's security code, and the project folder is a git repo.

---

## When something goes wrong

| What you see | What it means |
|---|---|
| `command not found: python` | You are on a Mac. Use `python3`. |
| `python3 is not recognized` | You are on Windows. Use `python`. |
| `No module named 'yaml'` | Your line is missing `(.venv)`. Run the activate line first. |
| `Invalid timezone: Asia/Ho_Chi_Minh` | Old clone. Run the install line again: `pip install -r requirements.txt`. |
| `git: command not found` | Step 1 is not finished. Wait, then use a new Terminal or PowerShell. |
| `404` when downloading the project | You do not have access to the private repo yet. |
| Apple asks you to sign in on every run | Chrome got closed. Run `--warm-only` again and leave it open. |
| It seems stuck on a sign-in page | It is waiting for you. Finish the 2FA code in that Chrome window. |
| Nothing happens for 10–20 seconds | That is Apple's page loading. Do not click anything. |
| `USER PICK waiting for COLOR…` (or SIZE, or STORAGE) | Your words in `config.yaml` do not match what Apple shows. Click it yourself in Chrome and the run continues. Then fix your config with `probe_family.py`. |
| `REFUSE: not iPhone 18` | A safety guard. It landed on the wrong phone's page and stopped rather than order the wrong thing. Nothing was added to your bag. |
| `Could not empty bag` | Open the Apple bag in Chrome, remove everything by hand, then run again. |
