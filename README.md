# iPhone Launch Sprint

[English version below.](#english)

Script luyện tập để mua iPhone trên **apple.com/vn**.

Nó điền hết phần thanh toán rồi **dừng ở nút Đặt hàng**. Nó không bao giờ bấm nút đó. Bạn bấm. Phải để `dry_run: true`, không thì script không chạy.

- **Luyện tập ngay:** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ
- **Đêm mở bán:** iPhone 18 Pro Max · 256GB · Burgundy — đổi một dòng trong `config.yaml` (`mode: launch`)

Một lần chạy khoảng một phút. Chỉ khoảng một giây là mình bấm; phần còn lại là Apple đang tải trang.

---

## Trước khi bắt đầu

Bạn cần làm sẵn những việc này **trước**, đừng đợi đêm mở bán.

1. **Tài khoản Apple** — đăng nhập, **lưu thẻ** (số thẻ, hạn dùng), và **địa chỉ giao hàng**. Địa chỉ thanh toán của thẻ phải là Việt Nam. Script không gõ số thẻ giúp bạn.
2. **Điện thoại của bạn**, để nhận mã 2FA của Apple.
3. **Mã CVV** (3 số) — điền vào `config.yaml` ở Bước 4. Phải có trước khi chạy. Nó không lên GitHub.

`--warm-only` (Bước 5) là lúc bạn đăng nhập. Script sẽ điền **địa chỉ giao hàng** từ file. **Thẻ bạn tự lưu** trong tài khoản Apple — script không gõ số thẻ. CVV trong `config.yaml` được gõ lúc chạy thật.

Chọn máy của bạn:

- **[Mac](#cài-đặt-trên-mac)** — lệnh bắt đầu bằng `python3`
- **[Windows 11](#cài-đặt-trên-windows-11)** — lệnh bắt đầu bằng `python`

Đừng lẫn. Mac không có lệnh `python`; Windows không có lệnh `python3`.

---

# Cài đặt trên Mac

Mở **Terminal** (bấm ⌘ Space, gõ `Terminal`, bấm Return).

Copy từng bước, dán vào Terminal, bấm Return. Làm theo thứ tự.

### Bước 1 — Cài Chrome và công cụ lập trình của Apple

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
```

Cái này cài Google Chrome và nhờ macOS cài công cụ lập trình (đó là chỗ ra lệnh `git`). Dòng `already installed - skipping` là thành công — phần đó đã có sẵn.

**Nếu hiện cửa sổ "Install the command line developer tools?"** — bấm **Install**, đồng ý, rồi đợi xong. Có thể mất 5 đến 15 phút.

Xong rồi thì **đóng Terminal, mở cái mới**, rồi làm bước 2.

### Bước 2 — Kiểm tra công cụ, rồi tải project

```bash
git --version
python3 --version
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Cả `git` và `python3` phải in ra số phiên bản. Máy Mac mới, `git --version` đôi khi hiện hộp thoại thay vì in chữ. Bấm **Install**, đợi, rồi chạy `git --version` lại. Chưa in phiên bản thì `git clone` không chạy được.

Repo này là public nên sẽ không hỏi đăng nhập GitHub. Nếu nó hỏi tên hoặc mật khẩu, là bạn gõ sai địa chỉ — copy lại dòng `git clone`.

**Dùng `git clone` ở trên, đừng bấm nút xanh Code → Download ZIP.** File ZIP bung ra thư mục tên `iphone-launch-sprint-main`, nên mọi lệnh `cd iphone-launch-sprint` trong hướng dẫn này sẽ lỗi. Nếu bạn đã tải ZIP, đổi tên thư mục thành `iphone-launch-sprint` là các bước sau vẫn dùng được.

### Bước 3 — Cài các gói Python

```bash
cd ~/Projects/iphone-launch-sprint
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -c "from playwright.sync_api import sync_playwright; print('all good')"
```

Hai thứ phải đúng trước khi làm tiếp:

- Đầu dòng Terminal hiện **`(.venv)`**
- Lệnh cuối in **`all good`**

### Bước 4 — Tạo file config

```bash
cp config.example.yaml config.yaml
open -e config.yaml
```

TextEdit mở ra. Điền **`cvv`** (3 số trên thẻ), rồi bấm ⌘S để lưu và đóng cửa sổ.

Các thứ khác để yên. Tên, địa chỉ, email và số điện thoại đã có trong file. Để nguyên `mode: test` và `dry_run: true`.

### Bước 5 — Đăng nhập Apple

```bash
python3 assist.py --warm-only
```

Một cửa sổ Chrome mở ra. **Đăng nhập Apple trong cửa sổ đó** và nhập mã 2FA trên điện thoại. Script đang đợi bạn — không phải bị treo.

Sau đó nó thêm một iPhone thử, vào thanh toán, **điền địa chỉ giao hàng** từ `config.yaml` (hoặc bấm địa chỉ đã lưu), rồi **xóa túi**. Đó là lúc địa chỉ có sẵn trên tài khoản. **Thẻ bạn tự thêm** trong tài khoản Apple nếu chưa có — script không gõ số thẻ. CVV trong file được gõ lúc chạy thật (`--now` / `--at-launch`).

**Để cửa sổ Chrome đó mở. Đừng tắt.** Tắt là phải đăng nhập và 2FA lại từ đầu.

### Bước 6 — Chạy thử

```bash
python3 assist.py --now
```

Xem nó chọn máy, từ chối đổi cũ lấy mới và AppleCare, rồi đi hết thanh toán. Nó dừng ở **Đặt hàng** và không bấm.

Chạy vài lần đến khi chán. Đó mới là mục tiêu.

### Mở Terminal lần sau

Mỗi cửa sổ Terminal mới cần hai dòng này trước:

```bash
cd ~/Projects/iphone-launch-sprint
source .venv/bin/activate
```

Đợi `(.venv)`, rồi mới chạy lệnh tiếp.

### Bước 7 — Đêm mở bán

Thứ Bảy **12 tháng 9 năm 2026 lúc 19:00** giờ Việt Nam. Đừng tự bấm Return đúng 19:00. Bạn khởi động sớm, script sẽ đợi.

**Gợi ý:** ngồi vào lúc **18:45**. Cắm sạc laptop. Điện thoại trong tay. Tắt Không làm phiền. Cùng máy bạn đã luyện tập.

1. Mở `config.yaml` và đổi **một chữ**: `mode: test` → `mode: launch`. Lưu.
2. Mở Terminal, dán hai dòng ở **Mở Terminal lần sau**. Đợi `(.venv)`.
3. Làm ấm đăng nhập. Đăng nhập và làm 2FA nếu Apple hỏi. **Để Chrome mở.**

```bash
python3 assist.py --warm-only
```

4. Trong **cùng** cửa sổ Terminal, bắt đầu đợi:

```bash
python3 assist.py --at-launch
```

Bạn sẽ thấy đếm ngược, kiểu `T-0: 8.4 min left`, rồi `T-0 — GO`. Ở gần máy. Đừng đóng Chrome. Đừng tắt Terminal. Đừng cố canh giờ. Nếu nó kêu hoặc nhờ bạn bấm màu / dung lượng, bấm trong Chrome.

Khi tới Đặt hàng thì nó dừng và kêu. **Bạn** bấm Đặt hàng. Script không bao giờ bấm.

Bạn có thể chạy `--at-launch` sớm năm, mười hoặc ba mươi phút. Đợi không mất gì. Lỡ thì mất máy. Nếu đã quá 19:00, nó in `launch_at already past — sprinting NOW` rồi chạy ngay.

---

# Cài đặt trên Windows 11

Bấm **Start**, gõ `PowerShell`, mở **Windows PowerShell**.

Copy từng bước, dán, bấm Enter. Làm theo thứ tự. Windows hỏi quyền thì bấm **Yes**.

### Bước 1 — Cài Chrome, Git và Python

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
winget install --id Google.Chrome -e --accept-package-agreements --accept-source-agreements
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
```

Mất vài phút. Xong thì **đóng PowerShell, mở cái mới.** Chương trình mới chỉ hiện trong cửa sổ mới.

### Bước 2 — Kiểm tra công cụ, rồi tải project

```powershell
git --version
py -3 --version
cd $env:USERPROFILE
mkdir Projects -ErrorAction SilentlyContinue
cd Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Cả hai phải in số phiên bản. Không thì bạn còn đang ở cửa sổ PowerShell cũ — mở cái mới.

Repo này là public nên sẽ không hỏi đăng nhập GitHub. Nếu nó hỏi tên hoặc mật khẩu, là bạn gõ sai địa chỉ — copy lại dòng `git clone`.

**Dùng `git clone` ở trên, đừng bấm nút xanh Code → Download ZIP.** File ZIP bung ra thư mục tên `iphone-launch-sprint-main`, nên mọi lệnh `cd iphone-launch-sprint` trong hướng dẫn này sẽ lỗi. Nếu bạn đã tải ZIP, đổi tên thư mục thành `iphone-launch-sprint` là các bước sau vẫn dùng được.

### Bước 3 — Cài các gói Python

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "from playwright.sync_api import sync_playwright; print('all good')"
```

Hai thứ phải đúng trước khi làm tiếp:

- Đầu dòng PowerShell hiện **`(.venv)`**
- Lệnh cuối in **`all good`**

### Bước 4 — Tạo file config

```powershell
copy config.example.yaml config.yaml
notepad config.yaml
```

Notepad mở ra. Điền **`cvv`** (3 số trên thẻ), rồi Save và đóng.

Các thứ khác để yên. Tên, địa chỉ, email và số điện thoại đã có trong file. Để nguyên `mode: test` và `dry_run: true`.

### Bước 5 — Đăng nhập Apple

```powershell
python assist.py --warm-only
```

Một cửa sổ Chrome mở ra. **Đăng nhập Apple trong cửa sổ đó** và nhập mã 2FA trên điện thoại. Script đang đợi bạn — không phải bị treo.

Sau đó nó thêm một iPhone thử, vào thanh toán, **điền địa chỉ giao hàng** từ `config.yaml` (hoặc bấm địa chỉ đã lưu), rồi **xóa túi**. Đó là lúc địa chỉ có sẵn trên tài khoản. **Thẻ bạn tự thêm** trong tài khoản Apple nếu chưa có — script không gõ số thẻ. CVV trong file được gõ lúc chạy thật (`--now` / `--at-launch`).

**Để cửa sổ Chrome đó mở. Đừng đóng.** Đóng là phải đăng nhập và 2FA lại từ đầu.

### Bước 6 — Chạy thử

```powershell
python assist.py --now
```

Xem nó chọn máy, từ chối đổi cũ lấy mới và AppleCare, rồi đi hết thanh toán. Nó dừng ở **Đặt hàng** và không bấm.

Chạy vài lần đến khi chán.

### Mở PowerShell lần sau

Mỗi cửa sổ PowerShell mới cần hai dòng này trước:

```powershell
cd $env:USERPROFILE\Projects\iphone-launch-sprint
.\.venv\Scripts\Activate.ps1
```

Đợi `(.venv)`, rồi mới chạy lệnh tiếp.

### Bước 7 — Đêm mở bán

Thứ Bảy **12 tháng 9 năm 2026 lúc 19:00** giờ Việt Nam. Đừng tự bấm Enter đúng 19:00. Bạn khởi động sớm, script sẽ đợi.

**Gợi ý:** ngồi vào lúc **18:45**. Cắm sạc laptop. Điện thoại trong tay. Tắt Không làm phiền. Cùng máy bạn đã luyện tập.

1. Mở `config.yaml` và đổi **một chữ**: `mode: test` → `mode: launch`. Lưu.
2. Mở PowerShell, dán hai dòng ở **Mở PowerShell lần sau**. Đợi `(.venv)`.
3. Làm ấm đăng nhập. Đăng nhập và làm 2FA nếu Apple hỏi. **Để Chrome mở.**

```powershell
python assist.py --warm-only
```

4. Trong **cùng** cửa sổ PowerShell, bắt đầu đợi:

```powershell
python assist.py --at-launch
```

Bạn sẽ thấy đếm ngược, kiểu `T-0: 8.4 min left`, rồi `T-0 — GO`. Ở gần máy. Đừng đóng Chrome. Đừng đóng PowerShell. Đừng cố canh giờ. Nếu nó kêu hoặc nhờ bạn bấm màu / dung lượng, bấm trong Chrome.

Khi tới Đặt hàng thì nó dừng và kêu. **Bạn** bấm Đặt hàng. Script không bao giờ bấm.

Bạn có thể chạy `--at-launch` sớm năm, mười hoặc ba mươi phút. Đợi không mất gì. Lỡ thì mất máy. Nếu đã quá 19:00, nó in `launch_at already past — sprinting NOW` rồi chạy ngay.

---

## Khi có chuyện không ổn

| Bạn thấy gì | Nghĩa là gì |
|---|---|
| `command not found: python` | Bạn đang dùng Mac. Dùng `python3`. |
| `python3 is not recognized` | Bạn đang dùng Windows. Dùng `python`. |
| `No module named 'yaml'` | Dòng lệnh thiếu `(.venv)`. Chạy hai dòng “mở lần sau” trước. |
| `Invalid timezone: Asia/Ho_Chi_Minh` | Bản cũ. Trong thư mục project, đã có `(.venv)`: `python3 -m pip install -r requirements.txt` (Windows: `py -3 -m pip install -r requirements.txt`). |
| `git: command not found` | Bước 1 chưa xong. Đợi, rồi mở Terminal hoặc PowerShell mới. |
| Apple hỏi đăng nhập mỗi lần chạy | Chrome bị đóng. Chạy `--warm-only` lại và để mở. |
| Như bị kẹt ở trang đăng nhập | Nó đang đợi bạn. Nhập mã 2FA trong cửa sổ Chrome đó. |
| Không có gì xảy ra 10–20 giây | Đó là Apple đang tải trang. Đừng bấm gì. |
| `USER PICK waiting for COLOR…` (hoặc SIZE, hoặc STORAGE) | Tự bấm ô đó trong Chrome, lần chạy sẽ tiếp tục. |
| `REFUSE: not iPhone 18` | Nó vào nhầm máy và dừng cố ý. Túi không bị thêm gì. |
| `Could not empty bag` | Mở túi Apple trong Chrome, xóa hết bằng tay, rồi chạy lại. |

---

# English

A practice script for buying an iPhone on **apple.com/vn**.

It fills the whole checkout for you and then **stops at the Đặt hàng button**. It never clicks it. You do. `dry_run: true` is required and the script refuses to start without it.

- **Practice now:** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ
- **Launch night:** iPhone 18 Pro Max · 256GB · Burgundy — one line in `config.yaml` (`mode: launch`)

A full run takes about a minute. Roughly one second of that is our clicking; the rest is Apple's pages loading.

---

## Before you start

Do these **beforehand**, not on launch night.

1. **An Apple ID** — sign in, **save your card** (number and expiry), and **save a shipping address**. The card's billing address must be Vietnam. The script never types the card number for you.
2. **Your phone**, for the Apple 2FA code.
3. **Your card's CVV** (the 3 digits) — put it in `config.yaml` in Step 4. It must be there before you run. It never goes to GitHub.

`--warm-only` (Step 5) is when you sign in. The script puts the **delivery address** from the file onto your Apple account. **You save the card yourself** — the script never types the card number. The CVV in `config.yaml` is typed on the real run.

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
```

This installs Google Chrome and asks macOS for the developer tools (that is where `git` comes from). `already installed - skipping` is a success — that part was already done.

**If a window pops up saying "Install the command line developer tools?"** — click **Install**, agree, and wait until it finishes. This can take 5 to 15 minutes.

When it is done, **close Terminal and open a new one**, then go to step 2.

### Step 2 — Check the tools, then download the project

```bash
git --version
python3 --version
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Both `git` and `python3` must print a version number. On a brand-new Mac, `git --version` may pop up a dialog instead. Click **Install**, wait, then run `git --version` again. Until it prints a version, `git clone` cannot work.

The repo is public, so nothing will ask you to sign in to GitHub. If it asks for a username or password, the address was typed wrong — copy the `git clone` line again.

**Use the `git clone` above, not the green Code → Download ZIP button.** The ZIP unpacks to a folder called `iphone-launch-sprint-main`, so every `cd iphone-launch-sprint` in this guide would fail. If you already took the ZIP, rename the folder to `iphone-launch-sprint` and the rest of the guide works.

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

TextEdit opens. Fill in **`cvv`** (your card's 3 digits), then press ⌘S to save and close the window.

Leave everything else alone. The name, address, email and phone are already in the file. Leave `mode: test` and `dry_run: true` alone.

### Step 5 — Sign in to Apple

```bash
python3 assist.py --warm-only
```

A Chrome window opens. **Sign in to Apple in that window** and finish the 2FA code from your phone. The script waits for you — it is not frozen.

It then adds a test iPhone, goes to checkout, **puts the delivery address** from `config.yaml` on your Apple account (or clicks the saved one), and **empties the bag**. That is how the address is in place before launch. **You add the card yourself** in the Apple account if it is not there yet — the script never types the card number. The CVV in the file is typed on the real run (`--now` / `--at-launch`).

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

Wait for `(.venv)`, then you can run the next command.

### Step 7 — Launch night

Saturday **12 September 2026 at 19:00** Vietnam time. You do not try to press Return at 19:00 yourself. You start early, and the script waits.

**The suggestion:** sit down at **18:45**. Laptop plugged in. Phone in your hand. Do Not Disturb off. Same computer you practised on.

1. Open `config.yaml` and change **one word**: `mode: test` → `mode: launch`. Save.
2. Open Terminal and paste the two lines from **Opening Terminal again later**. Wait for `(.venv)`.
3. Warm the login. Sign in and do 2FA if Apple asks. **Leave Chrome open.**

```bash
python3 assist.py --warm-only
```

4. In the **same** Terminal window, start the wait:

```bash
python3 assist.py --at-launch
```

You will see a countdown, like `T-0: 8.4 min left`, then `T-0 — GO`. Stay nearby. Do not close Chrome. Do not quit Terminal. Do not try to time it. If it beeps or asks you to click a colour or storage, click it in Chrome.

When it reaches Đặt hàng it stops and beeps. **You** click Đặt hàng. The script never clicks it.

You can start `--at-launch` five, ten or thirty minutes early. Waiting is free. Missing it is not. If 19:00 has already passed, it prints `launch_at already past — sprinting NOW` and goes immediately.

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
git clone https://github.com/kolbeinng/iphone-launch-sprint.git
cd iphone-launch-sprint
```

Both must print a version number. If not, you are still in the old PowerShell window — open a new one.

The repo is public, so nothing will ask you to sign in to GitHub. If it asks for a username or password, the address was typed wrong — copy the `git clone` line again.

**Use the `git clone` above, not the green Code → Download ZIP button.** The ZIP unpacks to a folder called `iphone-launch-sprint-main`, so every `cd iphone-launch-sprint` in this guide would fail. If you already took the ZIP, rename the folder to `iphone-launch-sprint` and the rest of the guide works.

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

Notepad opens. Fill in **`cvv`** (your card's 3 digits), then Save and close.

Leave everything else alone. The name, address, email and phone are already in the file. Leave `mode: test` and `dry_run: true` alone.

### Step 5 — Sign in to Apple

```powershell
python assist.py --warm-only
```

A Chrome window opens. **Sign in to Apple in that window** and finish the 2FA code from your phone. The script waits for you — it is not frozen.

It then adds a test iPhone, goes to checkout, **puts the delivery address** from `config.yaml` on your Apple account (or clicks the saved one), and **empties the bag**. That is how the address is in place before launch. **You add the card yourself** in the Apple account if it is not there yet — the script never types the card number. The CVV in the file is typed on the real run (`--now` / `--at-launch`).

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

Wait for `(.venv)`, then you can run the next command.

### Step 7 — Launch night

Saturday **12 September 2026 at 19:00** Vietnam time. You do not try to press Enter at 19:00 yourself. You start early, and the script waits.

**The suggestion:** sit down at **18:45**. Laptop plugged in. Phone in your hand. Do Not Disturb off. Same computer you practised on.

1. Open `config.yaml` and change **one word**: `mode: test` → `mode: launch`. Save.
2. Open PowerShell and paste the two lines from **Opening PowerShell again later**. Wait for `(.venv)`.
3. Warm the login. Sign in and do 2FA if Apple asks. **Leave Chrome open.**

```powershell
python assist.py --warm-only
```

4. In the **same** PowerShell window, start the wait:

```powershell
python assist.py --at-launch
```

You will see a countdown, like `T-0: 8.4 min left`, then `T-0 — GO`. Stay nearby. Do not close Chrome. Do not close PowerShell. Do not try to time it. If it beeps or asks you to click a colour or storage, click it in Chrome.

When it reaches Đặt hàng it stops and beeps. **You** click Đặt hàng. The script never clicks it.

You can start `--at-launch` five, ten or thirty minutes early. Waiting is free. Missing it is not. If 19:00 has already passed, it prints `launch_at already past — sprinting NOW` and goes immediately.

---

## When something goes wrong

| What you see | What it means |
|---|---|
| `command not found: python` | You are on a Mac. Use `python3`. |
| `python3 is not recognized` | You are on Windows. Use `python`. |
| `No module named 'yaml'` | Your line is missing `(.venv)`. Run the two “open again later” lines first. |
| `Invalid timezone: Asia/Ho_Chi_Minh` | Old clone. From the project folder, with `(.venv)` showing: `python3 -m pip install -r requirements.txt` (Windows: `py -3 -m pip install -r requirements.txt`). |
| `git: command not found` | Step 1 is not finished. Wait, then use a new Terminal or PowerShell. |
| Apple asks you to sign in on every run | Chrome got closed. Run `--warm-only` again and leave it open. |
| It seems stuck on a sign-in page | It is waiting for you. Finish the 2FA code in that Chrome window. |
| Nothing happens for 10–20 seconds | That is Apple's page loading. Do not click anything. |
| `USER PICK waiting for COLOR…` (or SIZE, or STORAGE) | Click that tile yourself in Chrome and the run continues. |
| `REFUSE: not iPhone 18` | It landed on the wrong phone and stopped on purpose. Nothing was added to your bag. |
| `Could not empty bag` | Open the Apple bag in Chrome, remove everything by hand, then run again. |
