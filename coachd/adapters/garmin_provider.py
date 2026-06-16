"""GarminProvider — the Garmin Connect data source behind the ToolProvider seam.

Wraps the community Garmin MCP server. Carries two pieces of scar tissue from the
legacy stack, both security-relevant:

  * READ_TOOLS — the exact read-only tool allowlist (from coach.sh + the three
    workout/course READ tools the chat added). Reports use only these.
  * WRITE_TOOLS — the five SAFE write tools (create/upload/schedule). This is an
    ALLOWLIST, not a denylist: destructive operations (delete/unschedule/
    schedule_week) are deliberately ABSENT and must never be added. Write tools
    are exposed only in interactive chat and always gated by the write-guard.

The MCP command is configurable so the image can point at a pinned, pre-baked
garmin-mcp (architecture decision A1) instead of pulling from git at runtime.
The token directory is passed through as GARMINTOKENS so the MCP reads the same
tokens that ``coachd login`` wrote.
"""

from __future__ import annotations

from pathlib import Path

from ..core.i18n import TODAY_MARKER

# Read-only tools. First 40 mirror coach.sh (reports); the last 3 are the
# workout/course READ tools chat added for context. All safe to expose anywhere.
READ_TOOLS: tuple[str, ...] = (
    "get_full_name",
    "get_sleep_summary", "get_sleep_data",
    "get_hrv_data", "get_hrv_trend",
    "get_training_readiness", "get_morning_training_readiness",
    "get_body_battery", "get_body_battery_events",
    "get_rhr_day",
    "get_vo2max_trend",
    "get_stress_data", "get_stress_summary", "get_all_day_stress",
    "get_user_summary", "get_stats", "get_stats_and_body", "get_user_profile",
    "get_heart_rates", "get_heart_rates_summary",
    "get_steps_data",
    "get_activities_by_date", "get_activities_fordate", "get_activity",
    "get_activity_hr_in_timezones", "get_activity_splits", "get_activity_split_summaries",
    "get_training_status", "get_training_load_trend", "get_training_effect",
    "get_endurance_score", "get_race_predictions", "get_spo2_data",
    "get_respiration_summary", "get_respiration_trend",
    "get_progress_summary_between_dates",
    "get_weekly_steps", "get_weekly_stress", "get_weekly_intensity_minutes",
    "get_daily_steps",
    "get_workouts", "get_workout_by_id", "get_courses",
)

# SAFE write tools only — an allowlist. Destructive ops are intentionally absent.
WRITE_TOOLS: tuple[str, ...] = (
    "create_walk_run_workout",
    "create_strength_workout",
    "upload_workout",
    "schedule_workout",
    "upload_course",
)

# Never exposed under any flow. Kept explicit so a future edit that "adds a
# Garmin tool" trips the guard test instead of silently widening the blast radius.
FORBIDDEN_TOOLS: tuple[str, ...] = (
    "delete_workout", "delete_workouts",
    "unschedule_workout", "unschedule_workouts",
    "delete_course", "schedule_week",
)

_FRAGMENT = (
    "Data comes from Garmin Connect via the mcp__garmin__* MCP tools (live watch "
    "telemetry: sleep, HRV, readiness, load, stress, activities). "
    "Tool hints: take sleep via get_sleep_summary (get_sleep_data is heavy); "
    "recovery — only from get_training_readiness (get_training_status is unreliable "
    "for it); prefer trend/weekly/summary endpoints over heavy per-day cycles.\n"
    "CREATING/SCHEDULING WORKOUTS — tool choice:\n"
    "• The user asks to SCHEDULE a run on a day (\"plan and schedule for tomorrow\", "
    "\"put a run on Wednesday\") → call create_and_schedule_run(..., "
    "schedule_date='YYYY-MM-DD'). This tool creates the workout AND puts it on the "
    f"calendar with one confirmation. Take the date from the '{TODAY_MARKER}' line "
    "below and count relative days.\n"
    "• The user asks only to BUILD/save a workout (no date) → "
    "create_walk_run_workout / create_strength_workout (goes to the library only).\n"
    "• Schedule an ALREADY EXISTING workout for a date → first get_workouts, take the "
    "real workout_id, then schedule_workout(workout_id, calendar_date). Do NOT guess the id."
)


class GarminProvider:
    """ToolProvider for Garmin Connect."""

    NAME = "garmin"

    def __init__(
        self,
        tokenstore: str | Path,
        *,
        command: str = "garmin-mcp",
        args: tuple[str, ...] = (),
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._tokenstore = str(Path(tokenstore).expanduser())
        self._command = command
        self._args = tuple(args)
        self._extra_env = dict(extra_env or {})

    def name(self) -> str:
        return self.NAME

    def _qualify(self, tools: tuple[str, ...]) -> list[str]:
        return [f"mcp__{self.NAME}__{t}" for t in tools]

    def mcp_servers(self) -> dict:
        return {
            self.NAME: {
                "type": "stdio",
                "command": self._command,
                "args": list(self._args),
                # the MCP reads tokens from the same store `coachd login` wrote
                "env": {"GARMINTOKENS": self._tokenstore, **self._extra_env},
            }
        }

    def read_tools(self) -> list[str]:
        return self._qualify(READ_TOOLS)

    def write_tools(self) -> list[str]:
        return self._qualify(WRITE_TOOLS)

    def system_prompt_fragment(self) -> str:
        return _FRAGMENT
