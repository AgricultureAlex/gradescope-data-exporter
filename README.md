# Gradescope Student Data Exporter

Just graduated?  This tool exports your Gradescope assignments.

It downloads your submitted PDFs (with inline grader feedback) and a grades CSV for every Gradescope course you're enrolled in.

Works with SSO logins (Microsoft, Google, etc.) via browser cookies.

## Setup

```bash
pip install -r requirements.txt
```

## Export your cookies

1. Install **[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** in Chrome
2. Log into [gradescope.com](https://www.gradescope.com) via your school SSO
3. Click the extension icon → select **gradescope.com** → **Export**
4. Save the file as `cookies.txt` in this directory

## Run

```bash
python export.py
```

Add `-v` for verbose output.

## Output

```
output/
  export-log_<timestamp>.txt    ← console output of one run (a new file each time)
  Course Name/
    Assignment 1.pdf                  ← graded PDF of your submission
    Assignment 1 (original).pdf       ← the file you uploaded
    Assignment 1 (code).zip           ← code submissions
    Assignment 1 (template).pdf       ← blank assignment, if Gradescope provides it
    Assignment 1 (details).md         ← questions, online answers, rubric, comments, autograder results
    Assignment 1 (linked files)/      ← Gradescope-hosted files referenced in those details
    Assignment 2.pdf
    grades.csv          ← all assignment scores for the course
```

Each item is saved only when Gradescope offers it. Files that already exist are not downloaded or overwritten, so delete a file to fetch it again (`grades.csv` is rewritten on every run). External links are listed in the details file but not downloaded. Assignments with no submission appear only in `grades.csv`.

## Config

Edit `credentials.yaml` to change the cookies path, output directory, or skip specific course IDs.
