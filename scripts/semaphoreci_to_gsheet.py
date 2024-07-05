#!/usr/bin/env python
# pylint: disable=import-error

"""
This script gathers and processes Semaphore CI project data, providing a summary of pipeline successes and
failures within a specified date range. It uses a Semaphore CI API Token, fetched from an environment
variable, to access the API data. The final data is written to a CSV file.

Refer to the project's README for setup prerequisites and usage instructions.
"""

import argparse
import functools
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

import gspread
import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from gspread_dataframe import set_with_dataframe
from oauth2client.service_account import ServiceAccountCredentials
from utilities import get_month_range

# Load environment variables
load_dotenv()

API_TOKEN = os.getenv("SEMAPHORECI_API_TOKEN")
ORG_NAME = os.getenv("SEMAPHORECI_ORG_NAME")
HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
}


BASE_URL = f"https://{ORG_NAME}.semaphoreci.com/api/v1alpha"


# Google Sheets details
GSHEET_ID = "1DQKeS1XsYytlQFFmCCUBOo_CGznCBJ6QVcdh6NqtOJA"


class ApiError(Exception):
    """An exception that represents an API error."""


def check_api_token():
    """
    Check that the API token is not None.

    Raises:
        ApiError: If the API token is None.
    """
    if API_TOKEN is None:
        raise ApiError("The API token is None. Please set the SEMAPHORECI_API_TOKEN environment variable.")


def handle_api_errors(func):
    """
    A decorator to handle API errors.

    This decorator catches requests exceptions and JSON decoding errors, and prints an error
    message.

    Args:
        func (callable): The function to decorate.

    Returns:
        callable: The decorated function.
    """

    @functools.wraps(func)
    def wrapper(*wrapper_args, **kwargs):
        try:
            return func(*wrapper_args, **kwargs)
        except (requests.exceptions.RequestException, json.JSONDecodeError) as error:
            print(f"An error occurred: {error}")
            return None

    return wrapper


def handle_rate_limiting(response):
    """
    Handle rate limiting by checking the response status code and headers.

    If the status code is 429 (Too Many Requests), the function will pause
    the execution for a certain amount of time before making the next request.
    The amount of time to pause is determined by the 'Retry-After' header in
    the response, if it is present. If the 'Retry-After' header is not present,
    the function will pause for 1 second by default.

    Args:
        response (requests.Response): The response object to check.

    Returns:
        bool: True if the response status code is 429, False otherwise.
    """
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            time.sleep(int(retry_after))
        else:
            time.sleep(1)
        return True
    return False


def valid_date(date_string):
    """
    Check that the date string is in the correct format (YYYY-MM).

    Args:
        date_string (str): The date string to check.

    Returns:
        str: The valid date string.

    Raises:
        argparse.ArgumentTypeError: If the date string is not in the correct format.
    """
    datetime.strptime(date_string, "%Y-%m")
    return date_string


@handle_api_errors
def get_projects():
    """
    Retrieve all projects.

    This function makes requests to the Semaphore CI API to retrieve all projects.
    If the API returns a 429 status code (rate limit exceeded), the function will
    pause execution for a certain amount of time before making the next request.

    Returns:
        list: A list of projects.

    Raises:
        ApiError: If the API returns an error status code.
    """
    projects_url = f"{BASE_URL}/projects"
    projects_response = requests.get(projects_url, headers=HEADERS, timeout=60)
    if projects_response.status_code != 200:
        raise ApiError(f"Error getting projects: {projects_response.text}")
    if handle_rate_limiting(projects_response):
        return get_projects()
    return projects_response.json()


