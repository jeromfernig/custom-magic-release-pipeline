from __future__ import annotations

import argparse
from pathlib import Path

from mtg_pipeline.config import load_config
from mtg_pipeline.sources.google_sheets import allow_localhost_oauth_transport


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local Google Sheets OAuth token for the release pipeline.")
    parser.add_argument("--config", default="config.example.yaml", help="Path to the pipeline YAML config.")
    parser.add_argument("--credentials", help="Override credentials.json path from config.")
    parser.add_argument("--token", help="Override token.json path from config.")
    parser.add_argument("--no-browser", action="store_true", help="Print the authorization URL instead of opening a browser.")
    parser.add_argument("--reset-token", action="store_true", help="Delete the existing token file before authenticating.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    credentials_path = Path(args.credentials) if args.credentials else config.source.credentials_path
    token_path = Path(args.token) if args.token else config.source.token_path

    if not credentials_path.exists():
        raise SystemExit(f"Google OAuth credentials file not found: {credentials_path}")
    if args.reset_token and token_path.exists():
        token_path.unlink()
        print(f"Deleted existing Google OAuth token: {token_path}")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit("google-auth-oauthlib is not installed. Run: pip install -e .") from exc

    allow_localhost_oauth_transport()
    print("Requesting Google OAuth scopes:")
    for scope in config.source.scopes:
        print(f"- {scope}")
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), config.source.scopes)
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=not args.no_browser,
        authorization_prompt_message="Open this URL to authorize Google Sheets access:\n{url}\n",
        success_message="Google authentication completed. You can close this tab and return to Streamlit.",
        access_type="offline",
        prompt="consent",
    )
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Wrote Google OAuth token: {token_path}")


if __name__ == "__main__":
    main()
