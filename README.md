# SDLC Metrics

This repository contains a collection of scripts to gather and analyze various
Software Development Lifecycle (SDLC) metrics. The main goal of these tools is
to provide actionable data that assists in improving management decisions and
overall productivity.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Prerequisites](#prerequisites)
3. [Setup](#setup)
4. [Scripts](#scripts)

## Getting Started

To get a local copy of the repository, run the following commands in your terminal:

```bash
git clone https://github.com/ryanspoone/sdlc_metrics.git
cd sdlc_metrics
```

## Prerequisites

The scripts require the following tools to run:

+ [GitHub CLI](https://cli.github.com/), `gh`
+ Node.js and `npm` (for the JavaScript scripts)
+ Python 3 (for the Python scripts)
+ Bash (for the Shell scripts)

Please ensure these are installed on your system before proceeding.

## Setup

### Node.js Setup

To install the required Node.js dependencies, run the following command:

```bash
npm install
```

### Python Setup

Install the Python dependencies by running the following command:

```bash
pip install --no-cache-dir -r requirements.txt
```

### Bash/Shell Setup

Make sure you have the right permissions to execute the bash scripts. If not,
you can use the following command to make them executable:

```bash
chmod u+x **/*.sh
```

#### Google Service Account Setup

Setting up a Google Service Account involves the following steps:

1. **Go to the Google Cloud Console**: Visit the
  [Google Cloud Console](https://console.cloud.google.com/). You need
  to be logged in with your Google account.
2. **Create a new project**: If you don't have a project already, create a new one
  by clicking on the project drop-down and selecting `New Project`. Provide a
  name for the project and click `Create`.
3. **Enable APIs**: Enable the Google Sheets and Google Calendar APIs. You can do
  this by clicking Library in the left-hand side menu, searching for
  `Google Sheets API` and `Google Calendar API`, clicking on each one, and
  clicking `Enable`.
4. **Create a Service Account**: Click on `IAM & Admin > Service Accounts` in the
   left-hand side menu. Click `Create Service Account`. Give it a name and a
   description and click `Create`.
5. **Assign Roles**: On the next screen, you'll need to assign roles to your
   service account. For this script, you'll need to assign `Project > Editor`
   so that the service account has access to view and modify data in your
   project.
6. **Create Key**: Click `Continue` and then `+ Create Key` on the next page.
   Choose `JSON` and click `Create`. This will automatically download a JSON
   file containing your service account credentials.
7. **Rename the JSON key file**: Rename this downloaded JSON file to
   `google-credentials.json` and place it in your project's root directory.

#### Giving Spreadsheet Permissions

To give the service account access to a specific Google Spreadsheet, do the following:

1. **Get Service Account Email**: Open the `google-credentials.json` file and find
   the `client_email` field. Copy the email address.
2. **Share the Google Spreadsheet**: Open the Google Spreadsheet you want to interact
   with. Click on `Share` and then paste the copied service account email into the
   field where it says `Add people and groups`. Ensure that the service account has
   `Editor` access to the spreadsheet.

Remember the `SPREADSHEET_ID` is in the Python script.

For the Google Calendar, you have to share the specific calendar with the service
account email and give it `See all event details` permission. You can find this
option under `Settings and sharing` of your Google Calendar. Again, remember the
`CALENDAR_ID` is in the script.

## Scripts

### get_ticket_info.py

### github_to_gsheet.py

This Python script fetches PR (Pull Request) data from Github and updates the
Google Sheets document with Merges, Reviews, and Code Changes information. It
uses caching to minimize API calls and takes care of rate limits by Github.
This script is particularly useful for organizations to track their open source
contributions.

**Usage**:

```bash
python scripts/github_to_gsheet.py [--months YYYY-MM YYYY-MM ...]
```

By default, the script will fetch data for the previous month. You can specify
different months with the --months argument, where each month is in the "YYYY-MM"
format.

**Options**:

+ `--months`: List of months in the "YYYY-MM" format for which to fetch the
  Github data. By default, it fetches data for the previous month.

**Example**:

Let's say you want to fetch the Github PR data for June and July of 2023:

```bash
python scripts/github_to_gsheet.py --months 2023-06 2023-07
```

### jira_cycle_time_to_gsheet.py

This Python script automatically updates the Jira sheets in a Google Sheets
document. The main purpose of the script is to fetch Jira issues based on
provided configurations and calculate their cycle time (from one stage to
another). This data is then updated in a Google Sheets document.

**Usage**:

```bash
python scripts/jira_cycle_time_to_gsheet.py [--months YYYY-MM YYYY-MM ...]
```

By default, the script will fetch data for the previous month. You can specify
different months with the --months argument, where each month is in the "YYYY-MM"
format.

**Options**:

+ `--months`: Specifies the months for which you want to fetch data from Jira.
  The months should be passed in a space-separated format, and each month should
  be in "YYYY-MM" format.

**Example**:

For example, to fetch data for January and February 2023, use:

```bash
python scripts/jira_cycle_time_to_gsheet.py --months 2023-01 2023-02
```

### jira_ic_to_gsheet.py

Automatically update the Jira sheets in the Google Sheets document. The script
fetches data from your Jira board and syncs it with the respective Google Sheets
based on issue type (Story, Epic, Bug, Task, Support Bug).

**Usage**:

```bash
python scripts/jira_ic_to_gsheet.py [--months YYYY-MM YYYY-MM ...]
```

**Options**:

+ `--months`: List of months in the "YYYY-MM" format for which the data needs
to be fetched. If not provided, the data for the previous month is fetched.

**Example**:

To get the data for May and June of 2023:

```bash
python scripts/jira_ic_to_gsheet.py --months 2023-05 2023-06
```

### metrics_summary_to_slack.py

This script reads data from multiple Google Sheets, anonymizes the data,
summarizes it using OpenAI's GPT-4, and sends the summary to specified users
via Slack. This can be useful for businesses that need to share data insights
without exposing sensitive information. The script is customizable and can
handle various data formats across different sheets.

**Usage**:

```bash
python metrics_summary_to_slack.py
```

### pto_to_gsheet.py

This script calculates the number of out-of-office days for each engineer in a
specified month. It fetches data from Google Calendar and a Google Spreadsheet,
calculates the out-of-office days, and then updates the Google Spreadsheet with
the new data.

**Usage**:

```bash
python scripts/pto_to_gsheet.py [--months YYYY-MM YYYY-MM ...]
```

**Options**:

+ `--months`: List of months in the format "YYYY-MM". Default is the previous month.

**Example**:

To fetch and update out-of-office days for the months of June and July 2023, use:

```bash
python out_of_office.py --months 2023-06 2023-07
```

### semaphoreci_to_gsheet.py

The SemaphoreCI Stats is a Python script that fetches data for Semaphore CI
projects and generates a detailed report of pipeline successes and failures within
a specified date range. It uses a Semaphore CI API Token, fetched from an environment
variable, to access the API data. The final data is written to a CSV file or Google
Sheets based on the configurations provided.

**Usage**:

```bash
python scripts/semaphoreci_to_gsheet.py [--month YYYY-MM] [--main-branch-only]
```

**Options**:

+ `--month`: Defines the month for which to sync, in the format YYYY-MM. If not
  provided, it will default to the last month.
+ `--main-branch-only`: If this argument is passed, the script will retrieve
  results only for the 'main' branch. By default, this is set to True.
+ `--csv`: If this argument is passed, the script will write results
   in a CSV. By default, this is unset and writes to Google Sheets.

**Example**:

To fetch data for all Semaphore CI projects for the month of January 2023
on all branches and write to a CSV file, run:

```bash
python scripts/semaphoreci_to_gsheet.py --month 2023-01 --main-branch-only --csv
```

To fetch data for all Semaphore CI projects for the previous month, for
only the main branch, and write to Google Sheets, run it with no options:

```bash
python scripts/semaphoreci_to_gsheet.py
```

---

Remember to keep your `.env` file updated with the necessary environment
variables for these scripts to run correctly. A sample `.env.example` is
included to get you started.

Remember to secure your `google-credentials.json` file properly as it
contains sensitive data that can allow access to your Google Sheets documents.

For more details on how to configure your workspace, refer to the VS Code
configuration files (`./.vscode/extensions.json`, `./.vscode/settings.json`).
