#!/usr/bin/env python
# pylint: disable=import-error,too-many-locals

"""
Automatically update the Jira sheets in the Google Sheets document.
"""

import argparse
import os
import urllib.parse
from datetime import datetime

import gspread
import pandas as pd
import requests
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from utilities import backoff, get_month_range, get_previous_month

load_dotenv()  # take environment variables from .env.

SHEET_NAME = "Cycle Time"
URL = os.getenv("ATLASSIAN_URL")

# Configurations for different issue types and their respective sheets in Google Sheets.
SYNC_CONFIG = [
    {
        "issue_types": ["bug"],
        "row_name": "Bug Resolution Time",
        "start_stage": "Triage",
        "end_stage": "Merged",
        "labels": [],
    },
    {
        "issue_types": ["bug"],
        "row_name": "Support Bug Resolution Time",
        "start_stage": "Triage",
        "end_stage": "Merged",
        "labels": ["jira_escalated", "support"],
    },
    {
        "issue_types": ["story", "epic"],
        "row_name": "Design Time (Stories, Epics)",
        "start_stage": "Open",
        "end_stage": "In Progress",
        "labels": [],
    },
    {
        "issue_types": ["story", "epic"],
        "row_name": "Completion Time (Stories, Epics)",
        "start_stage": "In Progress",
        "end_stage": "Merged",
        "labels": [],
    },
    {
        "issue_types": ["story", "epic", "bug"],
        "row_name": "Verification Time",
        "start_stage": "Merged",
        "end_stage": "Closed",
        "labels": [],
    },
]

STAGE_ORDER = [
    "Design Complete",  # migrated from another project
    "Backlog",
    "Triage",
    "Waiting for support",
    "Open",
    "Ready for Grooming",  # old
    "Groomed",  # old
    "In Progress",
    "In Review",
    "Review",  # migrated from another project
    "Merged",
    "Deploy",  # old
    "In Test",  # shouldn't be used by us
    "Awaiting Approval",
    "Closed",
    "Done",  # old
    "Declined",  # old
]


def get_google_credentials():
    """
    Loads Google Sheets credentials from a json file.

    Returns:
        ServiceAccountCredentials: The loaded Google Sheets credentials.
    """
    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        "google-credentials.json",
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
    )
    return credentials


