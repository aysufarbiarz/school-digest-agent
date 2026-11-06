"""One-time helper: get a Google refresh token for the digest.

Run on your own computer, with the OAuth client file you downloaded from Google
Cloud Console (Desktop app type) saved next to this script as client_secret.json:

    pip install google-auth-oauthlib
    python scripts/get_refresh_token.py

A browser opens; sign in and allow access. The script prints three values to
store as GitHub secrets: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and
GOOGLE_REFRESH_TOKEN. Delete client_secret.json afterwards.
"""

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]

secret_file = Path(__file__).with_name("client_secret.json")
if not secret_file.exists():
    raise SystemExit(f"Save your OAuth client file as {secret_file} first.")

flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\nAdd these as GitHub secrets (Settings > Secrets and variables > Actions):\n")
print(f"GOOGLE_CLIENT_ID      = {creds.client_id}")
print(f"GOOGLE_CLIENT_SECRET  = {creds.client_secret}")
print(f"GOOGLE_REFRESH_TOKEN  = {creds.refresh_token}")
