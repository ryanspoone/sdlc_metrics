#!/usr/bin/env python
# pylint: disable=import-error,too-many-locals

"""
This script reads multiple Google Sheets, anonymizes and summarizes the data, then sends
the summary to two users via Slack.
"""

import datetime
import os
from typing import Dict, List, Tuple, Union

import gspread
import openai
import pandas as pd
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from utilities import backoff

# Load environment variables
load_dotenv()

SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Slack users to notify
MANAGERS = {
    "Dan Greff": "U02UKASPJJ0",
    "Ryan Spoone": "U02UV65TDCZ",
    "Kyle House": "U02U2306NTZ",
}

OPT_OUT_MANAGERS = ["Kyle House", "Dan Greff"]

PREVIOUS_MONTH = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m")
MONTH_MINUS_2 = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime("%Y-%m")
MONTH_MINUS_3 = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime("%Y-%m")

# Names of the Google Sheets to read from
GOOGLE_SHEETS_CONFIG = [
    {
        "sheet_id": "1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA",
        "tabs": [
            {
                "tab_name": "Merges",
                "data_frame_name": "Merges",
            },
            {
                "tab_name": "Reviews",
                "data_frame_name": "Reviews",
            },
            {
                "tab_name": "PTO",
                "data_frame_name": "PTO",
            },
            {
                "tab_name": "IC Rollup Stats",
                "data_frame_name": "IC Rollup Stats",
            },
            {
                "tab_name": "Stories",
                "data_frame_name": "Stories",
            },
            {
                "tab_name": "Epics",
                "data_frame_name": "Epics",
            },
            {
                "tab_name": "Bugs",
                "data_frame_name": "Bugs",
            },
            {
                "tab_name": "Tasks",
                "data_frame_name": "Tasks",
            },
            {
                "tab_name": "Support Bugs",
                "data_frame_name": "Support Bugs",
            },
        ],
    },
    {
        "sheet_id": "1DQKeS1XsYytlQFFmCCUBOo_CGznCBJ6QVcdh6NqtOJA",
        "tabs": [
            {
                "tab_name": "Summary",
                "data_frame_name": "Build Failures",
            },
            {
                "tab_name": PREVIOUS_MONTH,
                "data_frame_name": f"{PREVIOUS_MONTH} Build Failures",
            },
            {
                "tab_name": MONTH_MINUS_2,
                "data_frame_name": f"{MONTH_MINUS_2} Build Failures",
            },
            {
                "tab_name": MONTH_MINUS_3,
                "data_frame_name": f"{MONTH_MINUS_3} Build Failures",
            },
        ],
    },
    {
        "sheet_id": "1f5xqsOXjS56k9NjUfZkFBHNlo4PMBKV9f_A0FHUf_1E",
        "tabs": [
            {
                "tab_name": "Data",
                "data_frame_name": "Engineering Metrics",
            },
            {
                "tab_name": "Cycle Time",
                "data_frame_name": "Cycle Time in Days",
            },
        ],
    },
]

DATA_FRAME_INDEX_TO_NAME = []

# 4000, 8000, 16000, or 32000 depending on the model
MAX_TOKENS = 4000
MODEL = "gpt-4"


def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string."""
    return len(text.split())


def get_anon_dict(sheet_id: str, sheet_name: str) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Fetches data from a Google Sheet and creates an anonymization dictionary."""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_name("google-credentials.json", scope)
    client = gspread.authorize(credentials)

    sheet = client.open_by_key(sheet_id)
    worksheet = sheet.worksheet(sheet_name)
    data = worksheet.get_all_records()  # fetch all data as a list of dictionaries

    anon_dict = {}
    deanon_dict = {}
    anon_to_manager_dict = {}

    for index, row in enumerate(data):
        if index == 0:
            continue  # Skip the header row
        ic_name = row["Engineer - IC"]
        anon_name = f"Anon{index}"
        manager = row["Manager"]
        anon_dict[ic_name] = anon_name
        deanon_dict[anon_name] = ic_name
        anon_to_manager_dict[anon_name] = manager

    return anon_dict, deanon_dict, anon_to_manager_dict


