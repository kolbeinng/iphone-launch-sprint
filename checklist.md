# Night-before prep checklist (manual)

Do this yourself. The scripts will **remind** you; they will **not** enter your password or pay for you.

## 1) Same browser, really signed in

Apple login is **per browser**. Being signed into Chrome does nothing if `launch.py` opens Safari.

- [ ] Pick one browser and set it in `config.yaml` (`browser: "Safari"` or `"Google Chrome"`)
- [ ] Run: `python launch.py --check-session`
- [ ] On the page that opens: if you see **Đăng nhập / Sign In**, sign in now (2FA nearby)
- [ ] Re-run `--check-session` until the account page loads **without** asking to sign in
- [ ] Optional stronger path: `python assist.py --setup-login` (waits for Apple ID + 2FA in its own Chromium profile), then `python assist.py`

Homepage alone is a weak check. Use the **account** page.

## 2) Shipping & payment

- [ ] Vietnam shipping address saved
- [ ] Payment method saved / Apple Pay ready
- [ ] You can reach checkout on a normal product and see ship/pay prefilled — then **cancel**

## 3) After the deep link opens (still manual / assist)

Deep link skips **model / storage / color only**. You still must:

1. **Apple Trade In** → **Không đổi cũ lấy mới**
2. **AppleCare** → **Không có bảo hành AppleCare+**
3. **Thêm vào giỏ hàng** → **Xem Giỏ Hàng** → **Thanh Toán** (checkout form)
4. **You** place the order — scripts stop before that

Or run `python assist.py` through step 3, then you buy.

## 4) Launch readiness

- [ ] `config.yaml` has the exact product deep link
- [ ] Alarm set for Vietnam launch time (`Asia/Ho_Chi_Minh`)
- [ ] Notifications on; Focus / DND off
- [ ] `dry_run: true` for practice — do not Place Order

If you are not signed in, trade-in/AppleCare speed does not matter — you already lost.
