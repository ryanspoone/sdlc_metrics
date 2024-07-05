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
import pandas as pd
import requests
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from utilities import backoff, get_month_range, get_previous_month

load_dotenv()  # take environment variables from .env.

# Configurations for different issue types and their respective sheets in Google Sheets.
SYNC_CONFIG = [
    {"issue_types": ["story"], "tab_name": "Stories", "labels": []},
    {"issue_types": ["epic"], "tab_name": "Epics", "labels": []},
    {"issue_types": ["bug"], "tab_name": "Bugs", "labels": []},
    {"issue_types": ["task", "subtask", "sub-task"], "tab_name": "Tasks", "labels": []},
    {"issue_types": ["bug"], "tab_name": "Support Bugs", "labels": ["jira_escalated", "support"]},
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


def get_issues(jql_query, max_results=50):
    """
    Get issues from JIRA based on the JQL query provided.

    Args:
        jql_query (str): The JQL query.
        max_results (int, optional): The maximum number of results to return. Defaults to 50.

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
        )

        try:
            issue_details = backoff(
                call_jira_api,
                args=(
                    jira_url,
                    auth,
                ),
                exceptions=requests.exceptions.RequestException,
            )
            all_issues.extend(issue_details["issues"])
            if len(issue_details["issues"]) < max_results:
                break  # we have fetched all issues
            start_at += max_results  # prepare the start_at for the next set of issues
        except ValueError as error:
            print(f"Error: Unable to parse response as JSON. {error}")
            return []

    return all_issues if all_issues else []


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


def get_user_aliases(credentials):
    """
    Fetch user aliases from Google Sheets.

    Returns:
        dict: A dictionary with usernames as keys and full names as values.
    """
    google = gspread.authorize(credentials)
    spreadsheet = backoff(google.open_by_key, args=("1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA",))
    usernames = pd.DataFrame(spreadsheet.worksheet("Aliases").get_all_records())
    username_map = usernames.set_index("Email").to_dict()["Engineer - IC"]

    return username_map


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
        # "AND status CHANGED FROM "
        # f'("In Progress", "In Review") TO '
        # '("Merged", "Awaiting Approval", "Deployed", "Closed", "Done") '
        # f'DURING("{start_date}", "{end_date}") '
        f"AND resolutiondate >= {start_date} "
        f"AND resolutiondate <= {end_date}"
    )

    if labels:
        jql_query += f' AND labels in ({", ".join(labels)})'

    return jql_query


def get_jira_data(jql_query, email_map):
    """
    Fetches JIRA issue data for the given query and maps user emails to their full names.

    Args:
        jql_query (str): The JQL query to be executed.
        email_map (dict): A dictionary mapping user emails to full names.

    Returns:
        dict: A dictionary mapping user full names to the number of issues assigned to them.
    """
    issues = get_issues(jql_query)
    if issues is None:
        return {}

    data = {user_full_name: 0 for user_full_name in email_map.values()}
    for issue in issues or []:
        assignee = issue["fields"]["assignee"]
        if assignee is None or "emailAddress" not in assignee:
            continue
        assignee = issue["fields"]["assignee"]["emailAddress"]
        user_full_name = email_map.get(assignee, assignee)
        data[user_full_name] = data.get(user_full_name, 0) + 1
    return data


def update_google_sheets(credentials, dataframe, sheet_name, month_converted):
    """
    Updates a specified Google Sheets with the provided DataFrame.

    Args:
        credentials (ServiceAccountCredentials): The Google Sheets credentials.
        dataframe (DataFrame): The DataFrame containing the data to be updated.
        sheet_name (str): The name of the sheet to be updated.
        month_converted (str): The month in 'Month Year' format.
    """
    google = gspread.authorize(credentials)
    spreadsheet = backoff(google.open_by_key, args=("1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA",))
    sheet = spreadsheet.worksheet(sheet_name)

    headers = backoff(get_row_values, args=(sheet, 1), exceptions=gspread.exceptions.APIError)

    if month_converted not in headers:
        headers.insert(1, month_converted)
        backoff(sheet.insert_cols, args=(2,), exceptions=gspread.exceptions.APIError)
        backoff(sheet.update, args=("A1", [headers]), exceptions=gspread.exceptions.APIError)

    column = headers.index(month_converted) + 1

    for _, row in dataframe.iterrows():
        data = backoff(sheet.get_all_values, exceptions=gspread.exceptions.APIError)
        user_rows = [i for i, row_values in enumerate(data) if row_values[0] == row["Engineer - IC"]]

        if not user_rows:
            continue

        row_index = user_rows[0] + 1
        backoff(
            sheet.update_cell,
            args=(
                row_index,
                column,
                row["count"],
            ),
            exceptions=gspread.exceptions.APIError,
        )


def transform_data_to_dataframe(data_dict):
    """
    Transforms a dictionary into a DataFrame.

    Args:
        data_dict (dict): The dictionary containing the data.
        data_type (str): The type of data, i.e., Merges, Reviews or Code Changes.
        month (str): The month in 'Month Year' format.

    Returns:
        DataFrame: The DataFrame created from the data.
    """
    data_list = [
        {"Engineer - IC": user_full_name, "count": count} for user_full_name, count in data_dict.items()
    ]
    return pd.DataFrame(data_list)


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
    email_map = get_user_aliases(google_credentials)

    for month in args.months:
        month_converted = datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")

        for config in SYNC_CONFIG:
            print(f"Getting data for {' and '.join(config['issue_types'])} during {month}")
            jql_query = build_jql_query(
                month=month, issue_types=config["issue_types"], labels=config["labels"]
            )
            data = get_jira_data(jql_query=jql_query, email_map=email_map)
            dataframe = transform_data_to_dataframe(data)
            print(f"Writing data for {' and '.join(config['issue_types'])} during {month}")
            update_google_sheets(
                credentials=google_credentials,
                dataframe=dataframe,
                sheet_name=config["tab_name"],
                month_converted=month_converted,
            )


if __name__ == "__main__":
    main()
