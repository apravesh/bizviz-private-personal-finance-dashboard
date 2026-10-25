# BizViz

BizViz is a private, local-first personal finance dashboard for macOS. A Python service runs on your Mac, keeps everything in an encrypted database, pulls your bank, card, loan and brokerage data through **your own** Plaid developer account, and serves a simple dashboard to your Mac browser and your iPhone on home Wi-Fi. It shows where your money comes from, where it goes, what your investments are worth and your net worth. The iPhone version is installed as a home-screen web app and keeps an offline, view-only copy for when the Mac is asleep.

**Show the truth, never advise.** BizViz makes no recommendations, nudges, budgets, goals, scores or forecasts. It shows facts, and it says so visibly when data is stale, pending, estimated or incomplete. It never presents partial data as complete.

There is no server of ours. There is no account to create with BizViz, no telemetry and no subscription. Ongoing cost is $0 on Plaid's free Trial plan.

> Screenshots are not included because the app only ever shows real financial data. Run `uv run bizviz demo` to see it with synthetic data.

## Privacy model

- **On your Mac:** the database is an encrypted SQLite file (SQLCipher). Its 256-bit random key and your Plaid credentials live in the macOS Keychain, not in the repo or any config file. Plaid access tokens are stored in the encrypted database and mirrored in the Keychain.
- **On your iPhone:** the home-screen app keeps an offline copy of your dashboard data. It is protected by iOS Data Protection and your device passcode or Face ID. There is no separate app PIN, so anyone holding your unlocked phone can open the app. Revoke a lost phone under Settings → Devices on the Mac.
- **Who else sees anything:**
  - Plaid sees your transaction data in transit. That is how it provides the bank connection.
  - Yahoo Finance sees which tickers are priced (for estimated investment history), not your quantities or your identity.
  - Your browser loads Plaid's Link script from `cdn.plaid.com` when you link an account (Mac browser only).
  - Nobody else. There are no other outbound calls and no telemetry.
- **Logs** (`~/Library/Logs/BizViz/`) never contain amounts, merchant names, account names or numbers, tokens or secrets. They record only IDs, counts, durations and error codes.
- **On your home network:** HTTPS with a locally trusted certificate, a dashboard password, per-device sessions and CSRF protection.
- **Accepted trade-off:** anyone logged in as you on the Mac can reach the data. Turn on FileVault; the dashboard warns if it is off.

## Prerequisites

