#!/usr/bin/env python
# pylint: disable=import-error

"""
Script name: get_ticket_info.py
This script retrieves details of a specific JIRA issue using the Atlassian REST API.

Usage: get_ticket_info.py issue_key [OPTIONS]

Arguments:
  issue_key  The key of the issue to retrieve.

Options:
  -o, --output TEXT     Path to file to write output to. If not specified, the output filename is
                        {issue_key}.json
  -h, --help            Show this message and exit.
"""
import argparse
import json
import os

import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def main():
    """
    Main function for get_ticket_info.py
    """
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Retrieve details of a JIRA issue")
    parser.add_argument("issue_key", help="The key of the issue to retrieve")
    parser.add_argument(
        "-o",
        "--output",
        help="Path to file to write output to. If not specified, the output filename is {issue_key}.json",
    )
    args = parser.parse_args()

    # Check if output path was provided, otherwise set to {issue_key}.json
    if args.output is None:
        args.output = f"{args.issue_key}.json"
    else:
        # Remove any existing extension and append .json
        output_filename, _ = os.path.splitext(args.output)
        args.output = f"{output_filename}.json"

    # Set up authentication
    api_token = os.getenv("ATLASSIAN_API_TOKEN")
    api_email = os.getenv("ATLASSIAN_EMAIL")
    if api_token is None or api_email is None:
        print("Error: ATLASSIAN_API_TOKEN and ATLASSIAN_EMAIL must be set in the environment.")
        return
    auth = (api_email, api_token)

    # Set up JIRA API URL and issue key
    jira_url = f"{api_url}/rest/api/2/issue/{args.issue_key}"

    # Send GET request to JIRA API
    try:
        response = requests.get(jira_url, auth=auth, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Error: Unable to retrieve issue details. {error}")
        return

    # Parse JSON response
    try:
        issue_details = response.json()
    except ValueError as error:
        print(f"Error: Unable to parse response as JSON. {error}")
        return

    # Write output to file
    try:
        with open(args.output, "w", encoding="utf-8") as file:
            json.dump(issue_details, file, indent=4)
    except IOError as error:
        print(f"Error: Unable to write output to file. {error}")
        return

    print(f"Successfully wrote output to {args.output}")


if __name__ == "__main__":
    main()
