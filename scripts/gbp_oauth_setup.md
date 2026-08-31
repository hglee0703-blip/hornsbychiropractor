# Google Business Profile OAuth Setup

Since the GBP API requires **user OAuth 2.0** (not a service account), you must
complete this one-time authorization flow manually. The refresh token you obtain
here is what the GitHub Action uses to post automatically on your behalf.

## Prerequisites

1. You **own** (or manage) the `hornsbychiropractor.com` Google Business Profile.
2. You have access to the Google account that owns that Business Profile.

## Step 1 — Create a Google Cloud Project & OAuth Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `hornsbychiropractor-gbp`).
3. Enable the **Google Business Profile API** for that project:
   - Navigation menu → APIs & Services → Library
   - Search "Google Business Profile API" → Enable
4. Create **OAuth 2.0 Client ID** credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth client ID
   - Application type: **Web application**
   - Name: `GBP GitHub Action`
   - **Authorized redirect URIs**:
     ```
     http://localhost:8080/callback
     https://developers.google.com/oauthplayground
     ```
   - Click **Create**.
5. Download the JSON credentials file, then copy these values:
   - `client_id`
   - `client_secret`

## Step 2 — Generate a Refresh Token

Use Google's OAuth 2.0 Playground (the easiest path for a one-time token):

1. Open [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground).
2. Click the ⚙️ gear icon (top-right) → **Select & authorize APIs**:
   - **Scopes**: `https://www.googleapis.com/auth/business.manage`
   - Check **Access type**: `offline` (so you get a refresh token)
   - Check **Force approval prompt**: `on`
   - **Authorization method**: `Authorization header` (or `Body`, either works)
   - Click **Save**
3. In the left panel, paste your `client_id` and `client_secret` from Step 1,
   then click **Authorize APIs** (you'll sign in with the Google account that
   owns the Business Profile and grant consent).
4. After consent, click **Use refresh token** (or **Exchange authorization code
   for tokens**).
5. You will see:
   - `access_token`
   - `refresh_token`  ← **this is the long-lived token**
6. Click **Download JSON** to save a backup, then copy the `refresh_token` value.

> ⚠️ Keep the refresh token secure — anyone with it can post to your Google Business
> Profile. If it's ever compromised, revoke it at
> https://myaccount.google.com/permissions and re-run this flow.

## Step 3 — Find Your Location ID

You need the GBP location resource name (e.g. `accounts/123456789/locations/987654321`):

1. In the OAuth Playground, click **Authorize APIs** again (your refresh token
   will still be valid).
2. Click **Enter your own scopes** and add
   `https://www.googleapis.com/auth/business.manage`.
3. In the request URL field, make a **GET** request to:
   ```
   https://mybusiness.googleapis.com/v4/accounts
   ```
   → This lists all Business Profile accounts you manage. Find your clinic's
   account ID.
4. Then request:
   ```
   https://mybusiness.googleapis.com/v4/accounts/{accountId}/locations
   ```
   → Find your clinic's location ID (e.g. `locations/987654321`).

Alternatively, the simplest method:
- Visit your Google Business Profile dashboard at
  https://support.google.com/business/
- Click **Manage now** → the URL will contain something like:
  `https://support.google.com/business/getstarted?accountId=123456789&lid=987654321`
- Your location resource name is: `accounts/123456789/locations/987654321`

## Step 4 — Add Secrets to Your GitHub Repository

In your GitHub repo (`hglee0703-blip/hornsbychiropractor`):

1. Go to **Settings → Secrets and variables → Actions → New repository secret**.

| Secret Name            | Value                                  |
|------------------------|----------------------------------------|
| `GOOGLE_CLIENT_ID`     | Your OAuth client_id from Step 1       |
| `GOOGLE_CLIENT_SECRET` | Your OAuth client_secret from Step 1   |
| `GBP_REFRESH_TOKEN`    | The refresh token from Step 2          |
| `GBP_LOCATION_ID`      | `accounts/{accountId}/locations/{locationId}` or just the location ID |
| `GBP_ACCOUNT_ID`       | Account ID (only if GBP_LOCATION_ID is a bare location id) |

The `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets already exist from
the daily-blog workflow.

## Step 5 — Verify

Run the workflow manually once:

1. GitHub repo → **Actions** tab → **GBP auto-post** workflow
2. Click **Run workflow** → **Run workflow**
3. Check the **gbp-posts.json** log file in your repo (it will be committed by
   the workflow).

On the first run, a new Google refresh token will exchange for an access token
automatically — no human interaction needed afterward (until the user revokes
consent from https://myaccount.google.com/permissions).