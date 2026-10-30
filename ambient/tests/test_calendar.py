from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from conftest import Server, free_port, make_config, wait_for

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def stamp(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y%m%dT%H%M%S")


def test_an_event_inside_notice_becomes_a_row_and_shows_while_on(tmp_path, plugins_dir):
    now = datetime.now().astimezone()
    ics = tmp_path / "work.ics"
    ics.write_text(
        "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//dusha-test//EN",
                "BEGIN:VEVENT",
                "UID:now@test",
                f"DTSTAMP:{stamp(now)}",
                f"DTSTART:{stamp(now - timedelta(minutes=5))}",
                f"DTEND:{stamp(now + timedelta(minutes=25))}",
                "SUMMARY:Standup",
                "LOCATION:Zoom",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "UID:later@test",
                f"DTSTAMP:{stamp(now)}",
                f"DTSTART:{stamp(now + timedelta(hours=5))}",
                f"DTEND:{stamp(now + timedelta(hours=6))}",
                "SUMMARY:Dentist",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ]
        )
    )
    config = make_config(
        tmp_path / "hub",
        plugins_dir,
        {"ambient": {"device": "desk", "sample_seconds": 3600, "calendar": {"sources": [str(ics)]}}},
    )
    with Server(config, free_port()) as hub:

        def rows() -> list:
            params = {"source": "calendar"}
            return httpx.get(hub.url + "/state/v1/ambient", params=params).json()["observations"]

        row = wait_for(rows, timeout=8)[0]
        assert row["text"].startswith("Standup at ") and row["text"].endswith("for 30m (Zoom)")
        assert len(rows()) == 1
        context = httpx.post(hub.url + "/state/v1/context", json={"query": "x"}).text
        assert "calendar_now" in context and "Standup" in context and "Dentist" not in context


def test_post_checkout_reports_branch_moves_only(tmp_path, recorder):
    server = recorder(reply={})
    repo = tmp_path / "dusha-test-repo"
    repo.mkdir()
    env = {**os.environ, "DUSHA_URL": server.url, "DUSHA_TOKEN": "t"}

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one")
    shutil.copy(HOOKS / "post-checkout", repo / ".git" / "hooks" / "post-checkout")
    git("checkout", "-q", "-b", "feature")  # the same commit, so no report
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "two")
    git("checkout", "-q", "main")
    wait_for(lambda: server.requests)
    assert server.requests[0]["body"] == {"source": "git", "text": "switched to dusha-test-repo/main"}
    assert server.requests[0]["headers"]["x-companion-token"] == "t"
