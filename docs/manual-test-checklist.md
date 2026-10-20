# Manual test checklist

Automated tests cover logic, never touch the network, and run on Linux. These checks need a real Mac, a real browser and (for some) an iPhone. Run them before a release and after changes to setup, certificates, the CSP, charts or restore.

Use a scratch clone outside `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive unless a step says otherwise. Run commands from the repo with `uv run bizviz ...`.

## 1. Setup end to end

- [ ] Clone the repo to `~/Developer/bizviz`, then run `uv sync` and `uv run bizviz setup`.
  - Expected: it asks for the Plaid `client_id` and secret (hidden input), a dashboard password twice, then shows the recovery phrase and requires its last group to continue.
  - Expected: it builds the web app, installs and starts the LaunchAgent, and prints `https://localhost:8443`, `https://<host>.local:8443`, a pointer to `docs/iphone-setup.md`, and the firewall note.
- [ ] Open `https://localhost:8443` in Safari or Chrome. Expected: no certificate warning; the login page loads; the password works.
- [ ] `~/Desktop/BizViz-CA.cer` exists.
- [ ] `uv run bizviz status`. Expected: service running, backup status, certificate and CA expiry, FileVault and Time Machine shown.
- [ ] Re-run `uv run bizviz setup`. Expected: no prompts for Plaid keys, password or recovery phrase; the service restarts; an open dashboard tab shows "BizViz was updated — Reload" and Reload works.

### Repo-location refusal

- [ ] Clone (or copy) the repo into each of `~/Documents`, `~/Desktop`, `~/Downloads` and an iCloud Drive folder, and run `uv run bizviz setup` from each.
  - Expected: setup refuses to run, changes nothing, and suggests `~/bizviz` or `~/Developer/bizviz`.
- [ ] With FileVault off or Time Machine unconfigured (if you can test this safely), expected: setup warns but continues.

## 2. Keychain prompts

- [ ] During setup, macOS asks for your password to trust the certificate. Expected: after entering it, no further certificate prompts.
- [ ] Keychain Access shows generic-password items under service `BizViz`: `db-key`, `plaid-client-id`, `plaid-secret`, `ca-private-key`.
- [ ] Restart the service (`launchctl kickstart -k gui/$(id -u)/com.bizviz.agent`). Expected: it starts; if macOS asks to allow Python access to the Keychain, choose Always Allow and confirm the service then runs unattended.
- [ ] After linking an account, `plaid-token:<item_id>` and `plaid-item-index` entries exist.

## 3. Firewall prompt

- [ ] On first start of the service, macOS asks whether Python may accept incoming network connections. Choose Allow.
- [ ] From another device on the same Wi-Fi, `https://<host>.local:8443` reaches the login page.

## 4. LaunchAgent

- [ ] `~/Library/LaunchAgents/com.bizviz.agent.plist` exists, and `uv run bizviz status` says the service is running.
- [ ] Log out and log back in (or reboot). Expected: the service is running again without any command, and `https://localhost:8443` loads.
- [ ] Find the service process (`pgrep -f "bizviz serve"`) and `kill` it. Expected: within a few seconds it is running again (new PID) and the dashboard loads.
- [ ] `uv run bizviz uninstall`. Expected: the service stops and the plist is removed; data stays; `uv run bizviz setup` brings it back with no prompts.

## 5. Plaid Link under the CSP (Sandbox first, then production)

Sandbox uses separate state, so it can run alongside your production install.

- [ ] `uv run bizviz --env sandbox setup`. Expected: it uses `~/Library/Application Support/BizViz-sandbox/`, Keychain service `BizViz-sandbox`, skips the LaunchAgent step, and asks for your **Sandbox** Plaid keys.
- [ ] `uv run bizviz --env sandbox serve` and leave it running.
- [ ] Open `https://localhost:8445` in the Mac browser and log in.
- [ ] Settings → Link bank or credit card. Expected: the counter reads "Plaid Trial connections used: 0 of 10", Plaid Link opens (no CSP errors in the browser console), and you can search for and select Plaid's sandbox institution.
- [ ] Log in with `user_good` / `pass_good`, select accounts, finish. Expected: you land on Settings, the new accounts are highlighted for review, and sync brings in transactions.
- [ ] **If Link is blocked by the CSP**, record the console error and report it. The documented fallback is adding `'unsafe-inline'` to `script-src`.
- [ ] Try the same institution again. Expected: the duplicate guard closes Link and asks for confirmation before using another slot. Cancel.
- [ ] Stop the sandbox server. Check that `uv run bizviz status` (production) shows none of the sandbox connections.
- [ ] **Production:** on `https://localhost:8443`, link one real institution the same way. Expected: Link opens and completes, including a bank that uses OAuth (it opens in a popup), the slot counter increases by one, and accounts appear for review.
- [ ] Open `https://<host>.local:8443` from the Mac. Expected: the Link buttons are hidden, and the page says to link accounts from the Mac's browser.

