#!/usr/bin/env python
# pylint: disable=import-error

"""
Script name: get_zoom_user.py
This script retrieves details of a Zoom user using the Zoom REST API.

Usage: get_ticket_info.py user_email [OPTIONS]

Arguments:
  user_email  The email of the user to retrieve.

Options:
  -h, --help            Show this message and exit.
"""

import argparse
import base64
import os

import requests
from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env.

# Parse script arguments
parser = argparse.ArgumentParser(description="Search for a user in Zoom.")
parser.add_argument("username", type=str, help="The username to search for")
args = parser.parse_args()

client_id = os.getenv("ZOOM_S2S_CLIENT_ID")
client_secret = os.getenv("ZOOM_S2S_CLIENT_SECRET")
account_id = os.getenv("ZOOM_S2S_ACCOUNT_ID")

# Base64 encode client id and secret
credentials = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("utf-8")

# Request access token
headers = {"Authorization": f"Basic {credentials}"}
data = {"grant_type": "account_credentials", "account_id": account_id}
response = requests.post("https://zoom.us/oauth/token", headers=headers, data=data, timeout=10)
access_token = response.json()["access_token"]

# Use access token to authenticate API calls
headers = {"Authorization": "Bearer " + access_token}

# Call the Zoom API to get user information
response = requests.get(f"https://api.zoom.us/v2/users/{args.username}", headers=headers, timeout=10)

# Check if user exists
if response.status_code == 200:
    user = response.json()
    print(f"Found user: {user}")
else:
    print(f"User not found: {args.username}")
