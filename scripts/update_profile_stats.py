#!/usr/bin/env python3
"""Refresh the terminal-style statistics block in the profile README.

Only public owned source repositories are included in commit and line-change
aggregates. The profile repository itself is excluded so that this daily README
maintenance commit does not artificially inflate source-code statistics.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OWNER = os.environ.get("PROFILE_OWNER", "Nixort")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

REPOSITORIES_QUERY = """
query($login: String!) {
  user(login: $login) {
    publicRepos: repositories(privacy: PUBLIC) { totalCount }
    followers { totalCount }
    contributionsCollection { contributionCalendar { totalContributions weeks { contributionDays { contributionCount } } } }
    ownedRepos: repositories(first: 100, privacy: PUBLIC, ownerAffiliations: OWNER) {
      nodes { name defaultBranchRef { name } stargazerCount }
    }
  }
}
"""

HISTORY_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $cursor) {
            nodes { additions deletions author { user { login } } }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict) -> dict:
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN must be set")
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "nixort-profile-stats",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response_data = json.load(response)
    if response_data.get("errors"):
        raise RuntimeError("; ".join(error["message"] for error in response_data["errors"]))
    return response_data["data"]


def source_totals(repositories: list[dict]) -> tuple[int, int, int, int]:
    commits = additions = deletions = scanned_repositories = 0
    for repository in repositories:
        if repository["name"].casefold() == OWNER.casefold() or not repository["defaultBranchRef"]:
            continue
        scanned_repositories += 1
        cursor = None
        while True:
            data = graphql(HISTORY_QUERY, {"owner": OWNER, "name": repository["name"], "cursor": cursor})
            branch = data["repository"]["defaultBranchRef"]
            if not branch:
                break
            history = branch["target"]["history"]
            for commit in history["nodes"]:
                author = (commit["author"] or {}).get("user") or {}
                if author.get("login", "").casefold() == OWNER.casefold():
                    commits += 1
                    additions += commit["additions"]
                    deletions += commit["deletions"]
            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]
            time.sleep(0.1)
    return scanned_repositories, commits, additions, deletions


def sparkline(weeks: list[dict]) -> str:
    values = [sum(day["contributionCount"] for day in week["contributionDays"]) for week in weeks][-12:]
    while len(values) < 12:
        values.insert(0, 0)
    peak = max(values) if values else 0
    symbols = "▁▂▃▄▅▆▇█"
    return "·" * 12 if peak == 0 else "".join(symbols[round(value / peak * 7)] for value in values)


def build_block(profile: dict, repository_count: int, commits: int, additions: int, deletions: int) -> str:
    calendar = profile["contributionsCollection"]["contributionCalendar"]
    stars = sum(repository["stargazerCount"] for repository in profile["ownedRepos"]["nodes"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d UTC")
    return f'''```ini
; nixort@github:~$ profile --live
; refreshed = {now}

[profile]
role = "systems & security engineering"
focus = "protocols · recovery · binary analysis"

[stack]
languages = "Rust · C · C++ · Go · Python · Bash"
platforms = "Linux · x86-64 · ARM64 · Docker"

[public_activity]
repositories = {profile["publicRepos"]["totalCount"]}
total_stars = {stars}
contributions_last_year = {calendar["totalContributions"]}
source_commits_scanned = {commits}
lines_added_git = "+{additions:,}"
lines_removed_git = "-{deletions:,}"
net_lines_changed = "+{additions - deletions:,}"
trend_last_12_weeks = "{sparkline(calendar["weeks"])}"

[contact]
email = "nixort@proton.me"
principle = "correctness before speed"

[scope]
source_repositories = {repository_count}
```
'''


def main() -> None:
    profile = graphql(REPOSITORIES_QUERY, {"login": OWNER})["user"]
    repository_count, commits, additions, deletions = source_totals(profile["ownedRepos"]["nodes"])
    block = build_block(profile, repository_count, commits, additions, deletions)
    original = README.read_text(encoding="utf-8")
    updated, substitutions = re.subn(r"```(?:text|ini)\n.*?\n```\n?", block, original, count=1, flags=re.DOTALL)
    if substitutions != 1:
        raise RuntimeError("Expected exactly one profile code block in README.md")
    README.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