def get_google_sheets_data(
    sheets_config: List[Dict[str, Union[str, List[Dict[str, str]]]]],
    anon_dict: Dict[str, str],
) -> List[pd.DataFrame]:
    """Reads data from multiple Google Sheets and returns as a list of DataFrames."""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_name("google-credentials.json", scope)
    client = gspread.authorize(credentials)

    data_frames = []

    index = 0
    for sheet_config in sheets_config:
        sheet_id = sheet_config["sheet_id"]
        tabs = sheet_config["tabs"]
        sheet = backoff(client.open_by_key, args=(sheet_id,))
        for tab_config in tabs:
            tab_name = tab_config["tab_name"]
            data_frame_name = tab_config["data_frame_name"]
            worksheet = next((ws for ws in sheet.worksheets() if ws.title == tab_name), None)
            if worksheet:
                data = backoff(
                    worksheet.get_all_values,
                )
            else:
                raise ValueError(f"Worksheet '{tab_name}' not found in the spreadsheet.")
            sheet_data_frame = pd.DataFrame(data)
            sheet_data_frame.replace(anon_dict, inplace=True)
            DATA_FRAME_INDEX_TO_NAME.append(data_frame_name)
            data_frames.append(sheet_data_frame)
            index += 1

    return data_frames


def summarize_with_openai(summary_text: str, deanon_dict: dict = None) -> str:
    """Summarizes text using the OpenAI GPT-4 model."""

    openai.api_key = OPENAI_API_KEY

    prompt_token_count = count_tokens(summary_text)
    if prompt_token_count > MAX_TOKENS:
        print(f"TOO MANY TOKENS: {prompt_token_count}")
        return "No summary generated."

    response = openai.ChatCompletion.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an analytics assistant. "
                    "Please analyze the data frames and provide insights based on the prompts. "
                    "You respond to answers in Slack bot mrkdwn formatting. "
                    "Make the summary as concise as possible. "
                ),
            },
            {"role": "user", "content": summary_text},
        ],
        temperature=0.01,
        max_tokens=MAX_TOKENS,
    )

    summary = response.choices[0].message.content.strip()

    if deanon_dict:
        # De-anonymizing the response
        for anon_name, original_name in deanon_dict.items():
            summary = summary.replace(anon_name, original_name)

    return summary if summary else "No summary generated."


def get_data_frames_for_prompt(
    data_frames: List[pd.DataFrame], relevant_tabs: List[str]
) -> List[Dict[str, pd.DataFrame]]:
    """Retrieve relevant data frames based on the specified tabs and assign names."""

    relevant_data_frames = [data_frames[DATA_FRAME_INDEX_TO_NAME.index(tab)] for tab in relevant_tabs]
    return relevant_data_frames


def is_manager_opted_out(
    user_id: str, anon_dict: Dict[str, str], anon_to_manager_dict: Dict[str, str]
) -> bool:
    """Checks if a user's manager is in the OPT_OUT_MANAGERS list."""
    if user_id in anon_dict:
        manager = anon_to_manager_dict.get(anon_dict[user_id])
        if manager in OPT_OUT_MANAGERS:
            return True
    return False


def send_summary_to_slack(summary: str, user_id: str = ""):
    """Sends a summary to the specified user via Slack."""
    client = WebClient(token=SLACK_TOKEN)

    try:
        client.chat_postMessage(channel=user_id, text=summary, mrkdwn=True)
    except SlackApiError as error:
        print(f"Error sending message to {user_id}: {error}")


# pylint: disable-next=too-many-arguments
def generate_prompt_for_data_frames(
    prompt: str,
    relevant_data_frames: List[Dict[str, pd.DataFrame]],
    relevant_tabs: List[str],
    deanon_dict: Dict[str, str],
    anon_to_manager_dict: Dict[str, str],
    limited_to_manager: bool = False,
) -> str:
    """Generates a prompt by incorporating relevant data frames."""
    summary_prompt = f"{prompt}\n\n"
    summary_text = summary_prompt

    for index, data_frame in enumerate(relevant_data_frames):
        data_frame_name = relevant_tabs[index]  # Get the data frame name using the index
        data_frame_text = data_frame.to_csv(index=False)
        prompt_token_count = count_tokens(summary_text + data_frame_text)
        if prompt_token_count > MAX_TOKENS:
            print(f"TOO MANY TOKENS: {prompt_token_count}")
            break
        summary_text += f"{data_frame_name}:\n{data_frame_text}\n\n"

    # Iterate over the report_to_manager_dict and append direct report information to the summary prompt
    for manager, user_id in MANAGERS.items():
        if manager in OPT_OUT_MANAGERS:
            print(f"Skipping {manager}")
            continue

        if limited_to_manager:
            direct_reports = [
                anon_name
                for anon_name, manager_name in anon_to_manager_dict.items()
                if manager_name == manager
            ]
            summary_text = f"Only for these engineers: {direct_reports}\n\n" + summary_text
        summary = summarize_with_openai(summary_text, deanon_dict)
        print(f"Sending summary to {manager, user_id}")
        send_summary_to_slack(summary, user_id)


