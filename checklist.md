# Don't forget

Scripts never enter your Apple password and never click **Đặt hàng**.

Copy `config.yaml` between computers for **name, street, email, phone, product**.
**CVV is the one field that changes** (different card / different machine). Edit `checkout.cvv` on that computer. Do not commit it; do not email it.

The script always applies `shipping_address` from config:

- If Apple already shows a saved radio whose **visible name + street** match → click it (~20ms).
- If not → fill **Sử dụng địa chỉ mới** from the same config (slower, still the right address).

Same Apple ID on a new computer: after login, the saved address usually appears by itself. First `--now` should click it. If Apple has no saved card yet, that first run creates it.

---

## Per-computer setup (once, days before — not at 18:50)

Do this on **the computer you will use at T-0**. Practice on another machine does not count.

3. `python assist.py --setup-login`  
   Sign in + 2FA in the **script’s Chrome** (not everyday Chrome). Leave that window open. Never ⌘Q / quit Chrome after this.

4. `python assist.py --warm-only`  
   Second login: **checkout** SSO. 2FA again if asked. Adds a practice iPhone, reaches checkout, **empties the bag**. Leave Chrome open.

5. `python assist.py --now`  
   Full dry-run → stop at Đặt hàng (not clicked). Repeat until it is boring (2–3 clean runs). Second run should select the saved shipping radio, not type a new address.

Also, once in a normal browser: Apple ID → Payment → card **billing** address already correct (Vietnam quận/phường, no junk postal). Do **not** leave that for T-0 (popup ~40s).

---

## Launch day (same computer, same open Chrome)

- **T−10:** `python assist.py --warm-only` if Chrome isn’t already warm. 2FA phone in hand. Bag must end empty.
- **T−0:** `python assist.py --at-launch` (sleeps until `launch_at`). Do not add extra seconds after 19:00.
- **You** click Đặt hàng.

`--now` = practice. `--at-launch` = real timer. Don’t mix them on the night. Don’t quit Chrome between warm and sprint.

---

## Two different “where do we deliver?” screens

Apple will not let you skip these. They are not the same thing.

| Page | What it is | Do we need it? |
|------|------------|----------------|
| **Fulfillment** (“Giao hàng đến”) | City so delivery slots exist | **The page, not the editor.** If it already shows HCM, we skip re-selecting and click **Tiếp tục đến Địa Chỉ Giao Hàng**. The ~10s after Continue is Apple loading Shipping. |
| **Shipping** — “Chúng tôi giao hàng cho bạn đến địa chỉ nào?” | Pick the **street** (saved radio vs new) | **Yes.** This is where the right house is chosen. Saved match ~20ms. |

We only open the city/quận editor if the label is **not** already HCM (wrong city). Apple’s label usually never shows Bình Thạnh even after an edit — so redoing it every time was wasted clicks.

---

## Night-before extras

- [ ] `config.yaml`: product prefs / family_match ready for launch (not leftover practice “17 Pro”)
- [ ] `checkout.cvv` is the card on **this** machine
- [ ] `dry_run: true` until you are ready to click Đặt hàng yourself
- [ ] Alarm `Asia/Ho_Chi_Minh`; notifications on; Focus / DND off

If you are not signed in, speed does not matter — you already lost.

---

## Timer (read WAIT vs CLICK)

End of each `--now` log:

- **US (CLICK/FILL)** — our clicks. Should stay small (under ~1s except location ~0.6s).
- **APPLE (WAIT/NAV/POLL)** — page load / checkout hop. ~10s each is Apple. Do not “fix” this by clicking more.

If a long pause has a `WAIT  Apple hop … heartbeat` line, we are idle. The page is loading.
