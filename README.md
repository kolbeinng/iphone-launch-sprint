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

## The two commands

That is the whole script. Everything else is setup.

| Command | What it does |
|---|---|
| `--warm-only` | Signs in to Apple and checkout, then empties the bag. Run it before launch. |
| `--now` | The full run. Stops at Đặt hàng. |
| `--at-launch` | Same as `--now`, but waits until `launch_at` in your config first. |

On a Mac put `python3 assist.py` in front. On Windows put `python assist.py` in front.

Launch-night steps and what each checkout screen means: **[checklist.md](checklist.md)**.

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