- macOS on Apple Silicon or Intel
- [Homebrew](https://brew.sh)
- [`uv`](https://docs.astral.sh/uv/) (`brew install uv`)
- Node 22 or newer (`brew install node`)
- A Plaid account (see below)
- An iPhone on the same Wi-Fi, if you want the phone app

**Where to clone the repo:** clone it **outside** `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive. macOS blocks background agents from reading those locations, so the service could not start. `bizviz setup` refuses to run from them. Use, for example, `~/Developer/bizviz`.

## Create a Plaid Trial account

BizViz uses your own Plaid developer account, so your bank connections belong to you.

1. Sign up at [dashboard.plaid.com](https://dashboard.plaid.com). Teams in the US or Canada created on or after 2026-04-15 get the free **Trial** plan.
2. In the dashboard, open **Developers → Keys** and copy your `client_id` and your **Production** secret. Setup will ask for them.
3. Know the limit: the Trial plan allows **10 connections, for life**.
   - A connection is one login at one institution. It can cover several accounts (checking, savings and a card at the same bank are one connection).
   - **Fix** (re-entering your login when a bank needs attention) keeps the same connection and never uses a slot.
   - **Removing a connection does not free its slot**, and losing a connection's access token wastes one. BizViz therefore mirrors every token into your Keychain.
   - BizViz shows "Plaid Trial connections used: N of 10" before you link anything, and warns you before you link an institution you already linked.
4. Plaid also gives you separate **Sandbox** keys for fake banks. They are only used with `--env sandbox` (see [Commands](#commands)); they never mix with your real data.

If you upgrade to a paid Plaid plan, Plaid's own billing applies. That is between you and Plaid.

## Install

```sh
git clone <repository-url> ~/Developer/bizviz
cd ~/Developer/bizviz
uv sync
uv run bizviz setup
```

`bizviz setup` is interactive. It:

1. checks that you are on macOS and that the repo is not in a blocked folder, and warns if FileVault is off or Time Machine is not configured;
2. asks for your Plaid `client_id` and Production secret (hidden input; stored in the Keychain);
3. asks you to choose a dashboard password (at least 10 characters), creates the encrypted database, and shows your **recovery phrase**. Store it in a password manager or write it on paper. You must retype its last group to continue. See [Backups and recovery](#backups-and-recovery);
4. creates a local certificate authority and a certificate for your Mac, and asks for your macOS password once to trust it. It also copies `BizViz-CA.cer` to your Desktop for the iPhone;
5. builds the web app (needs Node 22+) and installs and starts the background service (a per-user LaunchAgent, which also starts at login);
6. prints the addresses to use.

macOS may ask once whether Python may accept incoming network connections. Answer **Allow**, or your iPhone will not be able to reach the Mac.

When it finishes, open:

- `https://localhost:8443` on the Mac
- `https://<your-mac>.local:8443` from your iPhone (setup prints the exact name; `uv run bizviz status` shows it again)

### Link accounts (Mac browser only)

Linking happens only from the Mac's browser at `https://localhost:8443`. Log in, open **Settings**, and use one of:

- **Link bank or credit card**
- **Link brokerage or retirement account**
- **Link loan or mortgage**

After linking, mark each new account **Mine** or **Joint** (default Mine) and confirm it. The **Mine / Household** toggle at the top of the dashboard chooses what you see: Mine shows only accounts marked Mine, so a joint account does not appear in your own spending. Household shows all included accounts. Either way, money moving between your own and a joint account is a transfer, not spending or earning.

BizViz downloads up to 24 months of transactions when you link; the 5-year charts fill in over time.

## iPhone

On your home Wi-Fi, install the certificate, add BizViz to your Home Screen and log in there. Step by step: [docs/iphone-setup.md](docs/iphone-setup.md).

## Away from home

BizViz is reachable on home Wi-Fi only. If you want the phone app away from home, see the optional Tailscale guide: [docs/tailscale.md](docs/tailscale.md). Never forward the port on your router.

## How the numbers work

- Transfers between your own accounts are excluded, so moving money is not counted as earning or spending. That includes moves to and from a joint account, and transfers Plaid labels as between accounts even when the other account is not linked. Payments through apps such as Zelle or Venmo still count, since they usually go to or come from other people; mark any of them as Transfer in **Transactions** if they are your own.
- Credit card payments are not double counted: the purchases count when made, the payment to the card does not.
- Loan and mortgage payments count as spending.
- Refunds reduce spending.
- Pending charges are included and marked as pending.
- The current month is labelled "in progress".
- The dashboard has three tabs: **Earnings & spending** (savings, earnings, spending and recurring charges), **Investments** (taxable investment accounts and net worth) and **Retirement** (401(k), 403(b), IRA, Roth, HSA, 529 and other tax-advantaged accounts, grouped by tax treatment as reported by the institution, with money moved in per year). On Investments, **Gains (excl. deposits)** shows each account's change in value with deposits and withdrawals taken out, so moving money in or out does not look like growth or loss.
- **Investment history before you linked is estimated** (dashed lines), rebuilt from your current holdings, your investment transactions and free Yahoo Finance prices. It can exclude holdings that have no public prices, and says how many. From the day you link, actual balances are recorded and shown as solid lines.
- Net worth history starts when you link. It is never estimated.
- Your corrections (category, Earning/Spending/Transfer/Ignore, recurring) always win. Sync never overwrites them.

More detail for contributors: [docs/architecture.md](docs/architecture.md).

## Backups and recovery

- BizViz writes a consistent **encrypted** copy of the database every day into `~/Library/Application Support/BizViz/backups/` (the 7 newest are kept). Time Machine picks that folder up. A copy is unreadable without your key.
- Your **recovery phrase** is that key written in a form you can store offline. Together, the phrase and any backup file restore your data on any Mac:

  ```sh
  uv run bizviz setup --restore <backup-file>
  uv run bizviz setup --restore <backup-file> --force   # replace an existing database
  ```

  Restore asks for the recovery phrase and checks everything before changing anything. With `--force` it moves the old database, its `backups/` folder and its old key aside under names with a timestamp, and prints them. Restoring signs out all devices; you will log in again on each.
- **Lost the Keychain key but the database is intact?**

  ```sh
  uv run bizviz setup --recover-key
  ```

  It asks for the recovery phrase, checks that it opens your database, and stores it again. No files are touched.
- Plaid tokens are also mirrored in the Keychain, so after a restore or reinstall your connections are re-adopted automatically, without using Trial slots. A banner tells you which ones were recovered.
- Before every database upgrade, BizViz writes an extra backup first.

## Update

```sh
cd ~/Developer/bizviz
git pull
uv sync
uv run bizviz setup
```

Setup is safe to re-run: it does not ask again for Plaid keys, a password or a recovery phrase. It stops the service, rebuilds the web app, upgrades the database (with a backup first) and restarts the service. Open browsers and the iPhone app show "BizViz was updated — Reload".

## Demo mode

```sh
uv run bizviz demo
```

Serves synthetic data (about 30 months of history, several accounts, a brokerage account) at `https://localhost:8444`. Password: `demo-password`. Demo mode never calls Plaid or Yahoo Finance, never touches your Keychain, and uses a temporary data directory. Your browser will warn about the demo's throwaway certificate; that is expected.

## Commands

Run these from the repo with `uv run bizviz <command>`.

| Command | What it does |
|---|---|
| `setup` | Interactive install and update. Each step is skipped when its result already exists. |
| `setup --reconfigure-plaid` | Ask for the Plaid `client_id` and secret again. |
| `setup --restore <backup-file>` | Restore a backup using your recovery phrase. Refuses if a database already exists, unless `--force` is given. |
| `setup --restore <backup-file> --force` | Same, replacing the existing database (old files are moved aside, not deleted). |
| `setup --recover-key` | Re-store the database key from your recovery phrase when the Keychain entry is missing. |
| `serve` | Run the service in the foreground. This is what the LaunchAgent runs. |
| `demo [--port 8444]` | Serve synthetic demo data. |
| `status` | Show whether the service is running, each connection's status and last sync, last backup, certificate and CA expiry, hostname mismatch, FileVault and Time Machine. |
| `rotate-cert --hostname <name>` | Pin a new Mac hostname (the old one stays valid). Re-add the app on the iPhone afterwards. |
| `rotate-cert --add-hostname <name>` | Add another hostname to the certificate (for example a Tailscale name). |
| `rotate-cert --ca` | Replace the certificate authority. You must trust it again on every device. |
| `reset-password` | Set a new dashboard password from the Mac. Signs out all devices. |
| `uninstall` | Remove the background service; keeps your data. |
| `uninstall --delete-data` | Also delete the data directory (database, backups, certificates, config), the stored database key, Plaid credentials and CA key, and the trusted CA. Asks you to type a confirmation. Keeps the Plaid token mirror so a reinstall can re-adopt your connections. |
| `uninstall --delete-tokens` | With `--delete-data`, also delete the Plaid token mirror after a second typed confirmation. Your Trial slots cannot be recovered after this. |
| `--env sandbox` (before any command) | Use Plaid Sandbox with completely separate state. |

`--env sandbox` uses its own data directory (`~/Library/Application Support/BizViz-sandbox/`), its own Keychain entries (service `BizViz-sandbox`) and port 8445, and installs no background service. Use `uv run bizviz --env sandbox setup`, then `uv run bizviz --env sandbox serve`, and use Plaid's test login `user_good` / `pass_good`. Sandbox connections never count toward your 10 production slots.

## Troubleshooting

- **macOS asks for Keychain access ("Always Allow") after a Python upgrade.** The background service is now a different Python binary. When macOS asks, enter your login password and choose **Always Allow**, so the background service can read its secrets without further prompts.
- **Firewall prompt.** Choose **Allow** when macOS asks whether Python may accept incoming connections. If you clicked Deny, allow it under System Settings → Network → Firewall, then restart the service with `uv run bizviz setup`.
- **Firefox shows a certificate warning.** Firefox uses its own certificate store. Import `ca.pem` from `~/Library/Application Support/BizViz/certs/` into Firefox (Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import), or use Safari or Chrome.
- **iPhone says "Not Secure".** Redo the trust step: Settings → General → About → Certificate Trust Settings. See [docs/iphone-setup.md](docs/iphone-setup.md).
- **Hostname mismatch warning.** The Mac's local hostname changed (macOS sometimes renames to `name-2`). Either rename the Mac back in System Settings → General → Sharing → Local hostname, or run `uv run bizviz rotate-cert --hostname <new>` and re-add the app on the iPhone.
- **A bank shows "needs attention".** Open Settings and use **Fix** for that institution. Fix does not use a Trial slot.
- **"Plaid Trial connections used: 10 of 10".** You have used all your Trial connections; linking is disabled. Use Fix for broken connections. Removing a connection does not free a slot.
- **Setup says the repo is in a blocked folder.** Move the clone to `~/Developer/bizviz` (outside Documents, Desktop, Downloads and iCloud Drive) and run setup again.
- **Logs** are in `~/Library/Logs/BizViz/`.

## Development

- `./scripts/check.sh` runs the full quality gate: ruff (lint and format check), mypy, pytest, then in `web/` svelte-check, vitest and the build.
- Tests never call Plaid or Yahoo Finance; Plaid is replaced by a scripted fake and `yfinance` is mocked.
- `BIZVIZ_PLATFORM=fake` swaps the macOS Keychain, service manager and system checks for in-memory fakes, so everything runs on Linux.
- `Dockerfile.dev` and `agent_env.yml` define the development container used for contributors and coding agents.
- Manual checks that need a real Mac: [docs/manual-test-checklist.md](docs/manual-test-checklist.md). Design overview: [docs/architecture.md](docs/architecture.md).

## License

MIT. See [LICENSE](LICENSE).
