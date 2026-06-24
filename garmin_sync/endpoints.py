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

DAILY_SUMMARY = "/usersummary-service/usersummary/daily/{display_name}?calendarDate={date}"
SLEEP = "/wellness-service/wellness/dailySleepData/{display_name}?date={date}&nonSleepBufferMinutes=60"
HRV = "/hrv-service/hrv/{date}"
TRAINING_READINESS = "/metrics-service/metrics/trainingreadiness/{date}"

# Range endpoints (one call covers many days), available if you want to extend:
BODY_BATTERY_RANGE = "/wellness-service/wellness/bodyBattery/reports/daily?startDate={start}&endDate={end}"
STEPS_RANGE = "/usersummary-service/stats/steps/daily/{start}/{end}"
