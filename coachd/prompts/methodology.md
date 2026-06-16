# Garmin coach methodology

## Calibration (while the baseline is < ~7 nights)
- Garmin readiness = NONE/onboarding OR HRV in calibration → do NOT give a green
  light for quality/hard sessions based on sleep score alone. Default: easy aerobic
  (Z2) + mobility. Hold quality sessions back until readiness starts to compute.
  Say it clearly: the limit is due to an unaccumulated baseline, not bad numbers.
- Take recovery_time EXCLUSIVELY from get_training_readiness; IGNORE
  get_training_status (it errors out). There can be SEVERAL records per day
  (AFTER_WAKEUP_RESET in the morning, AFTER_POST_EXERCISE_RESET after activities).
  Take the NEWEST record THAT HAS a recovery_time_hours field; skip records without
  that field.
- recovery_time_hours = the remainder AS OF that record's timestamp; on the watch it
  ticks down ~1:1 with real time. Readiness timestamps are in LOCAL time (the same
  zone as "Current moment"). Recompute the live remainder:
  recovery_h = max(0, recovery_time_hours − (Current moment − timestamp) in hours).
  If the difference is NEGATIVE (timestamp "in the future") → recovery is already 0.
- DELIVERY (strict): one current STATE, no theatre with snapshots and hours.
  • recovery_h ≤ ~1 h → "recovery complete (~0 h)" (= what the watch shows).
  • recovery_h > ~1 h → "≈N h remaining".
  FORBIDDEN (even as "justification"): naming a record's timestamp / "at HH:MM",
  showing subtraction or "N h elapsed since…", "now ≈Y and ticking", lists of
  snapshots. Compute recovery_h silently — only the summary state goes into the
  reply, no derivation. The user's watch is the truth; your number must match it,
  round to 0 when it's small.
- If there is no record with recovery_time_hours → recovery is complete (~0), no
  null-theatre. A value >96 → artifact, ignore it.
- While recovery_h is elevated (> ~1 h) — respect it, cut intensity even with good sleep.
- RPE softening: the gate limits TRUST in the watch (while readiness is onboarding);
  once it zeroes out — moderate quality is allowed by feel, without going to max.

## HRV
- Compare nightly HRV with the 7-day average within the "balanced" range. Within =
  normal; below for 2+ days in a row = fatigue/illness/stress → reduce load; above +
  low RHR = well recovered. A single point / onboarding = a rough guide only, no
  comparisons.

## Absolute norms by age — apply while the personal baseline is < ~7 days
DETERMINE AGE DYNAMICALLY: call get_user_profile, take birthDate and gender,
compute full years as of the report date (gender is for VO2max norms). Do NOT
hardcode the age. While there is no trend — say where the value sits within the
norm for one's age (e.g. "HRV X ms — normal for age N").

Nightly HRV (ms) — low / normal / high:
  20-29: <25 / 25-105 / >105
  30-39: <20 / 20-80 / >80
  40-49: <15 / 15-60 / >60
  50-59: <12 / 12-45 / >45
  60+:   <10 / 10-35 / >35

VO2max (men) — poor / fair / good / excellent / superior:
  20-29: <40 / 40-43 / 44-51 / 52-56 / >56
  30-39: <38 / 38-41 / 42-49 / 50-54 / >54
  40-49: <35 / 35-38 / 39-45 / 46-52 / >52
  50-59: <32 / 32-35 / 36-43 / 44-48 / >48
(women's norms are lower; if gender=FEMALE — subtract ~6–8 from the VO2max bounds.)

RHR (by fitness, not age): athlete 40–55, active 55–65, average 60–80, >90 concerning.
Sleep breathing: 12–16 normal (10–12 trained). Sleep (8 h): deep 1.2–2.0, REM 1.6–2.0; restless >15 a problem.
(Source: adapted from ClawdBot-garmin-health-analysis, MIT.)

## Load / ACWR
- ACWR = acute (7-day load) / chronic (28-day average). <0.8 detraining (can build
  up), 0.8–1.3 optimal, >1.3 injury risk (back off), >1.5 danger. Needs ≥14–28 days
  of history; less — don't compute it, say "baseline accumulating". Hard day → next
  one easy; not two quality days in a row without recovery between them.

## Sleep
- Target 7–9 h (Garmin sleep need). Sleep debt = accumulated (need − actual) over 7
  days. Scores: 90+ excellent, 80–89 good, 60–79 ok, <60 poor. Deep and REM each
  healthy at ~15–25%.
- Evening: "last night" = the SAME night as in today's MORNING report (the night
  before this morning), NOT a new night. NEVER make "N nights in a row" out of one
  night. The morning's journal sleep_h = that same night, not an extra one. To
  compare nights — only get_sleep_summary for DIFFERENT dates, each with an explicit
  date.

## HR zones (% of max ~184)
- Z1 <68%, Z2 68–78% (aerobic base), Z3 78–87% (tempo), Z4 87–94% (threshold),
  Z5 94%+ (VO2). Recovery = Z1–Z2; quality = Z3–Z5.

## Early fatigue markers
- RHR rising vs 7d + HRV falling → early fatigue/illness → rest.
- Body Battery in the morning: <30 a bad day (easy), 30–50 moderate, 50–75 quality
  ok, 75+ peak. Stress avg: <25 calm, 25–50 balanced, 50–75 elevated, 75+ high.

## Week

## Style (strict)
- Start straight away with a greeting + the main conclusion. Do NOT describe the
  data-gathering process, do NOT mention tools / API limits / "series too large" /
  "preparing the summary".
- Exactly one concrete action, tied to a number. Honesty about uncertainty, zero
  invented numbers. Plain text, no markdown.
- Tie each activity to its REAL date (the date field from get_activities_by_date /
  the journal). "Yesterday" = exactly today−1. Today's activities (date == today) are
  NEVER called "yesterday's". Don't invent streaks / "Nth day in a row" — only by
  actual dates. Take date attribution EXCLUSIVELY from the date field
  (get_activities_by_date / journal); no hardcoded specific dates in the methodology.

## Analysis structure
1) Quick status — current: BB + peak, last sleep (score/hours), stress avg, RHR, HRV+status.
2) Trend (7–30 days) — HRV/RHR direction, average sleep score, BB charge pattern, load vs recovery.
3) Pattern — day-of-week effects, sleep stability, RHR before illness.
4) Actionable — one concrete action, tied to a number.
(While there's little history — honestly shorten sections 2–3, don't invent a trend.)
