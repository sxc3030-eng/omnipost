# Pinterest Setup — OmniPost (Phase 5)

Setup guide for connecting Pinterest publishing in OmniPost. Pinterest pins
require an **image** (text-only pins are not supported).

## 1. Create a Pinterest developer app

1. Go to https://developers.pinterest.com/
2. Sign in with the Pinterest account that will own GIa Underground content.
3. Click **My apps** -> **Create app**.
4. Fill in:
   - **App name**: `GIa Underground`
   - **App description**: `Cross-posting from genia.social to Pinterest`
   - **Website URL**: `https://genia.social`
   - **Redirect URI**: `https://genia.social/oauth/pinterest/callback`
     (or `http://localhost:8861/oauth/pinterest/callback` for local testing)
5. Request scopes: `boards:read`, `pins:read`, `pins:write`, `user_accounts:read`.
6. Once approved you get an **App ID** and **App secret**.
7. Copy them into `omnipost_settings.json`:
   ```json
   "oauth": {
     "pinterest": {
       "app_id": "YOUR_APP_ID",
       "app_secret": "YOUR_APP_SECRET",
       ...
     }
   }
   ```

## 2. Create the target board

1. Go to https://www.pinterest.com/ and log in.
2. Create a new board called **GIa Underground** (Public, no secret).
3. Pin one cover image so the board exists.

## 3. Get the board ID

After OAuth (step 4) you can list your boards:

```bash
curl -X GET "https://api.pinterest.com/v5/boards" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

Find the board with `"name": "GIa Underground"` and copy its `"id"` (a long
numeric string). Paste it into `omnipost_settings.json`:

```json
"oauth": {
  "pinterest": {
    "pinterest_board_id": "1234567890123456789",
    ...
  }
}
```

## 4. OAuth flow (get access_token)

Pinterest uses OAuth 2.0 with PKCE. Quick manual flow:

1. Open in browser:
   ```
   https://www.pinterest.com/oauth/?client_id=YOUR_APP_ID
     &redirect_uri=https://genia.social/oauth/pinterest/callback
     &response_type=code
     &scope=boards:read,pins:read,pins:write,user_accounts:read
     &state=random123
   ```
2. Approve. Pinterest redirects with `?code=AUTH_CODE`.
3. Exchange code for tokens:
   ```bash
   curl -X POST "https://api.pinterest.com/v5/oauth/token" \
     -u "APP_ID:APP_SECRET" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "grant_type=authorization_code&code=AUTH_CODE&redirect_uri=https://genia.social/oauth/pinterest/callback"
   ```
4. Copy `access_token` and `refresh_token` from the response into
   `omnipost_settings.json` under `oauth.pinterest`.

`access_token` lives ~30 days. Use `refresh_token` to renew via
`grant_type=refresh_token`.

## 5. Test a pin

OmniPost dashboard -> create a post with an image -> select Pinterest -> Publish.
You should see it on https://www.pinterest.com/YOUR_USERNAME/gia-underground/.

## Troubleshooting

- **`HTTP 401`** -> access_token expired, refresh it.
- **`HTTP 403`** -> missing `pins:write` scope, redo OAuth.
- **`Pinterest pins require an image`** -> attach a photo before publishing.
- **`board_id not configured`** -> step 3 above.

## API reference

- Pins: https://developers.pinterest.com/docs/api/v5/pins-create
- Boards: https://developers.pinterest.com/docs/api/v5/boards-list
- OAuth: https://developers.pinterest.com/docs/getting-started/connect-app/
