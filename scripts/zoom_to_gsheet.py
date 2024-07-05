#!/usr/bin/env python
# pylint: disable=import-error

"""Write Zoom meeting data to Google Sheets."""

import argparse
import json
from datetime import datetime

import gspread
import pandas as pd
import requests
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from refresh_zoom_access_tokens import refresh_access_tokens
from utilities import backoff, get_month_range, get_previous_month, to_snake_case

load_dotenv()  # take environment variables from .env.

ONE_WEEK_IN_SECONDS = 604800


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


def get_name_map(credentials):
    """
    Fetch data for engineers from Google Sheets.

    Args:
        credentials (ServiceAccountCredentials): Google Sheets credentials.

    Returns:
        dict: A dictionary with name as keys and dictionaries with data from other columns as values.
    """
    google = gspread.authorize(credentials)
    spreadsheet = backoff(google.open_by_key, args=("1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA",))
    aliases = pd.DataFrame(spreadsheet.worksheet("Aliases").get_all_records())

    # create map with name as key and a dictionary with data from other columns as values
    name_map = {
        row["Engineer - IC"]: {
            to_snake_case(col): row[col] for col in aliases.columns if aliases.columns.get_loc(col) > 1
        }
        for _, row in aliases.iterrows()
    }

    return name_map


def participant_in_name_map(participant, name_map):
    """
    Check if a participant's email or name is in the name map.

    Args:
        participant (dict): A dictionary with participant data.
        name_map (dict): A dictionary with engineer names as keys and dictionaries with other data as values.

    Returns:
        bool, dict: A boolean indicating if the participant is in the name map, and the participant
                    info if they are.
    """
    for _, info in name_map.items():
        for _, value in info.items():
            if value in (participant["user_email"], participant["name"]):
                return True, info

    return False, None


def make_request(url, headers, params):
    """
    Send a HTTP GET request with retries in case of failure.

    Args:
        url (str): The URL to send the request to.
        headers (dict): HTTP headers to include in the request.
        params (dict): Query parameters to include in the request.

    Returns:
        Response: The response to the request.
    """
    return backoff(
        requests.get,
        args=(url,),
        kwargs={"headers": headers, "params": params},
    )


def process_participant(participant, name_map, meeting_info, is_adhoc, result):
    """
    Process participant data, including updating the participant's meeting count and hours in meetings.

    Args:
        participant (dict): A dictionary with participant data.
        name_map (dict): A dictionary with engineer names as keys and dictionaries with other data as values.
        meeting_info (dict): A dictionary containing details about the meeting, including its duration.
        is_adhoc (bool): A flag indicating if the meeting is ad hoc.
        result (dict): The result dictionary to be updated.

    Note: The function will directly modify the result dictionary.
    """
    if participant_in_name_map(participant, name_map)[0]:
        _, participant_info = participant_in_name_map(participant, name_map)

        if not participant_info:
            print(f"Could not find participant information for participant {participant}.")
            return

        full_name = next((key for key, value in name_map.items() if value == participant_info), None)

        print(f"Processing meeting for {full_name}...")

        result[full_name]["meeting_count"] += 1
        result[full_name]["hours_in_meetings"] += meeting_info["duration"] / 60  # Convert minutes to hours
        if is_adhoc:
            result[full_name]["ad_hoc_meeting_count"] += 1


def process_meeting(meeting, headers, name_map, result):
    """
    Process data for each meeting including each participant's information.

    Args:
        meeting (dict): A dictionary containing meeting data.
        headers (dict): Request headers for the Zoom API.
        name_map (dict): A dictionary with engineer names as keys and dictionaries with other data as values.
        result (dict): The result dictionary to be updated.

    Note: The function will directly modify the result dictionary.
    """
    params_participants = {"page_size": 300}

    all_participants = []

    while True:
        participant_info = make_request(
            f"https://api.zoom.us/v2/past_meetings/{meeting['id']}/participants", headers, params_participants
        ).json()

        if "participants" not in participant_info:
            break

        all_participants.extend(participant_info["participants"])

        if "next_page_token" in participant_info and participant_info["next_page_token"]:
            params_participants["next_page_token"] = participant_info["next_page_token"]
        else:
            break

    # Fetch detailed meeting info
    response = make_request(f"https://api.zoom.us/v2/past_meetings/{meeting['id']}", headers, {})
    if response.status_code == 200 and response.text:
        try:
            meeting_info = response.json()
        except json.JSONDecodeError:
            print(f"Failed to decode JSON. Response was: {response.text}")
            return
    else:
        print(f"Error: Received status code {response.status_code}")
        return

    # Check if the meeting is ad hoc
    created_at = pd.to_datetime(meeting_info.get("created_at"))
    start_time = pd.to_datetime(meeting["start_time"])

    if meeting_info and "type" not in meeting_info:
        print(f"Meeting {meeting['id']} does not have a type.")
        print(f"Meeting info: {meeting_info}")
        return
    is_adhoc = (
        meeting_info["type"] == 1
        or (created_at and start_time and (start_time - created_at).total_seconds() <= ONE_WEEK_IN_SECONDS)
        or meeting["topic"].endswith("Zoom Meeting")
    )

    for participant in all_participants:
        process_participant(participant, name_map, meeting_info, is_adhoc, result)


