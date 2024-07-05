#!/usr/bin/env python
# pylint: disable=import-error,too-many-locals,duplicate-code,no-member,too-many-nested-blocks

"""
This script calculates the number of out-of-office days for each engineer in a specified month.
It fetches data from Google Calendar and a Google Spreadsheet, calculates the out-of-office days,
and then updates the Google Spreadsheet with the new data.
"""

import argparse
import datetime
import re
from datetime import timedelta

import gspread
import pandas as pd
import utilities
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Google Calendar API credentials
SERVICE_ACCOUNT_FILE = "google-credentials.json"
CALENDAR_ID = "c_ghpgu5aa00ik67d64asmi6al90@group.calendar.google.com"
HOLIDAYS_CALENDAR_ID = "example.com_gqrdui0pavord75cb0jr5aeub8@group.calendar.google.com"

# Google Spreadsheet credentials
SPREADSHEET_ID = "1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA"
PTO_SHEET_NAME = "PTO"
ALIASES_SHEET_NAME = "Aliases"
HOLIDAYS_SHEET_NAME = "Company Holidays"


def get_holiday_days(month):
    """
    Fetch company holiday events from Google Calendar for a specified month and count the number of days.

    :param month: a string representing the month in 'YYYY-MM' format
    :return: a dictionary mapping each holiday's name to its date in the specified month
    """

    # Authenticate with Google Calendar API
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
    )
    service = build("calendar", "v3", credentials=credentials)

    start_date, end_date = utilities.get_month_range(month, output_format="datetime")
    start_date = start_date.isoformat() + "Z"
    end_date = end_date.isoformat() + "Z"
    events_result = (
        service.events()
        .list(
            calendarId=HOLIDAYS_CALENDAR_ID,
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy="startTime",
            timeZone="UTC",
        )
        .execute()
    )

    events = utilities.backoff(events_result.get, args=("items", []))

    # Create a dictionary to store the holiday days
    holiday_days = {}

    # Iterate over the events and add the holiday days
    for event in events:
        start = event["start"].get("date") or event["start"].get("dateTime")

        # Check if the summary contains 'Company Holiday' or 'US Holiday'
        if "summary" in event and (
            "Company Holiday" in event["summary"]
            or ("US" in event["summary"] and "Holiday" in event["summary"])
        ):
            holiday = event["summary"]
            start_date = datetime.datetime.fromisoformat(start[:10])
            holiday_days[holiday] = start_date.strftime(
                "%d-%m-%Y"
            )  # assuming you want the date in 'DD-MM-YYYY' format
    return holiday_days


def get_out_of_office_days(month):
    """
    Fetch out of office events from Google Calendar for a specified month and count the number of days for
    each engineer.

    :param month: a string representing the month in 'YYYY-MM' format
    :return: a dictionary mapping each engineer's name to their number of out-of-office days in the
             specified month
    """

    # Authenticate with Google Calendar API
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/calendar.readonly"]
    )
    service = build("calendar", "v3", credentials=credentials)

    # Retrieve events from the specified calendar for the given month

    start_of_month, end_of_month = utilities.get_month_range(month, output_format="datetime")
    start_date = start_of_month.isoformat() + "Z"
    end_date = end_of_month.isoformat() + "Z"

    events_result = (
        service.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy="startTime",
            timeZone="UTC",
        )
        .execute()
    )

    events = utilities.backoff(events_result.get, args=("items", []))

    # Get the list of engineers from the "Aliases" sheet
    engineers = get_engineers_from_aliases_sheet()

    # Create a dictionary to store out of office days for each engineer, initialized with zeros
    out_of_office_days = {engineer: 0 for engineer in engineers}

    # Iterate over the events and count the out of office days for each engineer
    for event in events:
        start = event["start"].get("date") or event["start"].get("dateTime")
        end = event["end"].get("date") or event["end"].get("dateTime")

        # Check if the summary contains an engineer's name and 'out of office'
        if "summary" in event:
            match = re.match(r"(.*) - Out of office", event["summary"], re.I)
            if match:
                engineer = match.group(1)
                if engineer.lower() in [e.lower() for e in engineers]:
                    start_date = datetime.datetime.fromisoformat(start[:10])
                    end_date = datetime.datetime.fromisoformat(end[:10])
                    days = 0

                    # Adjust start_date to the first day of the current month if the event
                    # starts before the current month
                    start_date = max(start_date, start_of_month)

                    while start_date < end_date:
                        if start_date.weekday() < 5:  # 0-4 denotes Monday to Friday
                            days += 1
                        start_date += timedelta(days=1)

                    out_of_office_days[engineer] += days
    return out_of_office_days


def get_engineers_from_aliases_sheet():
    """
    Fetch engineer names from the Aliases sheet in the specified Google Spreadsheet.

    :return: a list of engineers' names
    """
    # Authenticate with Google Spreadsheet API
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    google = gspread.authorize(credentials)
    spreadsheet = google.open_by_key(SPREADSHEET_ID)
    aliases_sheet = spreadsheet.worksheet(ALIASES_SHEET_NAME)

    # Read the engineers' names from the "Aliases" sheet
    data = aliases_sheet.get_all_values()
    try:
        headers = data[0]
        engineer_index = headers.index("Engineer - IC")
    except (IndexError, ValueError):
        print("Error: Could not find 'Engineer - IC' column in the Aliases sheet.")
        return []

    engineers = [row[engineer_index] for row in data[1:] if row[engineer_index].strip() != ""]

    return engineers


