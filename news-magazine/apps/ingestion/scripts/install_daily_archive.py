"""
Install a Windows Task Scheduler job that runs the daily news archive.

Creates a task named "NepalNews-DailyArchive" that runs
scripts/archive_daily.py once a day at 23:45 local time.

What it does: writes data/daily/YYYY-MM-DD.json (a dated snapshot of
what the pipeline actually published) and commits it.

What it deliberately does NOT do: push. The script commits locally only.
Pushing is a separate, explicit action — see the note at the end of this
file.

Run with --remove to delete the task.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "NepalNews-DailyArchive"
INGESTION = Path(__file__).resolve().parent.parent
SCRIPT = INGESTION / "scripts" / "archive_daily.py"
PYTHON = INGESTION / ".venv" / "Scripts" / "python.exe"


def run(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(args, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


def remove() -> int:
    code, out = run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if code == 0:
        print(f"Removed task '{TASK_NAME}'.")
        return 0
    print(f"Nothing to remove, or removal failed:\n{out}")
    return 0


def install(hour: int = 23, minute: int = 45) -> int:
    if not PYTHON.exists():
        print(f"Virtualenv not found at {PYTHON}.")
        print("Create it first:  python -m venv .venv")
        return 1

    if not (INGESTION / ".env").exists():
        print(f"Missing {INGESTION / '.env'}")
        print("Copy .env.example to .env and fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
        return 1

    time_spec = f"{hour:02d}:{minute:02d}"

    code, out = run([
        "schtasks", "/Create",
        "/TN", TASK_NAME,
        "/TR", f'"{PYTHON}" "{SCRIPT}"',
        "/SC", "DAILY",
        "/ST", time_spec,
        "/F",
    ])

    if code != 0:
        print(f"Failed to create the task:\n{out}")
        print("\nIf this is a permissions issue, run the terminal as Administrator.")
        return 1

    print(f"Created task '{TASK_NAME}' — runs daily at {time_spec}.")
    print(f"  Command : {PYTHON} {SCRIPT}")
    print(f"  Output  : {INGESTION.parents[1] / 'data' / 'daily'}/YYYY-MM-DD.json")
    print()
    print("The script commits locally. It does NOT push.")
    print("To publish, run:  git push origin main")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"--remove", "remove"}:
        return remove()

    hour, minute = 23, 45
    if len(sys.argv) > 2:
        try:
            parts = sys.argv[2].split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            print("Time must look like 23:45")
            return 1

    return install(hour, minute)


if __name__ == "__main__":
    raise SystemExit(main())