## 6. Fix flow

- [ ] Cause a broken connection. For a sandbox item, use Plaid's sandbox tools to put the item into login-required state; for a real item, change the bank password or revoke access at the bank.
- [ ] Trigger a sync (Sync now). Expected: the dashboard banner and Settings show the institution as needing attention.
- [ ] Settings → Fix for that institution and complete Link. Expected: status returns to OK, new accounts you shared appear for review, and **the slot counter does not change**.

## 7. iPhone certificate trust and home-screen install

Follow [iphone-setup.md](iphone-setup.md) on a real iPhone.

- [ ] AirDrop `BizViz-CA.cer`, install the profile, and enable full trust. Expected: the CA appears as "BizViz Local CA (<hostname>)" under Certificate Trust Settings.
- [ ] Safari opens `https://<host>.local:8443` with no "Not Secure" warning.
- [ ] Add to Home Screen, open it from the Home Screen, and log in there. Expected: it opens full screen and the dashboard loads.
- [ ] iOS 18 fallback (if the CA does not appear under Certificate Trust Settings): email the `.cer` to yourself, open it in Mail, and repeat install and trust. Expected: the CA appears.
- [ ] Settings → Devices on the Mac lists the iPhone. Revoke it. Expected: the app returns to the login screen and its offline copy is gone.

## 8. Offline banner with the Mac asleep

- [ ] With the Home Screen app logged in and loaded, put the Mac to sleep (or turn off its Wi-Fi) and wait a minute.
- [ ] Reopen the Home Screen app. Expected: the dashboard still renders from the offline copy; the banner reads "Showing data as of <time>. Mac unreachable since <time>"; editing controls are disabled; legend toggles, zoom and drill-down still work.
- [ ] Wake the Mac. Expected: within about a minute (or when reopening the app) the banner disappears and editing works again.

## 9. ECharts legend and zoom

- [ ] On the dashboard with at least 6 months of data, zoom the Spending chart into a 6-month window (slider, or pinch on iPhone).
- [ ] Split by Category, then click legend items to turn series off and on. Expected: **the zoom window stays** after each toggle (see [apache/echarts#20392](https://github.com/apache/echarts/issues/20392)). Repeat on the Earnings chart and the Investments chart (legend toggles accounts).
- [ ] Switch the Split by selector while zoomed. Expected: the zoom window stays.
- [ ] If the zoom resets, report it. The planned fix is to re-apply the zoom range after `legendselectchanged`.

## 10. NVDA raw-close check

Confirms that stored prices are unadjusted closes. NVDA split 10-for-1 on 2024-06-10, so the raw close on 2024-06-07 must be about 1208.88, not about 120.89.

- [ ] In a Python shell (`uv run python`), fetch NVDA for 2024-06-03 to 2024-06-14 with BizViz's price source and run its raw-close conversion (`_raw_closes`: Yahoo's adjusted `Close` multiplied by the ratio of every split dated after that day, up to the fetch date):

  ```python
  from datetime import date
  from bizviz.prices import YahooPriceSource, _raw_closes

  src = YahooPriceSource()
  closes = src.download_closes(["NVDA"], date(2024, 6, 3), date(2024, 6, 15))
  raw = dict(_raw_closes(closes["NVDA"], src.splits("NVDA"), date.today()))
  print(raw[date(2024, 6, 7)])
  ```

- [ ] Expected: the printed raw close for 2024-06-07 is approximately **1208.88**. A value near 120.89 means the split conversion is wrong.
- [ ] Optionally, with a real account that holds NVDA, check that the `prices` table agrees.

## 11. Restore onto a clean user account

- [ ] On the production install, make sure a daily backup exists in `~/Library/Application Support/BizViz/backups/`, and have the recovery phrase to hand. Copy one backup file somewhere the other user can read (for example `/Users/Shared/`).
- [ ] Create a new standard macOS user account and log in as it. Clone the repo to `~/Developer/bizviz`. Run `uv sync`.
- [ ] `uv run bizviz setup --restore <backup-file>`. Enter the recovery phrase. Expected: with a wrong phrase it aborts with no changes; with the right one, it restores, asks "Set a new dashboard password? [y/N]", then continues with Plaid credentials (if asked), certificates and the LaunchAgent.
- [ ] Open `https://localhost:8443`. Expected: all history is present, all devices are signed out, and the connections are re-adopted (a recovered-connections banner may appear), with the slot counter unchanged.
- [ ] Run `uv run bizviz setup --restore <backup-file>` again. Expected: it refuses because a database exists. Run it with `--force`. Expected: the old database, its `backups/` folder and its old key are moved aside and their names are printed.
- [ ] Delete the key (Keychain Access → `db-key` under `BizViz`) and run `uv run bizviz setup`. Expected: it aborts and points to `--recover-key` or `--restore`. Run `uv run bizviz setup --recover-key` with the recovery phrase. Expected: the dashboard opens again.
