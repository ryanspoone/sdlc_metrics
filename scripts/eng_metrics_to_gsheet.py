#!/usr/bin/env python
# pylint: disable=import-error,too-many-locals

"""
Automatically update the Jira sheets in the Google Sheets document.
"""

import argparse
import datetime
import os
import urllib.parse

import gspread
import requests
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from utilities import backoff, get_previous_month

load_dotenv()  # take environment variables from .env.

STATUS_CHANGE_TEMPLATE = (
    'filter = "Filter for Insights" AND status changed TO ({})'
    " AFTER startOfMonth(-1) AND status changed TO ({}) BEFORE endOfMonth(-1)"
    " AND type in ({}) AND (labels != Billable OR labels is EMPTY)"
    ' AND assignee not in membersOf("Squad - Vampire")'
)

STATUS_IN_TEMPLATE = (
    'filter = "Filter for Insights" AND type in ({})'
    " AND status in ({}) AND (labels != Billable OR labels is EMPTY)"
    ' AND assignee not in membersOf("Squad - Vampire")'
)

CREATED_DATE_TEMPLATE = (
    'filter = "Filter for Insights" AND type in ({})'
    " AND createdDate >= startOfMonth(-1) AND createdDate <= endOfMonth(-1)"
    ' AND (labels != Billable OR labels is EMPTY) AND assignee not in membersOf("Squad - Vampire")'
)

FLAGGED_DEFECTS_TEMPLATE = (
    'filter = "Filter for Insights" AND status changed FROM "{}"'
    ' AFTER startOfMonth(-1) AND status changed TO "{}" BEFORE endOfMonth(-1)'
    ' AND (priority = "High" OR priority = "Immediate" OR savedfilter = "Insights - Unresolved Support Bugs")'
    ' AND (labels != Billable OR labels is EMPTY) AND assignee not in membersOf("Squad - Vampire")'
)

FLAGGED_DEFECTS_CREATED_TEMPLATE = (
    'filter = "Filter for Insights" AND type in ({})'
    " AND created >= startOfMonth(-1) AND created <= startOfMonth()"
    " AND (labels != Billable OR labels is EMPTY)"
    ' AND filter = "Insights - Support Bugs" AND assignee not in membersOf("Squad - Vampire")'
)


