#!/usr/bin/env python
# pylint: disable=import-error

"""Refresh the Zoom access token."""

import os

import requests
from dotenv import load_dotenv, set_key

# Load environment variables from .env file
load_dotenv()

# Get the access token, refresh token, client ID, and client secret from the environment variables
access_token = os.getenv("ZOOM_ACCESS_TOKEN")
refresh_token = os.getenv("ZOOM_REFRESH_TOKEN")
client_id = os.getenv("ZOOM_CLIENT_ID")
client_secret = os.getenv("ZOOM_CLIENT_SECRET")


def refresh_access_tokens():
    """
    Refresh the Zoom access token.
    """
    # Define the endpoint for token refresh
    refresh_url = (
        f"https://zoom.us/oauth/token?grant_type=refresh_token"
        f"&refresh_token={refresh_token}"
        f"&client_id={client_id}"
        f"&client_secret={client_secret}"
    )

    # Make a POST request to refresh the access token
    response = requests.post(refresh_url, timeout=10)

    # Check if the request was successful
    if response.status_code == 200:
        # Update the access token with the new value
        new_access_token = response.json()["access_token"]
        new_refresh_token = response.json()["refresh_token"]
        set_key(".env", "ZOOM_ACCESS_TOKEN", new_access_token)
        set_key(".env", "ZOOM_REFRESH_TOKEN", new_refresh_token)
        print("Access token refreshed successfully!")
        return new_access_token, new_refresh_token
    print("Failed to refresh access token. Status code:", response.status_code)
    print("Response:", response.json())
    return None, None


if __name__ == "__main__":
    refresh_access_tokens()
