#!/usr/bin/env python
# pylint: disable=import-error,too-many-locals

"""
Automatically update the Merges and Reviews sheets in the Google Sheets document.
"""

import argparse
import datetime
import os
import time

import gspread
import pandas as pd
from cachetools import TTLCache, cached
from dotenv import load_dotenv
from github import Github, GithubException
from oauth2client.service_account import ServiceAccountCredentials
from utilities import backoff, get_month_range, get_previous_month

load_dotenv()  # take environment variables from .env.

# Define a cache that stores up to 1000 items and expires after 10 minutes.
cache = TTLCache(maxsize=1000, ttl=600)


def get_credentials():
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


def get_github_token():
    """
    Gets the Github token from the environment variable.

    Returns:
        str: The Github token.
    """
    return os.getenv("GITHUB_TOKEN")


def get_row_values(sheet, row):
    """
    Gets the values of a specific row from a Google Sheets document.

    :param sheet: (gspread.Spreadsheet) instance representing the Google Sheets document.
    :param row: (int) representing the row number for which to get the values.

    :return: List of cell values in the specified row.
    :rtype: list
    """
    return sheet.row_values(row)


@cached(cache)
def get_user_aliases(credentials):
    """
    Fetches user aliases from Google Sheets. This function uses caching mechanism provided by
    'cachetools' library to minimize the repetitive API calls. The cache expires after 10 minutes.

    :param credentials: (ServiceAccountCredentials) Google Sheets API credentials.

    Returns:
        dict: A dictionary with Github usernames as keys and full names as values.
    """
    google = gspread.authorize(credentials)
    spreadsheet = backoff(google.open_by_key, args=("1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA",))
    usernames = pd.DataFrame(spreadsheet.worksheet("Aliases").get_all_records())
    username_map = usernames.set_index("Username").to_dict()["Engineer - IC"]

    return username_map


def get_all_pr_reviewers(pull):
    """
    Fetches all unique reviewers who approved the given pull request along with their review counts.
    This function also uses caching to minimize the repetitive API calls.

    Args:
        pull (PullRequest): The Github pull request.

    Returns:
        dict: Dictionary with Github usernames as keys and their review counts as values.
    """
    review_authors = {}
    for review in pull.get_reviews():
        if review.user.login != pull.user.login:  # Exclude the author of the PR
            review_authors[review.user.login] = review_authors.get(review.user.login, 0) + 1

    return review_authors


def get_all_pr_comment_authors(pull):
    """
    Fetches all unique authors who commented on the given pull request along with their comment counts.

    Args:
        pull (PullRequest): The Github pull request.

    Returns:
        dict: Dictionary with Github usernames as keys and their comment counts as values.
    """
    # Get issue comment authors
    issue_comments = {}
    for comment in pull.get_issue_comments():
        if comment.user.login != pull.user.login:  # Exclude the author of the PR
            issue_comments[comment.user.login] = issue_comments.get(comment.user.login, 0) + 1

    # Get review comment authors
    review_comments = {}
    for comment in pull.get_review_comments():
        if comment.user.login != pull.user.login:  # Exclude the author of the PR
            review_comments[comment.user.login] = review_comments.get(comment.user.login, 0) + 1

    all_comment_authors = issue_comments.copy()
    for user, count in review_comments.items():
        all_comment_authors[user] = all_comment_authors.get(user, 0) + count

    return all_comment_authors


def process_pull_request(pull, username_map):
    """
    Process a single pull request.

    Args:
        pull (PullRequest): The pull request object from PyGithub.
        username_map (dict): The mapping from Github username to full name.

    Returns:
        dict: A dictionary mapping each user to a dictionary with counts of merges, reviews, and changes.
    """
    user_login = pull.user.login
    user_full_name = username_map.get(user_login)

    result = {}

    if user_full_name:
        result[user_full_name] = {"merges": 1, "reviews": 0, "changes": pull.additions + pull.deletions}

    comment_authors = get_all_pr_comment_authors(pull)
    review_authors = get_all_pr_reviewers(pull)

    all_contributors = set(list(comment_authors.keys()) + list(review_authors.keys()))

    for contributor in all_contributors:
        contributor_full_name = username_map.get(contributor)

        if contributor_full_name:
            if contributor_full_name not in result:
                result[contributor_full_name] = {"merges": 0, "reviews": 0, "changes": 0}

            result[contributor_full_name]["reviews"] += comment_authors.get(contributor, 0)
            result[contributor_full_name]["reviews"] += review_authors.get(contributor, 0)

    return result


