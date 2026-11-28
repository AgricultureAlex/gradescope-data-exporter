#!/usr/bin/env python3
"""
Gradescope Student Data Exporter
Downloads your submitted PDFs (with inline feedback) and a grades CSV for
every course where you are a student.

Requires a Netscape-format cookies.txt file — see credentials.yaml for how
to export one from Chrome after logging in via SSO.

Usage:
    python export.py [-v] [--config credentials.yaml]
"""

import argparse
import contextlib
import csv
import http.cookiejar
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

BASE_URL = "https://www.gradescope.com"
REQUEST_DELAY = 0.5  # seconds between requests, to be polite


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def make_session(cookies_path: str) -> requests.Session:
    jar = http.cookiejar.MozillaCookieJar()
    try:
        jar.load(cookies_path, ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        sys.exit(f"ERROR: cookies file not found: {cookies_path}")
    except http.cookiejar.LoadError as e:
        sys.exit(f"ERROR: could not load cookies file: {e}")

    session = requests.Session()
    session.cookies = jar
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return session


def check_auth(session: requests.Session) -> str | None:
    """Return the logged-in user's name, or None if not authenticated."""
    resp = session.get(BASE_URL + "/", allow_redirects=True)
    if resp.url.startswith(BASE_URL + "/login"):
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    # Gradescope puts the account name in a header dropdown
    el = (soup.select_one("a.header--name") or
          soup.select_one("[data-react-class='FullNavigation']"))
    if el:
        # data-react-props contains JSON with the user's name
        props_str = el.get("data-react-props", "")
        m = re.search(r'"name"\s*:\s*"([^"]+)"', props_str)
        if m:
            return m.group(1)
        return el.get_text(strip=True) or "unknown"
    return "authenticated"


# ---------------------------------------------------------------------------
# Course discovery
# ---------------------------------------------------------------------------

def get_courses(session: requests.Session, verbose: bool) -> list[dict]:
    """Return list of dicts with keys: id, name, role."""
    resp = session.get(BASE_URL + "/")
    soup = BeautifulSoup(resp.text, "html.parser")
    courses = []
    seen = set()

    for a in soup.select("a.courseBox[href^='/courses/']"):
        m = re.match(r"/courses/(\d+)$", a.get("href", ""))
        if not m or m.group(1) in seen:
            continue
        cid = m.group(1)
        seen.add(cid)
        short_el = a.select_one(".courseBox--shortname")
        name_el = a.select_one(".courseBox--name")
        short = short_el.get_text(strip=True) if short_el else ""
        name = name_el.get_text(strip=True) if name_el else ""
        if not name.startswith(short):
            name = f"{short} {name}".strip()
        courses.append({"id": cid, "name": name or cid, "role": "student"})

    if verbose:
        print(f"  Found {len(courses)} course(s)")
    return courses


# ---------------------------------------------------------------------------
# Assignment discovery
# ---------------------------------------------------------------------------

def get_assignments(session: requests.Session, course_id: str, verbose: bool) -> list[dict]:
    """
    Return list of dicts:
      id, name, score, max_score, submission_id (may be None if not submitted/returned)
    """
    url = f"{BASE_URL}/courses/{course_id}"
    resp = session.get(url)
    if resp.status_code == 403:
        if verbose:
            print("    Skipping — no access to course (may be concluded)")
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    assignments = []

    for row in soup.select("tbody tr"):
        name_el = row.select_one("th.table--primaryLink")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)

        # Score
        score, max_score = "", ""
        score_el = row.select_one(".submissionStatus--score, .assignmentScore")
        if score_el:
            raw = score_el.get_text(strip=True)
            m = re.match(r"([\d.]+)\s*/\s*([\d.]+)", raw)
            if m:
                score, max_score = m.group(1), m.group(2)
            else:
                score = raw

        # Submission link → assignment and submission IDs (absent if not submitted)
        aid, sub_id = None, None
        link = name_el.select_one("a[href*='/submissions/']")
        if link:
            m = re.search(r"/assignments/(\d+)/submissions/(\d+)", link["href"])
            if m:
                aid, sub_id = m.groups()

        assignments.append({
            "id": aid,
            "name": name,
            "score": score,
            "max_score": max_score,
            "submission_id": sub_id,
        })

    return assignments


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def safe_name(s: str, max_len: int = 80) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", s)
    s = s.strip(". ")
    return s[:max_len] if s else "unnamed"


