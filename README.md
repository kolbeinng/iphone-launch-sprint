# iPhone Launch Sprint

Dry-run kit for apple.com/vn. It never clicks **Đặt hàng**. `dry_run: true` is required.

**TEST (`mode: test`):** iPhone 17 Pro Max · 256GB · Cam Vũ Trụ  
**LAUNCH (`mode: launch`):** iPhone 18 Pro Max. Fold is not listed.

`--warm-only` uses a live iPhone 17 link only to stay signed in, then **empties the bag**. That is not the order.

## You are on the right branch

Use **`cursor/dynamic-sku-select`**. Do not use `main`. Do not click **Compare & pull request**.

The long text on this GitHub page is this file (`README.md`). The Mac A–Z steps are in **[checklist.md](checklist.md)**. Click that file.

## New Mac

Follow **[checklist.md](checklist.md)** — **New Mac**. That is the only Mac path.

## New Windows 11

Follow **[checklist.md](checklist.md)** — **New Windows 11**. That is the only Windows path.

On Windows type **`python`** after `(.venv)` — not `python3`.

Do not type `python`. Type **`python3`**.  
Do not run `launch.py`. The only script is `assist.py`.  
Do not run `python3 assist.py` until Terminal shows `(.venv)`.

After step E in the checklist, every command is:

```bash
cd ~/Projects/iphone-launch-sprint
source .venv/bin/activate
python3 assist.py --warm-only
python3 assist.py --now
```

Leave Chrome open. Never ⌘Q.

## Launch night

1. In `config.yaml`: `mode: launch` and confirm `launch_at`
2. **T−10:** `source .venv/bin/activate` then `python3 assist.py --warm-only` if Chrome is not already warm
3. **T−0:** `python3 assist.py --at-launch`
4. **You** click Đặt hàng

## Config

`config.yaml` is **not** on GitHub (CVV). On each machine:

```bash
cp config.example.yaml config.yaml
```

Type `checkout.cvv` on that machine. Do not email it.

## Files that matter

- `assist.py` — the script
- `config.example.yaml` — copy this to `config.yaml`
- `checklist.md` — Mac setup + launch night
- `sprint_common.py` — shared helpers (you do not run this)

`launch.py`, `watch.py`, and `probe_checkout.py` are old. Ignore them.
