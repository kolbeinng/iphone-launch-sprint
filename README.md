# iPhone Launch Sprint

Dry-run kit for apple.com/vn. It never clicks **Đặt hàng**. `dry_run: true` is required.

**TEST:** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ  
**LAUNCH:** iPhone 18 Pro Max — set `mode: launch` in `config.yaml`

GitHub branch: **`cursor/dynamic-sku-select`**. Do not click **Compare & pull request**.

`config.yaml` is not on GitHub. You create it on each computer and type the CVV there.

The script is **`assist.py`**. After setup you only run `--warm-only` then `--now`.

Mac setup: **[checklist.md](checklist.md)** → **New Mac**.

---

## Windows 11

Start → type `PowerShell` → open **Windows PowerShell**.  
Paste **block 1**. When it finishes, close PowerShell. Open a **new** PowerShell. Paste **block 2**, then **block 3**, then **block 4**.

If Windows asks **Yes**, click Yes.  
If Git opens a browser, sign in as **kolbeinng**.

### Block 1 — Chrome, Git, Python

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
winget install --id Google.Chrome -e --accept-package-agreements --accept-source-agreements
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
```

Close this PowerShell window. Open a **new** one.

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

`git` and `py -3` must print a version. If clone says **404**, sign in to github.com as **kolbeinng** in Chrome, then run the `git clone` lines again.

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
In Notepad: keep `mode: test`, keep `dry_run: true`, type `checkout.cvv`, Save, close Notepad.

### Block 4 — warm, then practice

```powershell
python assist.py --warm-only
```

Sign in + 2FA in **that** Chrome if Apple asks. Leave Chrome open. Bag must end empty.

Then:

```powershell
python assist.py --now
```

Stops at **Đặt hàng**. Do not click it.

If you opened a **new** PowerShell, paste this first and wait for `(.venv)`:

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
.\.venv\Scripts\Activate.ps1
```

### Launch night

In `config.yaml` set `mode: launch` and check `launch_at`. Same open Chrome:

```powershell
python assist.py --warm-only
python assist.py --at-launch
```

**You** click Đặt hàng.
