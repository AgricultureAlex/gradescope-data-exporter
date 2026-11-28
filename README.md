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
    Assignment 1.pdf    ← your submission with grader annotations
    Assignment 2.pdf
    grades.csv          ← all assignment scores for the course
```

PDFs are skipped (noted in output) when the submission was a text entry or online quiz — Gradescope doesn't expose those as downloadable PDFs for students.

## Config

Edit `credentials.yaml` to change the cookies path, output directory, or skip specific course IDs.
