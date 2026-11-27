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
import csv
import http.cookiejar
import os
import re
import sys
import time
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

    # Try the React props blob first (most reliable across Gradescope versions)
    nav = soup.select_one("[data-react-class='FullNavigation']")
    if nav:
        props_str = nav.get("data-react-props", "")
        # Extract courses from JSON blob — look for id/name pairs near "courses"
        for m in re.finditer(r'"id"\s*:\s*(\d+).*?"name"\s*:\s*"([^"]+)"', props_str):
            cid, name = m.group(1), m.group(2)
            if cid not in seen:
                seen.add(cid)
                courses.append({"id": cid, "name": name, "role": "student"})

    # Fallback: scrape course box links from the dashboard HTML
    if not courses:
        for a in soup.select("a[href^='/courses/']"):
            m = re.match(r"/courses/(\d+)$", a.get("href", ""))
            if not m:
                continue
            cid = m.group(1)
            if cid in seen:
                continue
            seen.add(cid)
            name = (a.get_text(strip=True) or
                    a.get("aria-label", "") or
                    cid)
            courses.append({"id": cid, "name": name, "role": "student"})

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
    url = f"{BASE_URL}/courses/{course_id}/assignments"
    resp = session.get(url)
    if resp.status_code == 403:
        if verbose:
            print("    Skipping — no access to assignment list (course may be concluded)")
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    assignments = []
    seen_ids = set()

    for row in soup.select("tr[data-assignment-id]"):
        aid = row.get("data-assignment-id", "").strip()
        if not aid or aid in seen_ids:
            continue
        seen_ids.add(aid)

        # Name
        name_el = (row.select_one(".js-assignmentTableAssignmentName") or
                   row.select_one("td.submissionStatus--assignmentName") or
                   row.select_one("td:first-child"))
        name = name_el.get_text(strip=True) if name_el else aid

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

        # Submission link → submission ID
        sub_id = None
        for a in row.select("a[href*='/submissions/']"):
            m = re.search(r"/submissions/(\d+)", a["href"])
            if m:
                sub_id = m.group(1)
                break

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
            print(f"    {aname}: {label}")
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
    output_dir.mkdir(parents=True, exist_ok=True)

    # Courses
    print("Getting list of courses…")
    courses = get_courses(session, args.verbose)
    if not courses:
        sys.exit("No courses found. Try logging in again and re-exporting cookies.")

    total = len(courses)
    for i, course in enumerate(courses, 1):
        if course["id"] in skip_ids:
            print(f"[{i}/{total}] Skipping {course['name']} (in COURSES_TO_SKIP)")
            continue
        print(f"\n[{i}/{total}] {course['name']}  (id: {course['id']})")
        time.sleep(REQUEST_DELAY)
        export_course(session, course, output_dir, args.verbose)

    print("\nDone.")


if __name__ == "__main__":
    main()
