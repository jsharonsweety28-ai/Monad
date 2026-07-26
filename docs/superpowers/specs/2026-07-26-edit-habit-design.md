# Edit Habit — Design

Date: 2026-07-26
Status: approved, not yet implemented

## Goal

Let the user edit an existing habit's name and frequency. Today habits can
only be added, toggled, or deleted — a mis-entered name or a change of plan
(daily → twice a week) has no fix but delete-and-recreate.

## Data model (existing)

`Habit(name, frequency, habit_month_id)` — one row per month, linked to a
`HabitMonth(user_id, year, month)`. Habits carry forward to new months by
copying name + frequency. `frequency` is `daily | 2x | 3x | 4x | 5x | days:0,2,4`.
There is no shared habit id across months; copies are linked only by name.

## Scope of an edit (user chose: choose each time)

The edit form has a toggle:

- **This month onward** (default) — updates this habit's copy in the current
  month and every later month, leaving earlier months as history. For a genuine
  change of plan.
- **All months** — updates every copy of the habit across all the user's
  months, past included. For fixing a value that was wrong from the start.

Both identify "the same habit" across months by its **current (old) name**.

## Backend

New route `POST /habit/edit/<int:id>`, ownership-checked like `delete_habit` /
`toggle_habit` (via `habit_month.user_id == current_user.id`).

```
new_name  = request.form.get('habit', '').strip()
frequency = request.form.get('frequency', 'daily')
if frequency == 'custom':
    custom_days = request.form.get('custom_days', '').strip()
    frequency = f'days:{custom_days}' if custom_days else 'daily'
scope = request.form.get('scope', 'onward')  # 'onward' | 'all'

if new_name:
    old_name = habit.name
    cy, cm = habit_month.year, habit_month.month
    q = (Habit.query.join(HabitMonth)
         .filter(HabitMonth.user_id == current_user.id, Habit.name == old_name))
    if scope != 'all':
        q = q.filter(db.or_(HabitMonth.year > cy,
                            db.and_(HabitMonth.year == cy, HabitMonth.month >= cm)))
    for h in q.all():
        h.name = new_name
        h.frequency = frequency
    db.session.commit()
    flash('Habit updated!', category='success')
return redirect(request.referrer or url_for('views.habits_monthly', year=..., month=...))
```

The current habit is included because it sits in the current month, which is in
both scopes.

## Frontend (`habits_monthly.html`)

### Edit button

A pencil button in `.habit-name-cell`, beside the existing delete link:

```
<button type="button" class="btn-edit-habit"
        onclick="openEditHabit({{ habit.id }}, '{{ habit.name|e }}', '{{ habit.frequency or 'daily' }}')"
        title="Edit">✎</button>
```

### Modal

An overlay + modal mirroring the existing reflect-modal pattern (fixed,
centered, `z-index` above the table). Fields: name input, the same frequency
`<select>` and weekday pills as the add form (distinct ids, e.g.
`editCustomDayPicker` / `editCustomDays`), and a scope radio group
(`This month onward` default / `All months`). Submits to
`/habit/edit/<id>` via a form whose `action` is set on open.

### JS

- `openEditHabit(id, name, freq)` — set form action, fill name, set the freq
  select (map `days:*` → `custom` and light up the matching pills, show the
  picker), reset scope to `onward`, show the modal.
- `onEditFreqChange(sel)` / `toggleEditDay(pill)` — edit-scoped mirrors of the
  add-form helpers, writing to `editCustomDays`.
- `closeEditHabit()` — hide overlay + modal.

## Edge cases (stated)

- Two distinct habits sharing an identical name would both update (name-based
  matching). Rare.
- An "all months" frequency change reshapes how past months' progress/off-days
  display — intended for the fix-a-mistake case.
- A future month already opened before the edit is covered by "this month
  onward" (it filters by year/month ≥ current), so it updates too.

## Testing

No habit test coverage exists. Verify via code inspection + template render +
in-browser where reachable:
1. Route parses; template renders through the authenticated branch.
2. Edit button appears per habit; modal opens pre-filled (name, freq, custom
   pills for a `days:*` habit).
3. Frequency select + pills write the expected `custom_days`.
4. Scope radio defaults to `onward`.

Server-side scope filtering and cross-month updates are logic verified by
reading; a live multi-month check is the user's after deploy.
