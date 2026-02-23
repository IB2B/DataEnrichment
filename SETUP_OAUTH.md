# Google OAuth Setup Guide

Follow these steps to enable the "Connect Google Sheets" button in the Intelligent Enrichment app.

## 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your existing project (or create a new one)

## 2. Enable APIs

1. Go to **APIs & Services > Library**
2. Search for and enable **Google Sheets API**
3. Search for and enable **Google Drive API**

## 3. Configure OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Choose **External** (or **Internal** if using Google Workspace)
3. Fill in the required fields:
   - **App name**: Intelligent Enrichment
   - **User support email**: your email
   - **Developer contact**: your email
4. Add scopes:
   - `https://www.googleapis.com/auth/spreadsheets`
   - `https://www.googleapis.com/auth/drive.readonly`
5. Add test users (if External):
   - Add the Google accounts of your 4 team members
6. Save

## 4. Create OAuth Client ID

1. Go to **APIs & Services > Credentials**
2. Click **+ CREATE CREDENTIALS > OAuth client ID**
3. Application type: **Web application**
4. Name: `Intelligent Enrichment Web`
5. Authorized redirect URIs — add:
   ```
   https://YOUR_DOMAIN/auth/google/callback
   ```
   Replace `YOUR_DOMAIN` with your actual domain (e.g., `enrichment.yourdomain.com`).

   If testing locally, also add:
   ```
   http://localhost:8000/auth/google/callback
   ```
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

## 5. Set Environment Variables

Add these to your server environment. Choose one method:

### Option A: Environment variables (recommended for production)

Edit the systemd service file:

```bash
sudo nano /etc/systemd/system/enrichment.service
```

Add under `[Service]`:

```ini
Environment="GOOGLE_CLIENT_ID=your-client-id-here.apps.googleusercontent.com"
Environment="GOOGLE_CLIENT_SECRET=your-client-secret-here"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart enrichment
```

### Option B: .env file

Create `/opt/enrichment/.env`:

```
GOOGLE_CLIENT_ID=your-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret-here
```

If using a `.env` file, make sure your systemd service or process loads it (e.g., via `EnvironmentFile=/opt/enrichment/.env` in the service file).

## 6. Restart the Service

```bash
sudo systemctl restart enrichment
```

## 7. Connect Your Google Account

1. Open the app in your browser
2. Go to the Dashboard
3. Click **Connect Google Sheets**
4. Sign in with your Google account and authorize the app
5. You should see a green "Connected" badge with your email
6. Select a sheet from the dropdown and start enriching

## Troubleshooting

- **"Google OAuth not configured"**: The GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET environment variables are not set. Check your systemd service config.
- **"redirect_uri_mismatch"**: The callback URL doesn't match what's configured in Google Cloud Console. Make sure `https://YOUR_DOMAIN/auth/google/callback` is listed as an authorized redirect URI.
- **"access_denied"**: If using External consent screen, make sure your Google account is added as a test user.
- **Token expired / "Reconnect" shown**: The refresh token may have been revoked. Click "Disconnect" then "Connect Google Sheets" again.
- **Sheets not loading**: Ensure both Google Sheets API and Google Drive API are enabled in the Cloud Console.

## Notes

- OAuth tokens are stored in the SQLite database (shared across all app users)
- The app automatically refreshes expired access tokens using the refresh token
- If OAuth is unavailable, the enrichment engine falls back to the service account (enrichmentdata.json)
- The `prompt=consent` parameter ensures Google always returns a refresh token
