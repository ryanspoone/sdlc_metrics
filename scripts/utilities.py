"""Common utility functions for the project."""
# pylint: disable=broad-exception-caught,inconsistent-return-statements,too-many-arguments

import calendar
import random
import re
import time
from datetime import datetime, timedelta


def get_previous_month():
    """
    Returns the previous month in the format "YYYY-MM"
    """
    last_month = datetime.now() - timedelta(days=30)
    return last_month.strftime("%Y-%m")


def backoff(function, args=None, kwargs=None, sleep_time=1, max_retries=15, exceptions=Exception):
    """
    Call a function with exponential backoff.

    This function attempts to call the provided function with the given arguments.
    If the function raises an exception, this function sleeps for an exponentially
    increasing amount of time (plus a random jitter) and then tries again, up to a
    maximum number of retries.

    :param function: The function to call.
    :param args: A tuple of arguments to pass to the function.
    :param kwargs: A dictionary of keyword arguments to pass to the function.
    :param sleep_time: The initial sleep time in seconds if the function call fails.
    :param max_retries: The maximum number of times to retry the function call.
    :param exceptions: An exception or a tuple of exceptions to catch. Default is Exception which catches all.
    :raises: The exception raised by the function if the maximum number of retries is reached.
    """
    args = args or ()
    kwargs = kwargs or {}
    for retry_count in range(max_retries):
        try:
            return function(*args, **kwargs)
        except exceptions as error:
            if retry_count >= max_retries - 1:
                raise
            backoff_time = ((2**retry_count) * sleep_time) + random.uniform(0, 1)
            print(f"Encountered an error: {error}. Retrying in {backoff_time} seconds...")
            time.sleep(backoff_time)


def get_month_range(date_input=None, output_format="timestamp"):
    """
    Calculate the first and last second of the given month, or the previous month if None.
    Return the dates in the desired format.

    Args:
        date_input (str, datetime.date, optional): The date of the month to calculate the range for.
            If None, the range for the previous month will be calculated.
            If str, it should be in the 'YYYY-MM' format.
        output_format (str, optional): The desired output format. Can be 'timestamp', 'date',
            'datetime_str', or 'datetime'. Defaults to 'timestamp'.

    Returns:
        tuple: The start and end of the month in the desired format. The end will be inclusive of the last
               second of the last day if `output_format` is 'timestamp'.
    """
    if date_input is None:
        date_input = get_previous_month()
    elif isinstance(date_input, str):
        try:
            year, month = map(int, date_input.split("-"))
            date_input = datetime(year, month, 1)
        except Exception as error:
            raise ValueError("Invalid date_string. Should be in 'YYYY-MM' format.") from error
    elif not isinstance(date_input, datetime):
        raise TypeError(
            "date_input must be a datetime.datetime instance, a string in YYYY-MM format, or None."
        )

    year, month = date_input.year, date_input.month

    # Calculate the first and last day of the month
    first_day_of_month = datetime(year, month, 1)
    last_day_of_month = datetime(year, month, calendar.monthrange(year, month)[1])

    if output_format == "timestamp":
        first_day_of_month = first_day_of_month.replace(hour=0, minute=0, second=0)
        last_day_of_month = last_day_of_month.replace(hour=23, minute=59, second=59)
        start_of_month = int(first_day_of_month.timestamp())
        end_of_month = int(last_day_of_month.timestamp())
    elif output_format == "date":
        start_of_month = first_day_of_month.date()
        end_of_month = last_day_of_month.date()
    elif output_format == "datetime":
        start_of_month = first_day_of_month
        end_of_month = last_day_of_month
    elif output_format == "datetime_str":
        start_of_month = first_day_of_month.strftime("%Y-%m-%d")
        end_of_month = last_day_of_month.strftime("%Y-%m-%d")
    else:
        raise ValueError("Invalid output_format. Choose from 'timestamp', 'date', 'datetime'.")

    return start_of_month, end_of_month


def to_snake_case(value):
    """
    Converts a string to snake_case.

    :param s: A string.
    :return: The string in snake_case.
    """
    value = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", value).lower()
