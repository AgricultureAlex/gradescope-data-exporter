// Page parsing mirrors export.py; fix layout changes in both.
export const BASE_URL = "https://www.gradescope.com";

export class NotLoggedInError extends Error {}

async function fetchPage(path) {
  const resp = await fetch(BASE_URL + path, { credentials: "include" });
  if (new URL(resp.url).pathname.startsWith("/login")) {
    throw new NotLoggedInError();
  }
  if (!resp.ok) {
    throw new Error(`Gradescope returned HTTP ${resp.status}`);
  }
  return new DOMParser().parseFromString(await resp.text(), "text/html");
}

const text = (el) => el?.textContent.trim() ?? "";

export async function getCourses() {
  const doc = await fetchPage("/");
  const courses = new Map();
  for (const box of doc.querySelectorAll("a.courseBox[href^='/courses/']")) {
    const id = box.getAttribute("href").match(/^\/courses\/(\d+)$/)?.[1];
    if (!id) continue;
    const short = text(box.querySelector(".courseBox--shortname"));
    let name = text(box.querySelector(".courseBox--name"));
    if (!name.startsWith(short)) name = `${short} ${name}`.trim();
    courses.set(id, { id, name: name || id });
  }
  return [...courses.values()];
}