@handle_api_errors
def get_pipelines(project_id, month=None, main_branch_only=False):
    """
    Retrieve all pipelines for a given project.

    This function makes requests to the Semaphore CI API to retrieve all pipelines
    for a project specified by the project_id. It handles pagination by checking
    the 'link' header in the response and updating the request URL to the 'next'
    URL if it is present.

    If the API returns a 429 status code (rate limit exceeded), the function will
    pause execution for a certain amount of time before making the next request.

    Args:
        project_id (str): The ID of the project to retrieve pipelines for.
        main_branch_only (bool): Whether to retrieve pipelines only for the "main" branch.
            Default is False.

    Returns:
        list: A list of pipelines for the project.
    """
    start_of_month, end_of_month = get_month_range(month)

    pipelines_url = (
        f"{BASE_URL}/pipelines"
        f"?project_id={project_id}"
        f"&created_after={start_of_month}"
        f"&created_before={end_of_month}"
    )

    if main_branch_only:
        pipelines_url += "&branch=main"
    pipelines = []
    while True:
        pipelines_response = requests.get(pipelines_url, headers=HEADERS, timeout=60)
        if handle_rate_limiting(pipelines_response):
            continue
        pipelines_data = pipelines_response.json()
        pipelines.extend(pipelines_data)
        link_header = pipelines_response.headers.get("link")
        if link_header is not None:
            pattern = r'<(.+?)>; rel="next"'
            matches = re.findall(pattern, link_header)
            if matches:
                pipelines_url = matches[0]
            else:
                break
        else:
            break
    return pipelines


@handle_api_errors
def get_pipeline_details(pipeline_id):
    """
    Retrieve the details of a specific pipeline.

    This function makes a request to the Semaphore CI API to retrieve the details
    of a pipeline specified by the pipeline_id. If the API returns a 429 status
    code (rate limit exceeded), the function will pause execution for a certain
    amount of time before making the next request.

    Args:
        pipeline_id (str): The ID of the pipeline to retrieve details for.

    Returns:
        dict: A dictionary containing the details of the pipeline.
    """
    pipeline_url = f"{BASE_URL}/pipelines/{pipeline_id}?detailed=true"
    pipeline_response = requests.get(pipeline_url, headers=HEADERS, timeout=60)
    if handle_rate_limiting(pipeline_response):
        return get_pipeline_details(pipeline_id)
    return pipeline_response.json()


def process_pipeline(pipeline, project_name, counter):
    """
    Process a pipeline and update the counter.

    Args:
        pipeline (dict): The pipeline to process.
        project_name (str): The name of the project the pipeline belongs to.
        counter (collections.defaultdict): The counter for project statuses.
    """
    pipeline_details = get_pipeline_details(pipeline["ppl_id"])

    if not pipeline_details:
        counter[project_name][pipeline["result"].lower()] += 1
        return

    if "blocks" in pipeline_details and len(pipeline_details["blocks"]) > 1:
        for block in pipeline_details["blocks"]:
            jobs = block.get("jobs", [])
            results = [job["result"].lower() for job in jobs] if jobs else [block["result"].lower()]
            for result in results:
                counter[project_name][result] += 1
    elif "pipeline" in pipeline_details and "result" in pipeline_details["pipeline"]:
        counter[project_name][pipeline_details["pipeline"]["result"].lower()] += 1
    else:
        counter[project_name][pipeline["result"].lower()] += 1


