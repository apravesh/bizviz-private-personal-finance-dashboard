# Using BizViz away from home (optional)

By default BizViz is reachable only on your home Wi-Fi. BizViz has no built-in remote access. If you want to open it from your iPhone elsewhere, you can use [Tailscale](https://tailscale.com), a separate app that creates a private network (a "tailnet") between your own devices.

This is optional, and BizViz does not depend on it.

## Setup

1. Install Tailscale on the Mac and on the iPhone, and sign in to the **same tailnet** on both.
2. Find the Mac's MagicDNS name in Tailscale. It looks like `<mac>.<tailnet>.ts.net`.
3. On the Mac, add that name to BizViz's certificate:

   ```sh
   uv run bizviz rotate-cert --add-hostname <mac>.<tailnet>.ts.net
   ```

   This reissues the certificate with the extra name and restarts the service. The iPhone's existing trust of the BizViz CA still applies, so you do not need to install anything again.
4. On the iPhone, open `https://<mac>.<tailnet>.ts.net:8443` in Safari.
5. Share → Add to Home Screen.

## Things to know

- **It is a different web origin.** `https://<mac>.<tailnet>.ts.net:8443` and `https://<your-mac>.local:8443` are different websites to the iPhone. The Tailscale version is a **separate Home Screen app** with its own login and its own offline copy.
- **Privacy.** Tailscale's coordination servers help your devices find each other, but traffic between them is end-to-end encrypted (WireGuard). Your financial data does not pass through Tailscale's servers in readable form.
- **Never port-forward 8443 on your router.** BizViz is built for a private network, not the open internet.
- The Mac must be awake for the dashboard to load. When it is not, the Home Screen app shows its offline copy.
