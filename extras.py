"""Everything on a submission page beyond the graded PDF.

Only whitelisted fields are written out: the page data also holds other
people's details and content that Gradescope does not show to students.
"""

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

FILE_LINK = re.compile(r"(?:https://www\.gradescope\.com)?(?<![\w.])/files/[0-9a-fA-F-]{16,}")
WEB_LINK = re.compile(r"https?://[^\s\"'<>()\[\]`]+")


def get_viewer_data(session: requests.Session, url: str) -> dict | None:
    resp = session.get(url)
    if not resp.ok:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    viewer = soup.select_one("[data-react-class='AssignmentSubmissionViewer']")
    return json.loads(viewer["data-react-props"]) if viewer else None


def download_file(session: requests.Session, url: str, out_path: Path) -> bool:
    """Save url to out_path. Error messages omit the URL, which may be signed."""
    part = out_path.with_name(out_path.name + ".part")
    try:
        with session.get(url, stream=True, timeout=120) as resp:
            if not resp.ok or "text/html" in resp.headers.get("content-type", ""):
                return False
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=16384):
                    f.write(chunk)
    except requests.RequestException:
        part.unlink(missing_ok=True)
        return False
    part.replace(out_path)
    return True


def attached_files(data: dict, base_url: str, name: str) -> list[tuple[str, str]]:
    """Return (file name, url) for the original upload, code ZIP and template."""
    files = []
    original = (data.get("pdf_attachment") or {}).get("url")
    if original:
        files.append((f"{name} (original){_suffix(original)}", original))
    zip_path = data.get("paths", {}).get("submission_zip_path")
    if zip_path:
        files.append((f"{name} (code).zip", base_url + zip_path))
    template = data.get("assignment", {}).get("template_url")
    if template:
        files.append((f"{name} (template){_suffix(template)}", template))
    return files


def _suffix(url: str) -> str:
    return Path(urlparse(url).path).suffix or ".pdf"


def download_linked_file(session: requests.Session, url: str, folder: Path) -> str:
    """Download a Gradescope /files/ link. Return 'saved', 'exists' or 'failed'."""
    file_id = url.rstrip("/").rsplit("/", 1)[-1]
    if any(p.suffix != ".part" for p in folder.glob(f"{file_id}*")):
        return "exists"
    try:
        head = session.get(url, stream=True, timeout=120)
        head.close()
    except requests.RequestException:
        return "failed"
    content_type = head.headers.get("content-type", "").split(";")[0]
    suffix = Path(urlparse(head.url).path).suffix or mimetypes.guess_extension(content_type) or ""
    return "saved" if download_file(session, url, folder / (file_id + suffix)) else "failed"


# ---------------------------------------------------------------------------
# Details file
# ---------------------------------------------------------------------------

def _block(value) -> str:
    """Fence free text so that it cannot disturb the surrounding Markdown."""
    text = str(value).rstrip()
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _rubric_lines(rubric: list[dict], groups: dict, show_all: bool) -> list[str]:
    shown = [r for r in rubric if show_all or r.get("present")]
    if not shown:
        return []
    lines = ["Rubric:" if show_all else "Applied rubric items:", ""]
    last_group = None
    for item in shown:
        group_id = item.get("group_id")
        if group_id != last_group and group_id in groups:
            lines.append(f"- Group: {groups[group_id].get('description', '')}")
        last_group = group_id
        mark = ("[x] " if item.get("present") else "[ ] ") if show_all else ""
        lines.append(f"- {mark}({item.get('weight')} pts) {item.get('description', '')}")
    return lines + [""]


def _question_lines(question: dict, response: dict, rubric_lines: list[str]) -> list[str]:
    title = question.get("title") or ""
    lines = [f"## {question.get('full_index', '')} {title}".rstrip(), ""]
    score = response.get("score")
    if score is not None:
        lines += [f"Score: {score} / {question.get('weight')}", ""]

    answers = response.get("answers") or {}
    input_index = 0
    for item in question.get("content") or []:
        kind = item.get("type", "")
        if kind == "text":
            lines += [item.get("value", ""), ""]
            continue
        # Copy only the choice text; other choice fields may reveal the answer key
        for choice in item.get("choices") or []:
            lines.append(f"- {choice.get('value', '')}")
        if kind.endswith("_input"):
            answer = answers.get(str(input_index))
            input_index += 1
            if answer not in (None, "", []):
                if isinstance(answer, list):
                    answer = "\n".join(str(a) for a in answer)
                lines += ["", "Your answer:", "", _block(answer), ""]

    lines += rubric_lines
    for evaluation in response.get("evaluations") or []:
        if evaluation.get("comments"):
            lines += ["Grader comment:", "", _block(evaluation["comments"]), ""]
    for note in response.get("annotations") or []:
        if note.get("content"):
            page = note.get("page_number")
            lines += [f"Annotation (page {page}):", "", _block(note["content"]), ""]
    return lines


def _autograder_lines(results: dict) -> list[str]:
    lines = ["## Autograder results", ""]
    if results.get("score") is not None:
        lines += [f"Score: {results['score']}", ""]
    if results.get("output"):
        lines += [_block(results["output"]), ""]
    if results.get("stdout_shown_to_students") and results.get("stdout"):
        lines += ["Standard output:", "", _block(results["stdout"]), ""]
    for test in results.get("tests") or []:
        heading = f"### {test.get('name') or 'Test'}"
        if test.get("score") is not None:
            heading += f" ({test['score']})"
        lines += [heading, ""]
        if test.get("output"):
            lines += [_block(test["output"]), ""]
    return lines


def render_details(data: dict, name: str) -> tuple[str, list[str]]:
    """Return the Markdown text and the Gradescope-hosted file links in it."""
    responses = {r["question_id"]: r for r in data.get("question_submissions") or []}
    rubric = sorted(data.get("rubric_items") or [], key=lambda r: r.get("position") or 0)
    groups = {g["id"]: g for g in data.get("rubric_item_groups") or []}
    visibility = data.get("assignment", {}).get("rubric_visibility_setting")
    show_all = visibility == "show_all_rubric_items"

    lines = [f"# {name}", ""]
    for question in data.get("questions") or []:
        lines += _question_lines(
            question,
            responses.get(question["id"], {}),
            _rubric_lines([r for r in rubric if r.get("question_id") == question["id"]],
                          groups, show_all),
        )
    if data.get("autograder_results"):
        lines += _autograder_lines(data["autograder_results"])
    text = "\n".join(lines)

    web_links = sorted({u.rstrip(".,;:") for u in WEB_LINK.findall(text)})
    if web_links:
        text += "\n\n## Links\n\n" + "\n".join(f"- <{u}>" for u in web_links) + "\n"

    return text, sorted(set(FILE_LINK.findall(text)))