def fetch_github_data(github_token, username_map, month):
    """
    Fetches Github data for a list of months.

    Args:
        github_token (str): The Github token.
        username_map (dict): The mapping from Github username to full name.
        months (list[str]): List of months for which data is to be fetched, in 'YYYY-MM' format.

    Returns:
        dict: A dictionary mapping each month to a tuple of three dictionaries representing
              merges, reviews, and changes respectively.
    """
    github = Github(github_token)
    org_name = os.getenv("GITHUB_ORG")

    start_date, end_date = get_month_range(date_input=month, output_format="date")

    query = f"org:{org_name} state:closed review:approved is:pr merged:{start_date}..{end_date}"
    print(f"Query: {query}")
    issues = github.search_issues(query)
    print(f"Issues Found: {issues.totalCount}")

    merges = {user_full_name: 0 for user_full_name in username_map.values()}
    reviews = {user_full_name: 0 for user_full_name in username_map.values()}
    changes = {user_full_name: 0 for user_full_name in username_map.values()}
    for issue in issues:
        while True:
            try:
                pull = issue.repository.get_pull(issue.number)
                data = process_pull_request(pull, username_map)

                for user_full_name, counts in data.items():
                    merges[user_full_name] += counts["merges"]
                    reviews[user_full_name] += counts["reviews"]
                    changes[user_full_name] += counts["changes"]
                break
            except GithubException as error:
                if error.status == 403:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    print(f"Current Time: {now}")
                    print("Rate limit exceeded. Waiting for reset...")
                    rate_limit = github.get_rate_limit()
                    print(f"Rate Limit: {rate_limit.core.limit}")
                    print(f"Rate Limit Remaining: {rate_limit.core.remaining}")
                    print(f"Rate Limit Reset Time: {rate_limit.core.reset}")
                    if rate_limit.core.remaining == 0:
                        reset_time_naive = rate_limit.core.reset
                        # Make it aware by associating it with the UTC timezone
                        reset_time = reset_time_naive.replace(tzinfo=datetime.timezone.utc)
                        sleep_time = (reset_time - now).total_seconds()
                        print(f"Rate limit exceeded, sleeping for {sleep_time} seconds")
                        time.sleep(abs(sleep_time))
                else:
                    raise error

    print(f"merges: {merges}")
    print(f"reviews: {reviews}")
    print(f"changes: {changes}")
    return merges, reviews, changes


def update_google_sheets(credentials, dataframe, sheet_name, month_converted):
    """
    Updates Google Sheets with Github data for a specific month.

    Args:
        credentials (ServiceAccountCredentials): The Google Sheets credentials.
        dataframe (DataFrame): The dataframe containing the Github data.
        sheet_name (str): The name of the Google Sheets document to be updated.
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

    # Fetch all data once and store in memory
    data = backoff(sheet.get_all_values, exceptions=gspread.exceptions.APIError)

    print(sheet_name)
    print(dataframe)

    for _, row in dataframe.iterrows():
        user_rows = [i for i, row_values in enumerate(data) if row_values[0] == row["Engineer - IC"]]

        if not user_rows:
            continue

        row_index = user_rows[0]

        # Check if row_index is within the range of data
        if row_index >= len(data):
            data.append([""] * len(headers))

        # Update in memory
        if len(data[row_index]) < column:
            data[row_index] += [""] * (column - len(data[row_index]))
        data[row_index][column - 1] = int(row["count"])

        backoff(
            sheet.update_cell,
            args=(row_index + 1, column, data[row_index][column - 1]),
            exceptions=gspread.exceptions.APIError,
        )


def transform_data_to_dataframe(data_dict):
    """
    Transforms a dictionary into a DataFrame.

    Args:
        data_dict (dict): The dictionary containing the data.

    Returns:
        DataFrame: The DataFrame created from the data.
    """
    data_list = [
        {"Engineer - IC": user_full_name, "count": count} for user_full_name, count in data_dict.items()
    ]
    return pd.DataFrame(data_list)


def fetch_data(credentials, github_token, months):
    """
    Fetches the Github data and updates the Google Sheets document. It fetches data for each
    month provided in the 'months' list. By default, it fetches data for the previous month.

    Args:
        credentials (ServiceAccountCredentials): The Google Sheets credentials.
        github_token (str): The Github token.
        months (list[str]): A list of months in the 'YYYY-MM' format.
    """
    if months is None:
        months = [get_previous_month()]

    if not isinstance(months, list):
        months = [months]

    username_map = get_user_aliases(credentials)
    print(f"Username Map: {username_map}")

    for month in months:
        print(f"Fetching Github data for {month}")
        merges, reviews, changes = fetch_github_data(github_token, username_map, month)
        print(f"Done fetching Github data for {month}")

        month_converted = datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")

        merges_df = transform_data_to_dataframe(merges)
        reviews_df = transform_data_to_dataframe(reviews)
        changes_df = transform_data_to_dataframe(changes)

        update_google_sheets(credentials, merges_df, "Merges", month_converted)
        update_google_sheets(credentials, reviews_df, "Reviews", month_converted)
        update_google_sheets(credentials, changes_df, "Code Changes", month_converted)


def get_argument_parser():
    """
    Creates an argument parser. It takes 'months' as an optional argument which is a list of months
    in the 'YYYY-MM' format. By default, it considers the previous month.

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
    The main function. It sets up the argument parser, fetches the Google Sheets credentials
    and Github token, and then calls the 'fetch_data' function to fetch the Github data and
    update the Google Sheets document.
    """
    argparser = get_argument_parser()
    args = argparser.parse_args()
    credentials = get_credentials()
    github_token = get_github_token()
    fetch_data(credentials, github_token, args.months)


if __name__ == "__main__":
    main()