def get_project_metrics(csv=False, main_branch_only=False, month=None):
    """
    Retrieve failure details for all projects and write them to a CSV file.

    This function retrieves all projects and their associated pipelines. For each pipeline,
    it checks the state and result of each block and job, and counts the number of successes
    and failures. The counts are stored in a defaultdict of Counters.

    Args:
        main_branch_only (bool): Whether to retrieve pipelines only for the "main" branch.
            Default is False.
        month (datetime.date, optional): The month to retrieve pipelines for.
            If None, pipelines for the last month will be retrieved. Defaults to None.
    """
    projects = get_projects()
    counter = defaultdict(Counter)

    for project in projects:
        if "id" not in project["metadata"]:
            print(f"No 'id' key in project: {project}")
            continue
        project_id = project["metadata"]["id"]
        project_name = project["metadata"]["name"]
        pipelines = get_pipelines(project_id, month, main_branch_only)
        print(f"Found {len(pipelines)} pipelines for project {project_name}")
        if not pipelines:
            counter[project_name]["passed"] = 0
            counter[project_name]["failed"] = 0
            continue
        for pipeline in pipelines:
            process_pipeline(pipeline, project_name, counter)

        # Check if the total count of results is less than the number of pipelines
        total_results = sum(counter[project_name].values())
        if total_results < len(pipelines):
            print(
                f"Warning: Total result count ({total_results}) for project {project_name} "
                f"is less than the number of pipelines ({len(pipelines)})"
            )

    dataframe = counter_to_dataframe(counter)
    if csv:
        write_to_csv(dataframe, month, main_branch_only)
    else:
        write_to_gsheet(dataframe, month)


def counter_to_dataframe(counter):
    """
    Converts a collections.defaultdict counter to a pandas DataFrame.

    Args:
        counter (collections.defaultdict): A defaultdict object that contains
            the counts of each dynamic result for each project.

    Returns:
        dataframe (pandas.DataFrame): A DataFrame representation of the counter.
    """
    # Identify all unique result names across all projects
    result_names = set()
    for project_counter in counter.values():
        result_names.update(project_counter.keys())

    data = {"Project Name": list(counter.keys())}

    # Add a column for each result name
    for result_name in result_names:
        data[result_name] = [project_counter.get(result_name, 0) for project_counter in counter.values()]

    dataframe = pd.DataFrame(data)
    return dataframe


def write_to_csv(dataframe, month, main_branch_only=True):
    """
    Write the DataFrame to a CSV file.

    Args:
        dataframe (pandas.DataFrame): The DataFrame to write to a CSV file.
        month (str): The month, in the format "YYYY-MM".
    """
    filename = f"{month}_semaphoreci_build_metrics.csv"
    if main_branch_only:
        filename = f"{month}_semaphoreci_build_metrics_main_branch.csv"
    dataframe.to_csv(filename, index=False)


def write_to_gsheet(dataframe, month):
    """
    Upload the DataFrame to a new sheet in the Google Spreadsheet.

    Args:
        dataframe (pandas.DataFrame): The DataFrame to write to a Google Spreadsheet.
        month (str): The month, in the format "YYYY-MM".
    """

    # Setup authentication
    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        "google-credentials.json",
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
    )

    # Access the Google Spreadsheet
    google = gspread.authorize(credentials)
    spreadsheet = google.open_by_key(GSHEET_ID)

    # Try to select the sheet with the name of the month
    # If it doesn't exist, create it
    try:
        worksheet = spreadsheet.worksheet(month)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=month, rows="100", cols="20")

    # Write the dataframe to the sheet
    set_with_dataframe(worksheet, dataframe, include_column_header=True)


def main():
    """Main function."""
    # Create argument parser
    parser = argparse.ArgumentParser(
        description=(
            "Fetch data for Semaphore CI projects and generate a "
            "detailed report of pipeline failures and successes."
        )
    )

    # default date is last month
    default_date = (datetime.now() - relativedelta(months=1)).strftime("%Y-%m")

    parser.add_argument(
        "--month",
        type=valid_date,
        default=default_date,
        help=f"Month to sync, format YYYY-MM. Default is last month ({default_date}).",
    )

    parser.add_argument(
        "--main-branch-only",
        action="store_true",
        default=True,
        help="Retrieve results only for the 'main' branch. Default is True.",
    )

    parser.add_argument(
        "--csv",
        action="store_true",
        default=False,
        help="Write results to CSV. Default is False and writes to GSheet.",
    )

    args = parser.parse_args()

    # Check API token
    check_api_token()

    # Get project metrics
    get_project_metrics(csv=args.csv, main_branch_only=args.main_branch_only, month=args.month)


if __name__ == "__main__":
    main()