def download_pdf(session: requests.Session,
                 url: str,
                 out_path: Path,
                 verbose: bool) -> bool:
    if out_path.exists():
        if verbose:
            print(f"      Already exists, skipping: {out_path.name}")
        return True
    resp = session.get(url, stream=True)
    if not resp.ok:
        return False
    ct = resp.headers.get("content-type", "")
    if "pdf" not in ct and "octet-stream" not in ct:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=16384):
            f.write(chunk)
    return True


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def run_log(output_dir: Path):
    """Copy everything printed during this run to a new timestamped file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    # "x" mode fails rather than overwrite an earlier run's log
    with open(output_dir / f"export-log_{stamp}.txt", "x", encoding="utf-8") as log:
        with contextlib.redirect_stdout(Tee(sys.stdout, log)), \
             contextlib.redirect_stderr(Tee(sys.stderr, log)):
            print(f"Log file: {log.name}")
            try:
                yield
            # These are printed after the log closes, so record them here
            except SystemExit as e:
                log.write(f"{e}\n")
                raise
            except KeyboardInterrupt:
                log.write("Interrupted\n")
                raise
            except Exception:
                log.write(traceback.format_exc())
                raise


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def export_course(session: requests.Session,
                  course: dict,
                  output_dir: Path,
                  verbose: bool) -> None:
    cid = course["id"]
    cname = safe_name(course["name"])
    course_dir = output_dir / cname
    course_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Getting assignments")
    assignments = get_assignments(session, cid, verbose)
    if not assignments:
        print(f"    No assignments found (course may be empty or inaccessible)")
        return

    grades_rows = []
    downloaded = 0
    skipped = 0

    for asgn in assignments:
        aname = safe_name(asgn["name"])
        sub_id = asgn["submission_id"]

        grade_str = ""
        if asgn["score"] and asgn["max_score"]:
            grade_str = f"{asgn['score']} / {asgn['max_score']}"
        elif asgn["score"]:
            grade_str = asgn["score"]

        grades_rows.append({
            "Assignment": asgn["name"],
            "Score": asgn["score"],
            "Max Score": asgn["max_score"],
        })

        if not sub_id:
            if verbose:
                print(f"    {aname}: no submission on record")
            skipped += 1
            continue

        pdf_url = f"{BASE_URL}/courses/{cid}/assignments/{asgn['id']}/submissions/{sub_id}.pdf"
        out_path = course_dir / f"{aname}.pdf"

        time.sleep(REQUEST_DELAY)
        ok = download_pdf(session, pdf_url, out_path, verbose)
        if ok:
            label = grade_str or "downloaded"
            print(f"    {out_path.name}: {label}")
            downloaded += 1
        else:
            # Some assignments return HTML, not PDF (online quizzes, text submissions)
            if verbose:
                print(f"    {aname}: submission exists but PDF not available (online/text submission)")
            skipped += 1

    # Write grades CSV
    if grades_rows:
        grades_path = course_dir / "grades.csv"
        with open(grades_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["Assignment", "Score", "Max Score"])
            writer.writeheader()
            writer.writerows(grades_rows)

    print(f"  → {downloaded} PDF(s) saved, {skipped} skipped, grades.csv written")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Gradescope Student Data Exporter")
    parser.add_argument("--config", default="credentials.yaml",
                        help="Path to credentials YAML (default: credentials.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show extra detail")
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        sys.exit(f"ERROR: config file not found: {config_path}")
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    cookies_path = config.get("COOKIES_PATH", "./cookies.txt")
    output_dir = Path(config.get("OUTPUT_DIR", "./output"))
    skip_ids = set(str(x).strip() for x in (config.get("COURSES_TO_SKIP") or []))

    with run_log(output_dir):
        run_export(cookies_path, output_dir, skip_ids, args.verbose)


def run_export(cookies_path: str, output_dir: Path, skip_ids: set, verbose: bool) -> None:
    print("Welcome to the Gradescope Student Data Exporter\n")

    # Auth
    print("Connecting to Gradescope…")
    session = make_session(cookies_path)
    username = check_auth(session)
    if not username:
        sys.exit(
            "ERROR: Not authenticated. Your cookies may have expired.\n"
            "Log into gradescope.com in Chrome, re-export cookies.txt, and try again."
        )
    print(f"Authenticated as: {username}")
    print(f"Output directory: {output_dir}\n")

    # Courses
    print("Getting list of courses…")
    courses = get_courses(session, verbose)
    if not courses:
        sys.exit("No courses found. Try logging in again and re-exporting cookies.")

    total = len(courses)
    for i, course in enumerate(courses, 1):
        if course["id"] in skip_ids:
            print(f"[{i}/{total}] Skipping {course['name']} (in COURSES_TO_SKIP)")
            continue
        print(f"\n[{i}/{total}] {course['name']}  (id: {course['id']})")
        time.sleep(REQUEST_DELAY)
        export_course(session, course, output_dir, verbose)

    print("\nDone.")


if __name__ == "__main__":
    main()