def call_jira_api(jira_url, auth):
    """
    Calls the JIRA API to get data.

    Args:
        jira_url (str): The JIRA API endpoint URL.
        auth (tuple): The email and API token for authentication.

    Returns:
        dict: The JSON response from the JIRA API.

    Raises:
        requests.exceptions.RequestException: If unable to retrieve issue details.
    """
    try:
        response = requests.get(jira_url, auth=auth, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Error: Unable to retrieve issue details. {error}")
        raise  # we re-raise the error so the backoff function can catch it and retry
    return response.json()


def get_issue_with_changelog(issue_id, auth):
    """
    Gets the issue details including the changelog from the JIRA API.

    This function uses the JIRA REST API endpoint to fetch the details of a specific issue.
    The issue is identified by its ID. The details of the issue includes its changelog,
    which contains a history of all the changes made to the issue. The function requires
    authentication which is passed as a tuple containing the email and API token.

    Args:
        issue_id (str): The ID of the issue.
        auth (tuple): A tuple containing the email and API token for authentication.

    Returns:
        dict or None: A dictionary containing the JSON response from the JIRA API if successful.
                      This includes the details of the issue along with its changelog.
                      If the function is unable to retrieve the issue details, it returns None.

    Raises:
        requests.exceptions.RequestException: If unable to retrieve issue details.
    """
    jira_url = (
        f"{URL}/rest/api/2/issue/{issue_id}"
        f"?fields=key,created,resolutiondate"
        "&expand=changelog"
    )
    issue_details = backoff(
        call_jira_api,
        args=(
            jira_url,
            auth,
        ),
        exceptions=requests.exceptions.RequestException,
    )
    return issue_details if issue_details else None


def get_issues(jql_query, max_results=1000):
    """
    Get issues from JIRA based on the JQL query provided.

    Args:
        jql_query (str): The JQL query.
        max_results (int, optional): The maximum number of results to return. Defaults to 1000.

    Returns:
        list: A list of issues if successful. None otherwise.
    """
    api_token = os.getenv("ATLASSIAN_API_TOKEN")
    api_email = os.getenv("ATLASSIAN_EMAIL")

    if api_token is None or api_email is None:
        print("Error: ATLASSIAN_API_TOKEN and ATLASSIAN_EMAIL must be set in the environment.")
        return []

    auth = (api_email, api_token)
    encoded_query = urllib.parse.quote(jql_query)

    start_at = 0
    all_issues = []

    while True:
        jira_url = (
            f"{URL}/rest/api/2/search"
            f"?jql={encoded_query}"
            f"&startAt={start_at}"
            f"&maxResults={max_results}"
            f"&fields=key"
        )

        try:
            issues = backoff(
                call_jira_api,
                args=(
                    jira_url,
                    auth,
                ),
                exceptions=requests.exceptions.RequestException,
            )
            for issue in issues["issues"]:
                issue_id = issue["key"]
                issue_details = get_issue_with_changelog(issue_id, auth)
                all_issues.append(issue_details)

            if len(issues["issues"]) < max_results:
                break  # we have fetched all issues
            start_at += max_results  # prepare the start_at for the next set of issues
        except ValueError as error:
            print(f"Error: Unable to parse response as JSON. {error}")
            return []

    return all_issues if all_issues else []


def get_cycle_time(issue, start_stage, end_stage):
    """
    Calculates the cycle time for a single issue.

    Args:
        issue (dict): The issue for which the cycle time needs to be calculated.

    Returns:
        float: The cycle time in days.
    """

    changelog = issue["changelog"]
    history = changelog["histories"]

    # Initialize start_time to a future date and end_time to a past date
    start_time = datetime.strptime(issue["fields"]["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
    end_time = datetime.strptime(issue["fields"]["resolutiondate"], "%Y-%m-%dT%H:%M:%S.%f%z")

    for record in history:
        if "items" in record:
            for item in record["items"]:
                if item["field"] != "status":
                    continue

                if STAGE_ORDER.index(item["toString"]) <= STAGE_ORDER.index(start_stage):
                    # Update start_time to the latest occurrence
                    created_time = datetime.strptime(record["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
                    start_time = max(start_time, created_time)

                # Check if the item is in the same or a later stage than the end_stage
                if STAGE_ORDER.index(item["toString"]) >= STAGE_ORDER.index(end_stage):
                    # Update end_time to the earliest occurrence of the same or later stage
                    created_time = datetime.strptime(record["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
                    end_time = min(end_time, created_time)

    return (end_time - start_time).total_seconds() / 86400  # convert to days


def calculate_cycle_time(issues, start_stage, end_stage):
    """
    Calculates the cycle time for a list of issues.

    Args:
        issues (list): The list of issues for which the cycle time needs to be calculated.
        start_stage (str): The start stage of the cycle.
        end_stage (str): The end stage of the cycle.

    Returns:
        float: The average cycle time across all issues.
    """
    cycle_times = []
    print(f"Calculating cycle time for {len(issues)} issues.")

    cycle_times = [get_cycle_time(issue, start_stage, end_stage) for issue in issues]
    # Remove None values from cycle_times list
    cycle_times = [cycle_time for cycle_time in cycle_times if cycle_time is not None]
    return sum(cycle_times) / len(cycle_times) if cycle_times else 0


def get_row_values(sheet, row):
    """
    Gets the values of a specific row from a Google Sheets document.

    This function calls the row_values method on the provided gspread.Spreadsheet instance (sheet)
    for a specific row number (row).

    :param sheet: gspread.Spreadsheet instance representing the Google Sheets document.
    :param row: Integer representing the row number for which to get the values.

    :return: List of cell values in the specified row.
    :rtype: list
    """
    return sheet.row_values(row)


def build_jql_query(month, issue_types, labels):
    """
    Builds a Jira Query Language (JQL) query based on given parameters.

    Args:
        month (str): The month for which the data needs to be fetched, in 'YYYY-MM' format.
        issue_types (list): The types of issues that need to be fetched.
        labels (list): The labels that the issues must have.

    Returns:
        str: The JQL query string.
    """
    start_date, end_date = get_month_range(date_input=month, output_format="date")

    jql_types = " OR ".join(f"issuetype={issue_type}" for issue_type in issue_types)

    jql_query = (
        f"project=Insights AND ({jql_types}) "
        f"AND resolutiondate >= {start_date} "
        f"AND resolutiondate <= {end_date}"
    )

    if labels:
        jql_query += f' AND labels in ({", ".join(labels)})'

    return jql_query


def update_google_sheets(credentials, dataframe, month_converted):
    """
    Updates a specified Google Sheets with the provided DataFrame.

    Args:
        credentials (ServiceAccountCredentials): The Google Sheets credentials.
        dataframe (DataFrame): The DataFrame containing the data to be updated.
        month_converted (str): The month in 'Month Year' format.
    """
    google = gspread.authorize(credentials)
    spreadsheet = backoff(google.open_by_key, args=("1f5xqsOXjS56k9NjUfZkFBHNlo4PMBKV9f_A0FHUf_1E",))
    sheet = spreadsheet.worksheet(SHEET_NAME)

    headers = backoff(get_row_values, args=(sheet, 1), exceptions=gspread.exceptions.APIError)

    if month_converted not in headers:
        headers.insert(1, month_converted)
        backoff(sheet.insert_cols, args=(2,), exceptions=gspread.exceptions.APIError)
        backoff(sheet.update, args=("A1", [headers]), exceptions=gspread.exceptions.APIError)

    column = headers.index(month_converted) + 1

    # Fetch all data once and store in memory
    data = backoff(sheet.get_all_values, exceptions=gspread.exceptions.APIError)

    for _, row in dataframe.iterrows():
        type_rows = [i for i, row_values in enumerate(data) if row_values[0] == row["Type"]]

        if not type_rows:
            continue

        row_index = type_rows[0]

        # Check if row_index is within the range of data
        if row_index >= len(data):
            data.append([""] * len(headers))

        # Update in memory
        if len(data[row_index]) < column:
            data[row_index] += [""] * (column - len(data[row_index]))
        data[row_index][column - 1] = float(row[month_converted])

        backoff(
            sheet.update_cell,
            args=(row_index + 1, column, data[row_index][column - 1]),
            exceptions=gspread.exceptions.APIError,
        )


def get_argument_parser():
    """
    Creates an argument parser.

    Returns:
        ArgumentParser: The created argument parser.
    """
    parser = argparse.ArgumentParser(description="Fetch PR data from Github.")
    parser.add_argument(
        "--months", nargs="*", default=[get_previous_month()], help='List of months in the "YYYY-MM" format.'
    )
    return parser


def main():
    """
    The main function.
    """
    argparser = get_argument_parser()
    args = argparser.parse_args()

    google_credentials = get_google_credentials()

    for month in args.months:
        month_converted = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
        print(f"Fetching data for {month_converted}")
        data = []
        for config in SYNC_CONFIG:
            print(f"Fetching data for {config['row_name']}")
            jql_query = build_jql_query(month, config["issue_types"], config["labels"])
            print(jql_query)
            issues = get_issues(jql_query)
            cycle_time = calculate_cycle_time(issues, config["start_stage"], config["end_stage"])
            data.append([config["row_name"], cycle_time])

        dataframe = pd.DataFrame(data, columns=["Type", month_converted])
        print(dataframe)
        update_google_sheets(google_credentials, dataframe, month_converted)


if __name__ == "__main__":
    main()
