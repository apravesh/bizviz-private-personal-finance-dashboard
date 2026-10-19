# iPhone setup

BizViz runs on your Mac and serves its dashboard over HTTPS on your home Wi-Fi. For your iPhone to trust that connection, you install BizViz's local certificate once, then add the dashboard to your Home Screen.

You need `bizviz setup` to have finished on the Mac (see the [README](../README.md)), and the iPhone on the same Wi-Fi network as the Mac.

## Steps

1. **AirDrop the certificate.** Setup copied `BizViz-CA.cer` to the Mac's Desktop. AirDrop it to your iPhone.
2. **Install the profile.** On the iPhone, open Settings. Tap **Profile Downloaded**, then **Install**, and enter your passcode.
3. **Enable full trust.** Go to Settings → General → About → Certificate Trust Settings. Under "Enable full trust for root certificates", switch on **BizViz Local CA (your Mac's hostname)**.
4. **Open the dashboard in Safari.** Go to `https://<your-mac>.local:8443`. Setup prints the exact address at the end; `uv run bizviz status` on the Mac shows it again. It should load without a "Not Secure" warning.
5. **Add to Home Screen.** Tap Share, then **Add to Home Screen**.
6. **Open BizViz from the Home Screen and log in there.** A Home Screen app has its own storage, separate from Safari. Log in inside the Home Screen app, not only in Safari. Only the Home Screen app keeps its offline copy beyond 7 days; Safari tabs can have their website data cleared after a week of not being used ([WebKit](https://webkit.org/tracking-prevention/)).

## If the certificate does not appear under Certificate Trust Settings

Some people have reported that on iOS 18 an installed certificate does not show up in Certificate Trust Settings. If that happens:

1. Email the `BizViz-CA.cer` file to yourself.
2. On the iPhone, open the email in the **Mail** app and tap the attachment.
3. Repeat steps 2 and 3 above (install the profile, then enable full trust).

## Offline use

The Home Screen app keeps a copy of your dashboard data. When the Mac is asleep or unreachable, the app still opens, and charts, zoom and drill-down still work from that copy. A banner shows how current the data is ("Showing data as of ... Mac unreachable since ...").

Offline is **view-only**. Editing categories, rules, accounts and linking all need the Mac reachable, and linking accounts only works from the Mac's own browser. When the Mac is awake and on the same Wi-Fi again, the banner disappears and the data refreshes.

## Other things to know

- **Updates.** After you update BizViz on the Mac, the Home Screen app shows "BizViz was updated — Reload".
- **Certificate renewal** happens automatically on the Mac and needs nothing on the phone.
- **Hostname changes.** If the Mac's local hostname changes, the address changes and you need to add the app again. See Troubleshooting in the [README](../README.md#troubleshooting).
- **Lost or replaced phone.** On the Mac, open Settings → Devices and revoke that device. It is signed out immediately, and the offline copy is deleted from the app the next time it connects.
- **Away from home.** See [tailscale.md](tailscale.md).
