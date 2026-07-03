"""Minimal GitHub REST client used by intake, merge policy, and self-heal.

Explicit timeouts on every call (5s connect / 30s read) per the repo coding
standards. Only the endpoints the factory needs - not a general SDK.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API = "https://api.github.com"
TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

_REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$")


class GitHubError(Exception):
    pass


def detect_repo(cwd: str = ".") -> str:
    """Return 'owner/repo' from the origin remote, or '' when unavailable."""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    match = _REMOTE_RE.search(proc.stdout.strip())
    return match.group(1) if match else ""


class GitHubClient:
    def __init__(self, repo: str, token: str | None = None) -> None:
        if not repo:
            raise GitHubError("no GitHub repo configured or detected")
        self.repo = repo
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        if not self._token:
            raise GitHubError("GITHUB_TOKEN is not set (see .env.example)")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{API}/repos/{self.repo}{path}"
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.request(method, url, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise GitHubError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text

    # -- issues -----------------------------------------------------------

    def get_issue(self, number: int) -> dict[str, Any]:
        return self._request("GET", f"/issues/{number}")

    def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> dict:
        return self._request(
            "POST", "/issues", json={"title": title, "body": body, "labels": labels or []}
        )

    def comment(self, issue_number: int, body: str) -> None:
        self._request("POST", f"/issues/{issue_number}/comments", json={"body": body})

    # -- pull requests -------------------------------------------------------

    def create_pr(self, title: str, head: str, base: str, body: str) -> dict[str, Any]:
        return self._request(
            "POST", "/pulls", json={"title": title, "head": head, "base": base, "body": body}
        )

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._request("POST", f"/issues/{number}/labels", json={"labels": labels})

    def merge_pr(self, number: int, method: str = "squash") -> dict[str, Any]:
        return self._request("PUT", f"/pulls/{number}/merge", json={"merge_method": method})

    def pr_checks_passed(self, number: int) -> bool:
        pr = self._request("GET", f"/pulls/{number}")
        ref = pr["head"]["sha"]
        status = self._request("GET", f"/commits/{ref}/status")
        runs = self._request("GET", f"/commits/{ref}/check-runs")
        check_runs = runs.get("check_runs", [])
        checks_ok = all(
            run.get("conclusion") in ("success", "neutral", "skipped") for run in check_runs
        )
        # Legacy status API: 'success' or no statuses at all.
        status_ok = status.get("state") in ("success",) or status.get("total_count", 0) == 0
        return checks_ok and (status_ok or bool(check_runs))

    # -- actions (self-heal) ----------------------------------------------------

    def list_failed_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        # Bounded page (max 100 per API; we default far lower).
        data = self._request(
            "GET", f"/actions/runs?status=failure&per_page={min(limit, 100)}"
        )
        return data.get("workflow_runs", [])

    def get_run(self, run_id: int) -> dict[str, Any]:
        return self._request("GET", f"/actions/runs/{run_id}")

    def get_failed_logs(self, run_id: int, max_chars: int = 40_000) -> str:
        """Concatenated logs of failed jobs, truncated to bound memory."""
        jobs = self._request("GET", f"/actions/runs/{run_id}/jobs?per_page=50")
        chunks: list[str] = []
        for job in jobs.get("jobs", []):
            if job.get("conclusion") != "failure":
                continue
            try:
                log_text = self._request("GET", f"/actions/jobs/{job['id']}/logs")
            except GitHubError as exc:
                logger.warning(
                    "could not fetch job logs",
                    extra={"operation": "ci_logs", "job_id": job["id"], "error": str(exc)},
                )
                continue
            chunks.append(f"=== job: {job.get('name', job['id'])} ===\n{log_text}")
        combined = "\n".join(chunks)
        return combined[-max_chars:]  # keep the tail - failures print last

    def rerun_failed_jobs(self, run_id: int) -> None:
        self._request("POST", f"/actions/runs/{run_id}/rerun-failed-jobs")

    def get_workflow_file(self, path: str, ref: str) -> str:
        data = self._request("GET", f"/contents/{path}?ref={ref}")
        import base64

        return base64.b64decode(data["content"]).decode("utf-8")
