# iPhone Launch Sprint

Dry-run for apple.com/vn. Never clicks **Đặt hàng**. `dry_run: true` is required.

**TEST:** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ  
**LAUNCH:** iPhone 18 Pro Max — `mode: launch` in `config.yaml`

Branch: **`cursor/dynamic-sku-select`**. Do not open a pull request.

`config.yaml` stays on the computer (CVV). Copy it from `config.example.yaml`.

The only script is **`assist.py`**. After setup: `--warm-only`, then `--now`.

Launch-night notes: **[checklist.md](checklist.md)**.

---

## macOS

Open **Terminal**. Paste block 1. When the Mac window appears, click **Install** and wait until it finishes. Then a **new** Terminal, block 2, 3, 4.

On a Mac the command is `python3`.

### Block 1 — Chrome, Git, Python

```bash
curl -fsSL -o /tmp/googlechrome.dmg "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg"
hdiutil attach /tmp/googlechrome.dmg -nobrowse
test -d "/Applications/Google Chrome.app" || cp -R "/Volumes/Google Chrome/Google Chrome.app" /Applications/
hdiutil detach "/Volumes/Google Chrome"
rm -f /tmp/googlechrome.dmg
xcode-select --install
```

`xcode-select` opens a Mac dialog. Click **Install**. Wait until it is done (often 5–15 minutes). If it says already installed, continue.

Close Terminal. Open a **new** Terminal.

### Block 2 — check + download

```bash
git --version
python3 --version
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
git checkout cursor/dynamic-sku-select
```

The repo is **private**. Clone still works. Git will open a browser — sign in as **kolbeinng**.  
`git` and `python3` must print a version. If they do not, block 1 is not finished.  
If clone says **404**, you signed in as the wrong GitHub account. Sign in as **kolbeinng**, then run the `git clone` lines again.

### Block 3 — packages + config

```bash
cd ~/Projects/iphone-launch-sprint
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m playwright install chrome
python3 -c "from playwright.sync_api import sync_playwright; print('playwright ok')"
cp config.example.yaml config.yaml
open -e config.yaml
```

The line must start with `(.venv)`. The check must print `playwright ok`.  
In TextEdit: keep `mode: test`, keep `dry_run: true`, type `checkout.cvv`, Save, close.

### Block 4 — warm, then practice

```bash
python3 assist.py --warm-only
```

Sign in + 2FA in **that** Chrome if Apple asks. Leave Chrome open. Bag must end empty.

```bash
python3 assist.py --now
```

Stops at **Đặt hàng**. Do not click it.

New Terminal later:

```bash
cd ~/Projects/iphone-launch-sprint
source .venv/bin/activate
```

Then `python3 assist.py …` again.

### Launch night

`mode: launch` and check `launch_at`. Same open Chrome:

```bash
python3 assist.py --warm-only
python3 assist.py --at-launch
```

**You** click Đặt hàng.

---

## Windows 11

Start → `PowerShell`. Paste block 1. Close PowerShell. New PowerShell. Blocks 2, 3, 4.

On Windows the command is `python`.  
If Windows asks **Yes**, click Yes. If Git opens a browser, sign in as **kolbeinng**.

### Block 1 — Chrome, Git, Python

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
winget install --id Google.Chrome -e --accept-package-agreements --accept-source-agreements
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
```

Close this window. Open a **new** PowerShell.

### Block 2 — check + download

```powershell
git --version
py -3 --version
cd $env:USERPROFILE
mkdir Projects -ErrorAction SilentlyContinue
cd Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
git checkout cursor/dynamic-sku-select
```

The repo is **private**. Clone still works. Git will open a browser — sign in as **kolbeinng**.  
`git` and `py -3` must print a version.  
If clone says **404**, you signed in as the wrong GitHub account. Sign in as **kolbeinng**, then run the `git clone` lines again.

### Block 3 — packages + config

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chrome
python -c "from playwright.sync_api import sync_playwright; print('playwright ok')"
copy config.example.yaml config.yaml
notepad config.yaml
```

The line must start with `(.venv)`. The check must print `playwright ok`.  
In Notepad: keep `mode: test`, keep `dry_run: true`, type `checkout.cvv`, Save, close.

### Block 4 — warm, then practice

```powershell
python assist.py --warm-only
```

Sign in + 2FA in **that** Chrome if Apple asks. Leave Chrome open. Bag must end empty.

```powershell
python assist.py --now
```

Stops at **Đặt hàng**. Do not click it.

New PowerShell later:

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
.\.venv\Scripts\Activate.ps1
```

Then `python assist.py …` again.

### Launch night

`mode: launch` and check `launch_at`. Same open Chrome:

```powershell
python assist.py --warm-only
python assist.py --at-launch
```

**You** click Đặt hàng.
