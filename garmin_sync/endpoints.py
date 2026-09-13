"""
Garmin Connect internal API endpoints.

These are the stable paths Garmin's own web app uses (verified against the
reference Garmin client). What broke in 2026 was authentication, not these
paths -- so they're baked in and there's no discovery step.

{display_name} is your profile id (fetched once via PROFILE).
{date} is an ISO date, e.g. 2026-06-23.
"""

API_BASE = "https://connect.garmin.com/gc-api"

PROFILE = "/userprofile-service/userprofile/userProfileBase"

ACTIVITIES = "/activitylist-service/activities/search/activities?limit={limit}&start={start}"
ACTIVITY_DETAIL = "/activity-service/activity/{activity_id}"
ACTIVITY_SPLITS = "/activity-service/activity/{activity_id}/splits"

DAILY_SUMMARY = "/usersummary-service/usersummary/daily/{display_name}?calendarDate={date}"
SLEEP = "/wellness-service/wellness/dailySleepData/{display_name}?date={date}&nonSleepBufferMinutes=60"
HRV = "/hrv-service/hrv/{date}"
TRAINING_READINESS = "/metrics-service/metrics/trainingreadiness/{date}"

# Range endpoints (one call covers many days), available if you want to extend:
BODY_BATTERY_RANGE = "/wellness-service/wellness/bodyBattery/reports/daily?startDate={start}&endDate={end}"
STEPS_RANGE = "/usersummary-service/stats/steps/daily/{start}/{end}"

# ---- performance and configuration -----------------------------------------
# These change slowly (zones) or once a day at most (the rest), so they're
# pulled once per sync rather than per day.
HR_ZONES = "/biometric-service/heartRateZones"
POWER_ZONES = "/biometric-service/powerZones/sports/all"
PERSONAL_RECORDS = "/personalrecord-service/personalrecord/prs/{display_name}"
RACE_PREDICTIONS = "/metrics-service/metrics/racepredictions/latest/{display_name}"
TRAINING_STATUS = "/metrics-service/metrics/trainingstatus/aggregated/{date}"
MAX_METRICS = "/metrics-service/metrics/maxmet/daily/{start}/{end}"
ENDURANCE_SCORE = "/metrics-service/metrics/endurancescore?calendarDate={date}"
HILL_SCORE = "/metrics-service/metrics/hillscore?calendarDate={date}"
USER_SETTINGS = "/userprofile-service/userprofile/userSettings"


def sync_plan(start: str, end: str) -> dict:
    """What the browser extension (or any client) should fetch.

    Path templates keep Garmin URL knowledge on the server, so an endpoint
    change does not require a new extension build. Clients substitute
    `{display_name}`, `{date}`, `{limit}` and `{start}`.
    """
    return {
        "api_base": API_BASE,
        "since": start,
        "until": end,
        "profile": PROFILE,
        "day": [
            {"key": "daily", "path": DAILY_SUMMARY},
            {"key": "sleep", "path": SLEEP},
            {"key": "hrv", "path": HRV},
            {"key": "readiness", "path": TRAINING_READINESS},
        ],
        "once": [
            {"key": "activities", "path": ACTIVITIES, "paginate": True,
             "limit": 100},
            {"key": "hr_zones", "path": HR_ZONES, "meta": True},
            {"key": "power_zones", "path": POWER_ZONES, "meta": True},
            {"key": "maxmet", "path": MAX_METRICS, "meta": True},
            {"key": "race_predictions", "path": RACE_PREDICTIONS, "meta": True},
            {"key": "personal_records", "path": PERSONAL_RECORDS, "meta": True},
            {"key": "user_settings", "path": USER_SETTINGS, "meta": True},
            {"key": "training_status", "path": TRAINING_STATUS, "meta": True},
        ],
    }

# ---- workouts: the write side (Phase 1) ------------------------------------
# Creating a workout returns a workoutId; scheduling it puts it on the Garmin
# calendar, which is what makes it appear on the watch after a sync. Updating
# uses PUT and keeps the id, so existing schedules stay pointed at it.
WORKOUTS = "/workout-service/workouts?start={start}&limit={limit}"
WORKOUT = "/workout-service/workout/{workout_id}"
WORKOUT_CREATE = "/workout-service/workout"
WORKOUT_SCHEDULE = "/workout-service/schedule/{workout_id}"
WORKOUT_UNSCHEDULE = "/workout-service/schedule/{schedule_id}"