def main():
    """Main function to tie all steps together."""
    # Your anonymizing dictionary
    anon_dict, deanon_dict, anon_to_manager_dict = get_anon_dict(
        "1xREkcJwIP_iXblEkoTXoDqFWoLP7i6O2ZFdF2zn0tIA", "Aliases"
    )
    data_frames = get_google_sheets_data(GOOGLE_SHEETS_CONFIG, anon_dict)

    prompt_data = [
        {
            "prompt": (
                "Keep the response concise, 1-2 sentences, but feel free to add more details: "
                "Compare PTO, PR Merges, and PR Reviews for each engineer. "
                "Identify patterns or correlations between PTO and PR activity. "
            ),
            "relevant_tabs": [
                "PTO",
                "Merges",
                "Reviews",
                "IC Rollup Stats",
                "Stories",
                "Epics",
                "Bugs",
                "Tasks",
                "Support Bugs",
            ],
            "limited_to_manager": True,
        },
        {
            "prompt": (
                "Keep the response concise, 1-2 sentences, but feel free to add more details: "
                "Analyze main build failures by repository. "
                "Identify repositories with the highest failure rates and trends over time. "
                "Keep this high-level and focus on trends over time. "
            ),
            "relevant_tabs": [
                "Build Failures",
                f"{PREVIOUS_MONTH} Build Failures",
                f"{MONTH_MINUS_2} Build Failures",
                f"{MONTH_MINUS_3} Build Failures",
            ],
            "limited_to_manager": False,
        },
        {
            "prompt": (
                "Keep the response concise, 1-2 sentences, but feel free to add more details: "
                "Assess Jira and cost metrics for the engineering organization. "
                "Identify correlations between Jira metrics and cost metrics. "
                "Keep this high-level and focus on trends over time. "
            ),
            "relevant_tabs": ["Engineering Metrics"],
            "limited_to_manager": False,
        },
        {
            "prompt": (
                "Keep the response concise, 1-2 sentences, but feel free to add more details: "
                "Identify top-performing engineers based on PTO, PR Merges, and PR Reviews. "
                "Consider high PR merge rates, low PR review turnaround time, and low PTO days. "
            ),
            "relevant_tabs": ["PTO", "Merges", "Reviews"],
            "limited_to_manager": True,
        },
        {
            "prompt": (
                "Keep the response concise, 1-2 sentences, but feel free to add more details: "
                "Investigate the impact of build failures on engineering efficiency metrics. "
                "Analyze PR Merges, PR Reviews, and Jira metrics over time. "
                "Keep this high-level and focus on trends over time. "
            ),
            "relevant_tabs": [
                "Build Failures",
                "Merges",
                "Reviews",
                "Engineering Metrics",
                "Cycle Time in Days",
            ],
            "limited_to_manager": False,
        },
        {
            "prompt": (
                "Keep the response concise, 1-2 sentences, but feel free to add more details: "
                "Identify cost trends and correlations in the engineering organization. "
                "Analyze cost metrics over time and their relationship with PR activity and team size. "
                "Keep this high-level and focus on trends over time. "
            ),
            "relevant_tabs": ["Merges", "Build Failures", "Engineering Metrics", "Cycle Time in Days"],
            "limited_to_manager": False,
        },
    ]

    for prompt_data_item in prompt_data:
        prompt = prompt_data_item["prompt"]
        relevant_tabs = prompt_data_item["relevant_tabs"]
        limited_to_manager = prompt_data_item["limited_to_manager"]
        relevant_data_frames = get_data_frames_for_prompt(data_frames, relevant_tabs)
        generate_prompt_for_data_frames(
            prompt,
            relevant_data_frames,
            relevant_tabs,
            deanon_dict,
            anon_to_manager_dict,
            limited_to_manager,
        )


if __name__ == "__main__":
    main()
