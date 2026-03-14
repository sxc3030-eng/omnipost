# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 1.0.x | ✅ Yes |

## Reporting a Vulnerability

If you find a security vulnerability, please **do not** open a public issue.

Email: open an issue marked **[SECURITY]** with limited details, and we will contact you privately.

## Notes

- OmniPost runs **100% locally** — no data is sent to external servers unless you configure the Claude API key
- API keys are stored in `omnipost_settings.json` on your local machine
- The WebSocket server only binds to `localhost` — not accessible from outside your machine
- HTTPS is supported for the auth server with a self-signed certificate
