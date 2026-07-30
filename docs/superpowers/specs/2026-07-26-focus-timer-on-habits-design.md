# Focus Timer on Habits — Design

Date: 2026-07-26
Status: approved, not yet implemented

## Goal

Let a focus session target a habit (not only a task). Completing the session
ticks the habit off for today and logs the focused time against it.

## Model change

Add to `Habit`:
- `focus_time` INTEGER default 0 — seconds focused on this habit this month.
- `session_count` INTEGER default 0 — number of focus sessions.

Both are per-month (each month's Habit copy tracks its own), and the carry-
forward that copies name + frequency does not copy these, so a new month starts
at 0. Applied via the existing startup `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
pattern in `website/__init__.py`.

## Session target

`FocusTimerCore.createSession` gains a `habitId` field (default null) alongside
`taskId`. A session targets at most one of a task or a habit.

## Dropdown (pomodoro setup)

The `/pomodoro` route passes `habits` (current month's HabitMonth habits). The
`#cfgTask` select becomes a grouped picker:
- `<option value="">No specific task</option>` (default)
- `<optgroup label="Tasks">` — tasks, value = bare task id (unchanged, keeps the
  `/pomodoro/<task_id>` deep link working; the deep-linked task is preselected).
- `<optgroup label="Habits">` — habits, value = `habit:<id>`.

`startSession` parses the value: a `habit:` prefix sets `selectedHabit` (and
`sessionLabel` = the habit name so it shows on the timer and in history);
otherwise it's a task as today. The custom-name box stays hidden whenever any
task or habit is chosen (the value is truthy).

## Save payload

`saveSession` sends `habit_id` (from `session.habitId`) alongside `task_id`.
`restoreRunningSession` restores `selectedHabit` from `session.habitId`.

## Backend `save_pomodoro`

```
habit_id = data.get('habit_id')
...
habit = Habit.query.get(habit_id) if habit_id else None
hm = db.session.get(HabitMonth, habit.habit_month_id) if habit else None
if not hm or hm.user_id != current_user.id:
    habit = None
if habit and not partial:
    habit.focus_time = (habit.focus_time or 0) + duration_seconds
    habit.session_count = (habit.session_count or 0) + 1
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    log = HabitLog.query.filter_by(habit_id=habit.id, date=today).first()
    if log:
        log.completed = True
    else:
        db.session.add(HabitLog(habit_id=habit.id, date=today, completed=True))
```

The `FocusSession` is still written (so it shows in Recent Sessions and counts
in analytics totals) with `task_id=None` and `label` = the habit name when a
habit is the target. Label precedence: task → None; else habit → habit.name;
else the typed label.

Partial sessions record the FocusSession but do **not** tick off or add
focus_time — matching partial task behaviour.

`FocusSession` gets no `habit_id` column; per-habit time lives on
`Habit.focus_time`, which is all the display needs.

## Display

On each habit row in `habits_monthly.html`, a small "Xm" badge shows
`habit.focus_time // 60` when > 0 — this month's focused minutes.

## Edge cases (stated)

- Focusing a habit on a frequency "off-day" still ticks it done for today; the
  user chose to focus on it, so it counts. Rare.
- A habit whose id is stale/deleted is ignored (ownership/existence check).
- Completion on a habit does not redirect to quick-note (that is task-only);
  `session.taskId` is null for habits, so the existing task-redirect branch is
  skipped and it closes to the setup screen.

## Testing

- Core: `createSession` includes `habitId`; existing 18 tests still pass.
- Template renders with the Habits optgroup and the focus-time badge.
- In-browser: selecting a habit sets a `habit:` value and the name box hides;
  a session's payload carries `habit_id`.
- Backend logic (focus_time increment, tick-off, ownership) verified by reading;
  the DB effect is the user's to confirm after deploy (needs real habits).