METRICS_QUERIES = {
    "Issues completed": STATUS_CHANGE_TEMPLATE.format(
        '"Merged", "Awaiting Approval", "Closed"',
        '"Merged", "Awaiting Approval", "Closed"',
        "Bug, Story, Epic, Task, Sub-task",
    ),
    "Epics completed": STATUS_CHANGE_TEMPLATE.format(
        '"Merged", "Awaiting Approval", "Closed"', '"Merged", "Awaiting Approval", "Closed"', "Epic"
    ),
    "Stories completed": STATUS_CHANGE_TEMPLATE.format(
        '"Merged", "Awaiting Approval", "Closed"', '"Merged", "Awaiting Approval", "Closed"', "Story"
    ),
    "Defects completed": STATUS_CHANGE_TEMPLATE.format(
        '"Merged", "Awaiting Approval", "Closed"', '"Merged", "Awaiting Approval", "Closed"', "Bug"
    ),
    "Epics Stories and Defects completed": STATUS_CHANGE_TEMPLATE.format(
        '"Merged", "Closed"', '"Merged", "Closed"', "Epic, Story, Bug"
    ),
    "Declined Stories/Epics": STATUS_CHANGE_TEMPLATE.format(
        '"Merged", "Closed"', '"Merged", "Closed"', "Story, Epic"
    ),
    "Epic\n(Open -> In Progress)": STATUS_CHANGE_TEMPLATE.format("Open", '"In Progress"', "Epic"),
    "Epic\n(In Progress -> Merged)": STATUS_CHANGE_TEMPLATE.format('"In Progress"', "Merged", "Epic"),
    "Stories\n(In Progress -> Merged)": STATUS_CHANGE_TEMPLATE.format('"In Progress"', "Merged", "Story"),
    "Defects\n(Triage -> Merged)": STATUS_CHANGE_TEMPLATE.format("Triage", "Merged", "Bug"),
    "Flagged Defects\n(Triage -> Merged)": FLAGGED_DEFECTS_TEMPLATE.format(
        "Triage",
        "Merged",
    ),
    "Number of Defects Open": STATUS_IN_TEMPLATE.format("Bug", 'Triage, Open, "In Progress", "In Review"'),
    "Total epics/stories in Open": STATUS_IN_TEMPLATE.format("Story, Epic", "Open"),
    "Number of Defects created": CREATED_DATE_TEMPLATE.format("Bug"),
    "Number of Flagged Defects created": FLAGGED_DEFECTS_CREATED_TEMPLATE.format("Bug"),
}


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
    """
    try:
        response = requests.get(jira_url, auth=auth, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Error: Unable to retrieve issue details. {error}")
        raise
    return response.json()


def get_issue_count(jql_query, max_results=1000):
    """
    Get issues from JIRA based on the JQL query provided.

    Args:
        jql_query (str): The JQL query.
        max_results (int, optional): The maximum number of results to return. Defaults to 1000.

    Returns:
        int: A number of issues if successful. Zero otherwise.
    """
    api_token = os.getenv("ATLASSIAN_API_TOKEN")
    api_email = os.getenv("ATLASSIAN_EMAIL")
    api_url = os.getenv("ATLASSIAN_URL")


    if api_token is None or api_email is None or api_url is None:
        print("Error: ATLASSIAN_API_TOKEN, ATLASSIAN_EMAIL, and ATLASSIAN_URL must be set in the environment.")
        return 0

    auth = (api_email, api_token)
    encoded_query = urllib.parse.quote(jql_query)

    start_at = 0
    count = 0

    while True:
        jira_url = (
            f"{api_url}/rest/api/2/search"
            f"?jql={encoded_query}"
            f"&startAt={start_at}"
            f"&maxResults={max_results}"
            "&fields=key"
        )

        try:
            issue_details = call_jira_api(jira_url, auth)
            count += len(issue_details["issues"])
            if len(issue_details["issues"]) < max_results:
                break  # we have fetched all issues
            start_at += max_results  # prepare the start_at for the next set of issues
        except ValueError as error:
            print(f"Error: Unable to parse response as JSON. {error}")
            return count

    return count


def fetch_and_update_metrics(months):
    """
    Fetch the required metrics from JIRA and update the Google sheet.

    Args:
        months (list): List of months in the "YYYY-MM" format.
    """
    credentials = get_google_credentials()
    google = gspread.authorize(credentials)
    spreadsheet = backoff(google.open_by_key, args=("1f5xqsOXjS56k9NjUfZkFBHNlo4PMBKV9f_A0FHUf_1E",))
    worksheet = spreadsheet.worksheet("Data")

    metrics_data = {}

    for month in months:
        # Convert the month to "Month YYYY" format
        month_datetime = datetime.datetime.strptime(month, "%Y-%m")
        month_formatted = month_datetime.strftime("%B %Y")

        metrics_for_month = {}
        for metric, jql_query in METRICS_QUERIES.items():
            print(f"Fetching {metric} for {month_formatted}...")
            print(f"JQL query: {jql_query}")
            issue_count = get_issue_count(jql_query)
            metrics_for_month[metric] = issue_count

        metrics_data[month_formatted] = metrics_for_month

    # Find or create the columns for the months
    all_months = [cell.value for cell in worksheet.range("2:2")]
    for month, metrics in metrics_data.items():
        if month not in all_months:
            print(f"Adding column for {month}...")
            print(f"All months: {all_months}")
            all_months.append(month)
            worksheet.update_cell(1, len(all_months), month)

        month_column = all_months.index(month) + 1  # 1-indexed

        for metric, value in metrics.items():
            # Find or create the row for the metric
            all_metrics = [cell.value for cell in worksheet.range("B:B")]
            if metric not in all_metrics:
                all_metrics.append(metric)
                worksheet.update_cell(len(all_metrics), 2, metric)

            metric_row = all_metrics.index(metric) + 1  # 1-indexed

            # Update the cell with the metric value
            worksheet.update_cell(metric_row, month_column, value)


def get_argument_parser():
    """
    Creates an argument parser.

    Returns:
        ArgumentParser: The created argument parser.
    """
    parser = argparse.ArgumentParser(description="Fetch engineering metrics.")
    parser.add_argument(
        "--months", nargs="*", default=[get_previous_month()], help='List of months in the "YYYY-MM" format.'
    )
    return parser


def main():
    """
    The main function.
    """
    parser = get_argument_parser()
    args = parser.parse_args()
    fetch_and_update_metrics(args.months)


if __name__ == "__main__":
    main()
