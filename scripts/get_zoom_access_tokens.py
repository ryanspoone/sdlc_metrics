#!/usr/bin/env python
# pylint: disable=import-error

"""Exchange authorization token for access and refresh tokens."""

import argparse
import base64
import os
import urllib.parse

import requests

# Your client id, client secret and redirect uri
client_id = os.getenv("ZOOM_CLIENT_ID")
client_secret = os.getenv("ZOOM_CLIENT_SECRET")
REDIRECT_URI = "https://localhost/"

# Create the parser
parser = argparse.ArgumentParser(description="Exchange an authorization code for an access and refresh token")

# Add the arguments
parser.add_argument("redirect_url", type=str, help="The redirect URL with the authorization code")

# Parse the arguments
ARGS = parser.parse_args()

# Extract the code from the redirect URL
parsed_url = urllib.parse.urlparse(ARGS.redirect_url)
params = urllib.parse.parse_qs(parsed_url.query)
authorization_code = params["code"][0]

# Zoom OAuth token endpoint
URL = "https://zoom.us/oauth/token"

# Prepare the headers
headers = {
    "Authorization": "Basic " + base64.b64encode((client_id + ":" + client_secret).encode()).decode(),
}

# Prepare the data
data = {"grant_type": "authorization_code", "code": authorization_code, "redirect_uri": REDIRECT_URI}

# Send the POST request
response = requests.post(URL, headers=headers, data=data, timeout=10)

# If the request was successful, print the access and refresh tokens
if response.status_code == 200:
    json_response = response.json()
    print("Access token:", json_response["access_token"])
    print("Refresh token:", json_response["refresh_token"])
else:
    print("Error:", response.text)
