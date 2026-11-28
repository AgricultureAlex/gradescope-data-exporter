import { getCourses, NotLoggedInError } from "./gradescope.js";

const status = document.getElementById("status");
const login = document.getElementById("login");
const list = document.getElementById("courses");

try {
  const courses = await getCourses();
  if (courses.length === 0) {
    // A logged-out visit may show the landing page without redirecting to /login.
    status.textContent = "No courses found. Check that you're logged in, then reopen this popup.";
    login.hidden = false;
  } else {
    status.textContent = `Found ${courses.length} course(s)`;
    for (const course of courses) {
      const item = document.createElement("li");
      item.textContent = course.name;
      list.append(item);
    }
  }
} catch (err) {
  if (err instanceof NotLoggedInError) {
    status.textContent = "Log in, then reopen this popup.";
    login.hidden = false;
  } else {
    status.textContent = `Could not load courses: ${err.message}`;
  }
}