def update_holidays_sheet(month, holiday_days):
    """
    Update the specified Google Spreadsheet with the company and US holidays for a specific month.

    :param month: a string representing the month in 'YYYY-MM' format
    :param holiday_days: a dictionary mapping each holiday's name to its date
    """
    # Convert 'YYYY-MM' to 'Month YYYY'
    month_str = datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")

    # Authenticate with Google Spreadsheet API
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    google = gspread.authorize(credentials)
    spreadsheet = google.open_by_key(SPREADSHEET_ID)
    holidays_sheet = spreadsheet.worksheet(HOLIDAYS_SHEET_NAME)

    # Read existing data from the Holiday sheet
    data = holidays_sheet.get_all_values()
    headers = data[0]

    # Check if month_str already exists in the headers
    if month_str not in headers:
        # Insert a new column to the right of the last month
        holidays_sheet.insert_cols(len(headers) + 1)  # 1-indexed
        holidays_sheet.update_cell(1, len(headers) + 1, month_str)
        headers.append(month_str)

    # Create a new DataFrame with the updated out of office days
    dataframe = pd.DataFrame(data[1:], columns=headers)

    for idx, row in dataframe.iterrows():
        if row["Country"] == "US":
            cell_value = len(holiday_days.values())
            row[month_str] = cell_value
            # Convert the index to start from 2 (for spreadsheet indexing)
            # idx is zero-based so adding 2 will start it from 2 (as in spreadsheet)
            cell_row = idx + 2
            cell_col = headers.index(month_str) + 1
            utilities.backoff(holidays_sheet.update_cell, args=(cell_row, cell_col, cell_value))

    print(f"Updated the Google Spreadsheet with US holidays for {month}.")


def update_pto_sheet(month, out_of_office_days):
    """
    Update the specified Google Spreadsheet with the out of office days for each
    engineer for a specific month.

    :param month: a string representing the month in 'YYYY-MM' format
    :param out_of_office_days: a dictionary mapping each engineer's name to their
                               number of out-of-office days
    """
    # Convert 'YYYY-MM' to 'Month YYYY'
    month_str = datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")

    # Authenticate with Google Spreadsheet API
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    google = gspread.authorize(credentials)
    spreadsheet = google.open_by_key(SPREADSHEET_ID)
    pto_sheet = spreadsheet.worksheet(PTO_SHEET_NAME)

    # Read existing data from the PTO sheet
    data = pto_sheet.get_all_values()
    headers = data[0]

    # Check if month_str already exists in the headers
    if month_str not in headers:
        # Insert a new column to the right of 'Engineer - IC'
        engineer_ic_index = headers.index("Engineer - IC")
        pto_sheet.insert_cols(engineer_ic_index + 2)  # 1-indexed
        pto_sheet.update_cell(1, engineer_ic_index + 2, month_str)
        headers.insert(engineer_ic_index + 1, month_str)

    # Create a new DataFrame with the updated out of office days
    dataframe = pd.DataFrame(data[1:], columns=headers)
    for engineer, days in out_of_office_days.items():
        dataframe.loc[dataframe["Engineer - IC"].str.lower() == engineer.lower(), month_str] = days

    # Replace NaNs with zero
    dataframe = dataframe.fillna(0)

    # Enforce the column to be of integer type
    # pylint: disable-next=unsupported-assignment-operation,unsubscriptable-object
    dataframe[month_str] = dataframe[month_str].astype(int)

    for engineer, days in out_of_office_days.items():
        # pylint: disable-next=unsupported-assignment-operation,unsubscriptable-object
        mask = dataframe["Engineer - IC"].str.lower() == engineer.lower()
        # pylint: disable-next=unsupported-assignment-operation,unsubscriptable-object
        dataframe.loc[mask, month_str] = days

        for idx, _ in dataframe.loc[mask].iterrows():
            cell_value = days
            cell_row = idx + 2  # Convert the index to start from 2 (for spreadsheet indexing)
            cell_col = headers.index(month_str) + 1
            utilities.backoff(pto_sheet.update_cell, args=(cell_row, cell_col, cell_value))

    print(f"Updated the Google Spreadsheet with out of office days for {month}.")


def get_argument_parser():
    """
    Create an ArgumentParser object to handle command-line arguments to the script.

    :return: an ArgumentParser object with the command-line options for the script
    """
    parser = argparse.ArgumentParser(description="Fetch out of office days from Google Calendar.")
    parser.add_argument(
        "--months",
        nargs="*",
        default=[(datetime.datetime.now() - timedelta(days=30)).strftime("%Y-%m")],
        help='List of months in the format "YYYY-MM". Default is the previous month.',
    )
    return parser


def main():
    """Main function."""
    argparser = get_argument_parser()
    args = argparser.parse_args()

    for arg_month in args.months:
        print(f"Processing month: {arg_month}")

        out_of_offices = get_out_of_office_days(arg_month)
        print(f"Out of Office Days: {out_of_offices}")
        update_pto_sheet(arg_month, out_of_offices)

        holiday_days = get_holiday_days(arg_month)
        print(f"Holiday Days: {holiday_days}")
        update_holidays_sheet(arg_month, holiday_days)

        print(f"Completed processing month: {arg_month}")


# Main script execution
if __name__ == "__main__":
    main()
