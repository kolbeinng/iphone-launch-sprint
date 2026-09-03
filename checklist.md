# Launch night checklist

Setting up a new computer? Start with **[README.md](README.md)** instead. This page is for when the script already works.

The script never types your Apple password and never clicks **Đặt hàng**. You click it.

---

## Which phone it buys

| Mode | Phone |
|---|---|
| `mode: test` | iPhone 17 Pro Max · 256GB · Cam Vũ Trụ — practice, on sale now |
| `mode: launch` | iPhone 18 Pro Max |

On launch night you change **one line** in `config.yaml`: `mode: test` → `mode: launch`. Then check that `launch_at` matches Apple's announced Vietnam time.

With `mode: launch` on, the script **refuses** to put an iPhone 17, Fold or Air in the bag. If Apple's page is not up yet, it keeps looking rather than buying the wrong phone.

`--warm-only` always uses an iPhone 17 link, even on launch night. That is only to keep you signed in, and it empties the bag afterwards. It is not your order.

---

## Days before

- [ ] Apple ID → Payment → the card's **billing address** is already Vietnam (city, quận, phường, no leftover postal code). The script never sets this one — Apple pulls it from your account when the saved card is selected. `sync_billing_address: true` will repair it mid-checkout if Apple complains, but that costs about 40 seconds at T-0, so get it right beforehand.
- [ ] Your shipping address is **saved** in your Apple account. The script clicks a saved address in about 20ms; typing a new one is much slower.
- [ ] `config.yaml` on this computer has your CVV, your address, your contact details.
- [ ] You have done `--now` a few times and it reached Đặt hàng every time.

---

## The night itself

Use the **same computer** you practised on, and keep **the same Chrome window** open the whole time.

**T−10 minutes**

```
--warm-only
```

Phone in your hand for 2FA. It must finish with the bag empty. If Chrome is already open and warm from earlier today, you can skip this.

**T−0**

```
--at-launch
```

This sleeps until `launch_at`, then goes. Do not add a safety margin after the launch time — start it early and let it wait.

**Then you click Đặt hàng.**

Put `python3 assist.py` (Mac) or `python assist.py` (Windows) in front of those commands.

Do not quit Chrome between the warm and the run. If you do, you lose the Apple checkout session and have to do 2FA again.

---

## Also check

- [ ] `dry_run: true` is still set
- [ ] Alarm set in `Asia/Ho_Chi_Minh`
- [ ] Do Not Disturb / Focus is **off**, so you see the 2FA code
- [ ] Laptop plugged in, wifi solid

If you are not signed in when the sale opens, speed does not matter. You have already lost.

---

## The two delivery screens

Apple asks about delivery twice. They are not the same question, and neither can be skipped.

| Screen | What it is asking | What the script does |
|---|---|---|
| **Giao hàng đến** | Which city, so it can show delivery slots | If it already says Hồ Chí Minh, it leaves it alone and clicks continue |
| **Chúng tôi giao hàng cho bạn đến địa chỉ nào?** | Which street address | Clicks your saved address if the name and street match your config, otherwise types a new one |

---

## Reading the timer

Every `--now` run prints a summary at the end, split in two.

- **US (CLICK/FILL)** — our own clicking. This should stay around one second. If it grows, something is wrong on our side.
- **APPLE (WAIT/NAV/POLL)** — waiting for Apple's pages. This is normally 50+ seconds and there is nothing to fix.

A long pause with a `WAIT … heartbeat` line means the script is idle and the page is loading. That is Apple, not a freeze.

---

## If a run fails

| What you see | What to do |
|---|---|
| `Invalid timezone: Asia/Ho_Chi_Minh` | Old clone. Re-run `pip install -r requirements.txt`. |
| `No module named 'yaml'` | Your line is missing `(.venv)`. Activate first. |
| Apple asks for a password every run | Chrome was closed. Run `--warm-only` again and leave it open. |
| Stuck on a sign-in page | It is waiting for you. Finish 2FA in that Chrome window. |
| `Could not empty bag` | Chrome is parked on an old checkout page. Go back to the bag tab and re-run. |
| Trade-in options stay greyed out | Apple has not committed the storage choice. Do not click the page yourself and do not reload — just run again. |

Full setup and install problems: **[README.md](README.md)**.
