# Don't forget

Scripts never enter your Apple password and never click **Đặt hàng**.

**Install** (paste blocks): **[README.md](README.md)** — macOS or Windows 11.

## What this orders

**TEST (`mode: test`):** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ  
**LAUNCH (`mode: launch`):** iPhone 18 Pro Max. Fold is not listed.

In `config.yaml` change only:

1. `mode: launch`
2. Confirm `launch_at`

`--warm-only` uses a live iPhone 17 link, then **empties the bag**. That is not the order.  
On launch, the script **refuses** to bag 17 / Fold / Air.

`config.yaml` is never on GitHub. Repo: https://github.com/kolbeinng/iphone-launch-sprint — branch `cursor/dynamic-sku-select`.

---

## After setup

On the computer you will use at T-0:

1. `config.yaml` exists. CVV filled. `mode: test` for practice.
2. Apple ID → Payment → card billing already Vietnam (HCM, quận/phường).
3. `--warm-only` — 2FA in **that** Chrome if asked. Bag empty. Leave Chrome open.
4. `--now` — stop at Đặt hàng. Do not click it. Repeat until boring. Second run should click the saved shipping radio.

Saved shipping: if Apple shows a radio whose visible name + street match config → click it. Else fill **Sử dụng địa chỉ mới**.

---

## Launch day

Same computer. Same open Chrome.

- **T−10:** `--warm-only` if Chrome is not already warm. Bag empty.
- **T−0:** `--at-launch` (sleeps until `launch_at`).
- **You** click Đặt hàng.

Do not quit Chrome between warm and sprint. Keep `warm_product_url` on a live iPhone 17 link. Keep `dry_run: true`.

---

## Two delivery screens

| Page | What it is | What we do |
|------|------------|------------|
| **Fulfillment** (“Giao hàng đến”) | City so slots exist | If it already says HCM, skip the editor, click continue. |
| **Shipping** | Street | Click the matching saved radio, or fill new from config. |

---

## Night before

- [ ] `mode: launch` in `config.yaml`
- [ ] `checkout.cvv` is this machine’s card
- [ ] `dry_run: true`
- [ ] Alarm `Asia/Ho_Chi_Minh`

---

## Timer

- **US (CLICK/FILL)** — our clicks. Should stay under ~1s.
- **APPLE (WAIT/NAV/POLL)** — page load. Do not “fix” this by clicking more.

---

## If a command fails

**Mac:** the command is `python3` after `source .venv/bin/activate` (line starts with `(.venv)`).  
`git` / `python3` missing → README macOS block 1 is not done.  
`No module named 'yaml'` → you skipped `source .venv/bin/activate`.  
`command not found: python` → you typed the Windows command.

**Windows:** the command is `python` after `.\.venv\Scripts\Activate.ps1` (line starts with `(.venv)`).  
`git` / `python` missing → new PowerShell after README Windows block 1.  
`python3` is not recognized → you typed the Mac command.  
`winget` missing → Microsoft Store → App Installer.  
Clone **404** → GitHub as **kolbeinng**.  
Apple login every run → Chrome was closed. `--warm-only` again. Leave it open.  
Script sits on sign-in → finish 2FA in **that** Chrome.