def get_zoom_meeting_data(headers, name_map, start_of_month, end_of_month):
    """
    Fetch Zoom meeting data for a specific date range and update the result dictionary with the data.

    Args:
        headers (dict): The request headers to use when fetching data.
        name_map (dict): A dictionary with engineer names as keys and dictionaries with other data as values.
        start_of_month (str): The start date of the date range.
        end_of_month (str): The end date of the date range.

    Returns:
        dict: The result dictionary updated with the meeting data.
    """
    print(f"Fetching meeting data for {start_of_month} to {end_of_month}...")
    params = {
        "page_size": 300,
        "from": start_of_month,
        "to": end_of_month,
        "type": "past",
    }

    result = {}
    for name in name_map:
        result[name] = {"meeting_count": 0, "hours_in_meetings": 0.0, "ad_hoc_meeting_count": 0}

    meeting_processed = 0
    while True:
        response = make_request("https://api.zoom.us/v2/metrics/meetings", headers, params)

        # check if request was successful
        if response.status_code != 200:
            print(f"Error: Received status code {response.status_code}")
            print(f"Response: {response.json()}")
            break

        meetings_data = response.json()

        for meeting in meetings_data["meetings"]:
            meeting_processed += 1
            process_meeting(meeting, headers, name_map, result)

        print(f"Processed {meeting_processed} meetings...")

        if "next_page_token" in meetings_data and meetings_data["next_page_token"]:
            params["next_page_token"] = meetings_data["next_page_token"]
        else:
            break

    return result


def get_month_index(worksheet, month_converted):
    """
    Get the index of the column with the month in a Google Sheets worksheet.

    Args:
        worksheet (gspread.models.Worksheet): The worksheet to find the column index in.
        month_converted (str): The month to find the column index of, formatted as 'Month Year'.

    Returns:
        int: The index of the column with the month.
    """
    return worksheet.row_values(1).index(month_converted) + 1


def get_user_row_index(worksheet, full_name):
    """
    Get the index of the row with the engineer's name in a Google Sheets worksheet.

    Args:
        worksheet (gspread.models.Worksheet): The worksheet to find the row index in.
        full_name (str): The name of the engineer.

    Returns:
        int: The index of the row with the engineer's name.
    """
    return worksheet.col_values(1).index(full_name) + 1


def update_google_sheet(credentials, full_name, data, sheet_name, month):
    """
    Update the Google Sheet with the new meeting data.

    Args:
        credentials (ServiceAccountCredentials): Google Sheets credentials.
        full_name (str): The full name of the engineer.
        data (int, float): The meeting data to write to the sheet.
        sheet_name (str): The name of the sheet to write the data to.
        month (str): The month the data pertains to, formatted as 'YYYY-MM'.
    """
    google = gspread.authorize(credentials)
    spreadsheet = backoff(
        google.open_by_key, args=("1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA",), max_retries=30
    )
    worksheet = backoff(spreadsheet.worksheet, args=(sheet_name,), max_retries=30)

    month_converted = datetime.strptime(month, "%Y-%m").strftime("%B %Y")

    # get the index of the column with the month
    month_col_index = backoff(get_month_index, args=(worksheet, month_converted))

    # get the index of the row with the user id
    user_row_index = backoff(get_user_row_index, args=(worksheet, full_name))
    # write the data to the cell
    backoff(
        worksheet.update_cell,
        args=(
            user_row_index,
            month_col_index,
            data,
        ),
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
    The main function that orchestrates the process of fetching Zoom meeting data for each specified month
    and writing the data to Google Sheets.
    """
    argparser = get_argument_parser()
    args = argparser.parse_args()

    google_credentials = get_google_credentials()

    name_map = get_name_map(google_credentials)

    for month in args.months:
        start_of_month, end_of_month = get_month_range(month, output_format="datetime_str")
        access_token, _ = refresh_access_tokens()  # try to not let the access token expire
        headers = {"Authorization": "Bearer " + access_token}

        meeting_data = get_zoom_meeting_data(headers, name_map, start_of_month, end_of_month)

        # Write the data to the Google Sheets
        for full_name, data in meeting_data.items():
            print(f"Updating {full_name} for {month}...")
            print(f"Data: {data}")
            update_google_sheet(google_credentials, full_name, data["meeting_count"], "Meetings", month)
            update_google_sheet(
                google_credentials, full_name, data["hours_in_meetings"], "Time in Meetings", month
            )
            update_google_sheet(
                google_credentials, full_name, data["ad_hoc_meeting_count"], "Ad-hoc Meetings", month
            )


if __name__ == "__main__":
    main()
