from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, jsonify, send_from_directory, current_app
from .storage import upload_file, get_public_url
from flask_login import login_required, current_user
from . import db
from .models import User, Task, HabitMonth, Habit, HabitLog, DailyJournal, FocusSession, DailyPhoto, Community, CommunityMember, CommunityHabit, CommunityHabitLog, Achievement, TimeBlock, Note, Category, Subject, ClassSlot, Exam, StudyNote, Message, StudentProfile, ExamResult, SubjectMark, PushSubscription
import base64
from datetime import datetime, timedelta, timezone, date
import calendar
import json
import os
import random
import string
import threading as _threading
from werkzeug.utils import secure_filename

views = Blueprint('views', __name__)

@views.before_request
def guard_study():
    if request.path.startswith('/study') and current_user.is_authenticated:
        if current_user.study_enabled == False:
            flash('Study section is disabled. Enable it in Settings.', 'error')
            return redirect(url_for('views.home'))

PHASE_COPY = {
    'school':  {
        'greeting': 'Ready for class?',
        'task_label': "today's schoolwork",
        'empty': "No schoolwork added yet. Start with your hardest subject first.",
    },
    'college': {
        'greeting': "Let's get it done.",
        'task_label': "today's tasks",
        'empty': "Nothing yet. Tackle the assignment due soonest.",
    },
    'working': {
        'greeting': "Let's get to work.",
        'task_label': "today's work",
        'empty': "Clear slate. Add your top priority for today.",
    },
    'other':   {
        'greeting': "Let's get started.",
        'task_label': "today's tasks",
        'empty': "Nothing here yet. What do you want to accomplish?",
    },
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_invite_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


@views.app_context_processor
def inject_globals():
    now = datetime.now(timezone.utc)
    life_phase = getattr(current_user, 'life_phase', 'other') if current_user.is_authenticated else 'other'
    return dict(
        datetime=datetime,
        current_year=now.year,
        current_month=now.month,
        user_theme=getattr(current_user, 'theme', 'default') if current_user.is_authenticated else 'default',
        life_phase=life_phase,
        phase_copy=PHASE_COPY.get(life_phase, PHASE_COPY['other']),
    )

def get_smart_prompt(task_content):
    content = task_content.lower()
    if any(word in content for word in ['bible', 'scripture', 'word', 'god', 'pray', 'church']):
        return "📖 What chapter/verse did you read? What was the key message?"
    elif any(word in content for word in ['gym', 'workout', 'exercise', 'run', 'walk', 'cardio']):
        return "💪 Which body part did you work on? How was the intensity?"
    elif any(word in content for word in ['meet', 'call', 'zoom', 'discuss']):
        return "🤝 Who did you meet with? What was the outcome?"
    elif any(word in content for word in ['read', 'book', 'study', 'learn']):
        return "📚 What book/topic? Key takeaway?"
    elif any(word in content for word in ['code', 'program', 'develop', 'build']):
        return "💻 What did you build/fix? Any bugs solved?"
    elif any(word in content for word in ['cook', 'food', 'eat', 'meal', 'dinner', 'lunch']):
        return "🍳 What did you cook/eat? New recipe?"
    elif any(word in content for word in ['shop', 'buy', 'grocer', 'mall']):
        return "🛒 What did you buy? From where?"
    elif any(word in content for word in ['clean', 'organize', 'tidy']):
        return "🧹 What area did you clean? Before/after difference?"
    elif any(word in content for word in ['write', 'blog', 'journal', 'article']):
        return "✍️ What did you write about? How many words?"
    elif any(word in content for word in ['meditate', 'yoga', 'stretch']):
        return "🧘 How long did you meditate? How do you feel after?"
    elif any(word in content for word in ['sleep', 'nap', 'rest']):
        return "😴 How many hours? Quality of sleep?"
    else:
        return "💡 What did you focus on? Any quick note?"

def spawn_recurring_tasks(for_date):
    """Auto-create task instances for recurring tasks on the given date."""
    weekday = str(for_date.weekday())
    masters = Task.query.filter(
        Task.user_id == current_user.id,
        Task.recurring_days.isnot(None),
        Task.recurring_task_id.is_(None),   # only master tasks spawn
    ).all()
    created = False
    for master in masters:
        days = [d.strip() for d in master.recurring_days.split(',') if d.strip()]
        if weekday not in days:
            continue
        if master.date.date() == for_date:
            continue   # master itself covers its own date
        exists = Task.query.filter(
            Task.user_id == current_user.id,
            Task.recurring_task_id == master.id,
            db.func.date(Task.date) == for_date,
        ).first()
        if not exists:
            db.session.add(Task(
                content=master.content,
                user_id=current_user.id,
                date=datetime.combine(for_date, datetime.min.time()),
                priority=master.priority,
                due_time=master.due_time,
                recurring_task_id=master.id,
            ))
            created = True
    if created:
        db.session.commit()

def upsert_category(user_id, name, color):
    if not name:
        return
    cat = Category.query.filter_by(user_id=user_id, name=name).first()
    if cat:
        if color:
            cat.color = color
    else:
        db.session.add(Category(user_id=user_id, name=name, color=color or '#E07B39'))

def _time_to_minutes(t):
    h, m = map(int, t.split(':'))
    return h * 60 + m

def sync_task_timeblock(task):
    """Create or update the TimeBlock linked to a task when start+end times are both set."""
    # Resolve category color
    cat_color = '#E07B39'
    if task.category:
        cat = Category.query.filter_by(user_id=task.user_id, name=task.category).first()
        if cat:
            cat_color = cat.color

    if task.due_time and task.end_time:
        start_min = _time_to_minutes(task.due_time)
        end_min   = _time_to_minutes(task.end_time)
        task_date = task.date.date() if hasattr(task.date, 'date') else task.date
        block = TimeBlock.query.filter_by(task_id=task.id).first()
        if block:
            block.start_minute = start_min
            block.end_minute   = end_min
            block.title        = task.content
            block.date         = task_date
            block.color        = cat_color
            block.category     = task.category or None
        else:
            block = TimeBlock(
                user_id      = task.user_id,
                date         = task_date,
                start_minute = start_min,
                end_minute   = end_min,
                title        = task.content,
                block_type   = 'task',
                category     = task.category or None,
                task_id      = task.id,
                color        = cat_color,
            )
            db.session.add(block)
    else:
        block = TimeBlock.query.filter_by(task_id=task.id).first()
        if block:
            db.session.delete(block)

def check_daily_perfection(day_date):
    tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == day_date).all()
    if tasks:
        if not all(task.completed for task in tasks):
            return False
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=day_date.year, month=day_date.month).first()
    if habit_month:
        habits = Habit.query.filter_by(habit_month_id=habit_month.id).all()
        for habit in habits:
            log = HabitLog.query.filter_by(habit_id=habit.id, date=day_date).first()
            if not log or not log.completed:
                return False
    has_tasks = len(tasks) > 0
    has_habits = habit_month and Habit.query.filter_by(habit_month_id=habit_month.id).count() > 0
    if not has_tasks and not has_habits:
        return False
    return True

def check_community_crown(user_id, day_date):
    """Return True if any community the user belongs to had a perfect day on day_date."""
    memberships = CommunityMember.query.filter_by(user_id=user_id).all()
    for membership in memberships:
        community_id = membership.community_id
        member_ids = [m.user_id for m in CommunityMember.query.filter_by(community_id=community_id).all()]
        if not member_ids:
            continue
        habit_ids = [h.id for h in CommunityHabit.query.filter_by(community_id=community_id).all()]
        if not habit_ids:
            continue
        logs = CommunityHabitLog.query.filter(
            CommunityHabitLog.community_habit_id.in_(habit_ids),
            CommunityHabitLog.date == day_date,
            CommunityHabitLog.completed == True
        ).all()
        checked_in = set(log.user_id for log in logs)
        if set(member_ids).issubset(checked_in):
            return True
    return False

def compute_habit_progress(habits, year, month):
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    days_in_month = calendar.monthrange(year, month)[1]
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    start_of_month = date(year, month, 1)
    is_current_month = (year == today.year and month == today.month)
    result = {}
    for habit in habits:
        freq = habit.frequency or 'daily'
        # Determine effective start: first log date, capped to start of month
        earliest_log = min((log.date for log in habit.logs), default=None)
        if earliest_log and earliest_log <= start_of_month:
            effective_start = start_of_month
        elif earliest_log:
            effective_start = earliest_log
        else:
            effective_start = today if is_current_month else start_of_month
        end_date = today if is_current_month else date(year, month, days_in_month)
        end_day_num = (end_date - start_of_month).days + 1
        start_day_num = (effective_start - start_of_month).days + 1
        active_days = end_day_num - start_day_num + 1
        done = sum(1 for log in habit.logs if log.completed and log.date >= effective_start)
        required_weekdays = None
        week_done = None
        week_target = None
        if freq == 'daily':
            expected = active_days
        elif freq.endswith('x') and freq[:-1].isdigit():
            times = int(freq[:-1])
            week_target = times
            full_weeks = active_days // 7
            expected = full_weeks * times + min(active_days % 7, times)
            week_done = sum(1 for log in habit.logs
                           if log.completed and week_start <= log.date <= week_end)
        elif freq.startswith('days:'):
            req_str = freq[5:].strip()
            req_days = {int(d) for d in req_str.split(',') if d.strip().isdigit()} if req_str else set()
            required_weekdays = req_days
            expected = sum(1 for d in range(start_day_num, end_day_num + 1)
                          if datetime(year, month, d).weekday() in req_days)
        else:
            expected = active_days
        result[habit.id] = {
            'done': done,
            'expected': max(expected, 1),
            'required_weekdays': required_weekdays,
            'frequency': freq,
            'week_done': week_done,
            'week_target': week_target,
        }
    return result

def get_habit_suggestions(life_phase):
    suggestions = {
        'school':  ['Study for exams', 'Review class notes', 'Complete homework', 'Read for 30 min', 'Exercise daily'],
        'college': ['Attend lectures', 'Study group session', 'Work on assignments', 'Research paper', 'Stay hydrated'],
        'working': ['Morning planning', 'Deep work session', 'Check emails', 'Take breaks', 'Evening reflection'],
        'other':   ['Read a book', 'Meditate', 'Go for a walk', 'Drink water', 'Journal'],
    }
    return suggestions.get(life_phase, suggestions['other'])

# ─────────────────────────────────────────────
# 🏠 HOME / DASHBOARD
# ─────────────────────────────────────────────
DASHBOARD_QUOTES = [
    ("The secret of getting ahead is getting started.", "Mark Twain"),
    ("Small daily improvements are the key to staggering long-term results.", "Unknown"),
    ("You don't have to be great to start, but you have to start to be great.", "Zig Ziglar"),
    ("Focus on being productive instead of busy.", "Tim Ferriss"),
    ("It always seems impossible until it's done.", "Nelson Mandela"),
    ("Don't watch the clock; do what it does. Keep going.", "Sam Levenson"),
    ("The future depends on what you do today.", "Mahatma Gandhi"),
    ("Discipline is choosing between what you want now and what you want most.", "Abraham Lincoln"),
    ("Energy and persistence conquer all things.", "Benjamin Franklin"),
    ("Act as if what you do makes a difference. It does.", "William James"),
    ("Success is the sum of small efforts repeated day in and day out.", "Robert Collier"),
    ("Believe you can and you're halfway there.", "Theodore Roosevelt"),
    ("You are never too old to set another goal or to dream a new dream.", "C.S. Lewis"),
    ("Do something today that your future self will thank you for.", "Sean Patrick Flanery"),
    ("Hard work beats talent when talent doesn't work hard.", "Tim Notke"),
    ("Start where you are. Use what you have. Do what you can.", "Arthur Ashe"),
    ("The only way to do great work is to love what you do.", "Steve Jobs"),
    ("Study while others are sleeping; work while others are loafing.", "William A. Ward"),
    ("Push yourself, because no one else is going to do it for you.", "Unknown"),
    ("Great things never come from comfort zones.", "Unknown"),
]

@views.route('/sw.js')
def service_worker():
    response = send_from_directory(
        os.path.join(current_app.static_folder), 'sw.js'
    )
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Content-Type'] = 'application/javascript'
    return response

@views.route('/')
def home():
    if not current_user.is_authenticated:
        return render_template("home.html", active_page="home")

    today   = datetime.now(timezone.utc).date()
    now_h   = datetime.now(timezone.utc).hour
    dow     = today.weekday()  # 0=Mon…5=Sat, 6=Sun

    if now_h < 12:
        greeting = "Good morning"
    elif now_h < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    quote_text, quote_author = DASHBOARD_QUOTES[today.toordinal() % len(DASHBOARD_QUOTES)]

    # Tasks
    tasks_today   = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == today).order_by(Task.due_time).all()
    tasks_done    = sum(1 for t in tasks_today if t.completed)
    tasks_pending = [t for t in tasks_today if not t.completed][:5]

    # Habits — scoped to this month's HabitMonth
    today_str    = today.strftime('%Y-%m-%d')
    habit_month  = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
    habits_all   = habit_month.habits if habit_month else []
    logged_ids   = {l.habit_id for l in HabitLog.query.filter_by(date=today).filter(
                      HabitLog.habit_id.in_([h.id for h in habits_all])).all() if l.completed} if habits_all else set()
    habits_done = len(logged_ids)

    # Focus minutes today
    focus_sessions = FocusSession.query.filter_by(user_id=current_user.id, date=today, completed=True).all()
    focus_mins = sum(s.duration for s in focus_sessions) // 60 if focus_sessions else 0

    # Class slots today (only Mon–Sat)
    subjects    = Subject.query.filter_by(user_id=current_user.id).all()
    today_slots = []
    if dow <= 5:
        for s in subjects:
            for sl in s.slots:
                if sl.day_of_week == dow:
                    today_slots.append({
                        'type': 'class', 'label': s.name, 'color': s.color,
                        'start': sl.start_time, 'end': sl.end_time,
                        'room': sl.room or '', 'sort_key': sl.start_time,
                        'url': url_for('views.study_timetable'),
                    })

    # TimeBlocks today
    blocks_today = TimeBlock.query.filter_by(user_id=current_user.id, date=today).all()
    for b in blocks_today:
        h, m = divmod(b.start_minute, 60)
        today_slots.append({
            'type': 'block', 'label': b.title, 'color': b.color or '#6366f1',
            'start': f'{h:02d}:{m:02d}', 'end': '',
            'room': '', 'sort_key': f'{h:02d}:{m:02d}',
            'url': url_for('views.timemap'),
        })

    # Tasks with due_time → merge into schedule
    for t in tasks_today:
        if t.due_time:
            today_slots.append({
                'type': 'task', 'label': t.content, 'color': '#64748b',
                'start': t.due_time, 'end': t.end_time or '',
                'room': '', 'sort_key': t.due_time,
                'url': url_for('views.daily'),
            })

    today_slots.sort(key=lambda x: x['sort_key'])

    # Upcoming deadlines (exams + assignments + quizzes + projects)
    exams_upcoming = (Exam.query.join(Subject)
                      .filter(Subject.user_id == current_user.id, Exam.date >= today)
                      .order_by(Exam.date).limit(5).all())

    return render_template("dashboard.html",
        active_page   = 'dashboard',
        greeting      = greeting,
        quote_text    = quote_text,
        quote_author  = quote_author,
        today         = today,
        today_str     = today_str,
        tasks_today   = tasks_today,
        tasks_done    = tasks_done,
        tasks_pending = tasks_pending,
        habits_all    = habits_all,
        logged_ids    = logged_ids,
        habits_done   = habits_done,
        focus_mins    = focus_mins,
        today_slots   = today_slots,
        class_count   = len([s for s in today_slots if s['type'] == 'class']),
        exams_upcoming= exams_upcoming,
    )

# ─────────────────────────────────────────────
# 📅 DAILY TASKS
# ─────────────────────────────────────────────
@views.route('/daily', defaults={'date_str': None}, methods=['GET', 'POST'])
@views.route('/daily/<date_str>', methods=['GET', 'POST'])
@login_required
def daily(date_str):
    if date_str:
        current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        current_date = datetime.now(timezone.utc).replace(tzinfo=None).date()
    if request.method == 'POST':
        content = request.form.get('task')
        if content:
            raw_days = request.form.get('recurring_days', '').strip()
            recurring_days = raw_days if raw_days else None
            raw_offset = request.form.get('reminder_offset', '')
            reminder_offset = int(raw_offset) if raw_offset != '' else None
            new_task = Task(
                content=content,
                user_id=current_user.id,
                date=datetime.combine(current_date, datetime.min.time()),
                priority=request.form.get('priority') or None,
                due_time=request.form.get('due_time') or None,
                end_time=request.form.get('end_time') or None,
                recurring_days=recurring_days,
                reminder_offset=reminder_offset,
                category=request.form.get('category', '').strip() or None,
            )
            db.session.add(new_task)
            db.session.flush()
            upsert_category(current_user.id, new_task.category, request.form.get('category_color', ''))
            sync_task_timeblock(new_task)
            db.session.commit()
            flash('Task added!', category='success')
        return redirect(url_for('views.daily', date_str=current_date.strftime("%Y-%m-%d")))
    spawn_recurring_tasks(current_date)
    tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == current_date).all()
    notes = Note.query.filter_by(user_id=current_user.id, date=current_date).order_by(Note.created_at.desc()).all()
    yesterday_date = current_date - timedelta(days=1)
    yesterday = yesterday_date.strftime("%Y-%m-%d")
    tomorrow = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
    # Categories with colors
    cat_records = Category.query.filter_by(user_id=current_user.id).order_by(Category.name).all()
    cat_colors = {c.name: c.color for c in cat_records}
    used_categories = [c.name for c in cat_records]
    # Today's class slots (recurring timetable)
    dow = current_date.weekday()
    today_class_slots = []
    if dow <= 5:
        subjects_all = Subject.query.filter_by(user_id=current_user.id).all()
        for s in subjects_all:
            for sl in s.slots:
                if sl.day_of_week == dow:
                    today_class_slots.append({'subject': s, 'slot': sl})
        today_class_slots.sort(key=lambda x: x['slot'].start_time)
    # Personalized insights
    last_7_pcts = []
    for i in range(1, 8):
        d = current_date - timedelta(days=i)
        day_tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == d).all()
        if day_tasks:
            done_c = sum(1 for t in day_tasks if t.completed)
            last_7_pcts.append(done_c / len(day_tasks) * 100)
    last_week_pct = round(sum(last_7_pcts) / len(last_7_pcts)) if last_7_pcts else None
    yesterday_tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == yesterday_date).all()
    yesterday_done_count = sum(1 for t in yesterday_tasks if t.completed)
    today_done_count = sum(1 for t in tasks if t.completed)
    vs_yesterday = today_done_count - yesterday_done_count
    task_streak = 0
    check = yesterday_date
    while True:
        has_done = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == check, Task.completed == True).first()
        if has_done:
            task_streak += 1
            check -= timedelta(days=1)
        else:
            break
    productive_window = None
    if current_user.wake_time:
        try:
            h_w, m_w = map(int, current_user.wake_time.split(':'))
            s_h, e_h = h_w + 2, h_w + 4
            def _fmth(hh):
                if hh >= 24: hh -= 24
                if current_user.time_format == '12h':
                    p = 'AM' if hh < 12 else 'PM'
                    hh = hh % 12 or 12
                    return f"{hh} {p}"
                return f"{hh:02d}:00"
            productive_window = f"{_fmth(s_h)}–{_fmth(e_h)}"
        except Exception:
            pass
    return render_template("daily.html", tasks=tasks, notes=notes, today=current_date,
                           yesterday=yesterday, tomorrow=tomorrow,
                           used_categories=used_categories, cat_colors=cat_colors,
                           today_class_slots=today_class_slots,
                           last_week_pct=last_week_pct, task_streak=task_streak,
                           vs_yesterday=vs_yesterday, productive_window=productive_window,
                           active_page="daily")

# ─────────────────────────────────────────────
# 📅 WEEKLY TASKS
# ─────────────────────────────────────────────
@views.route('/weekly', defaults={'week_offset': 0})
@views.route('/weekly/<week_offset>')
@login_required
def weekly(week_offset):
    week_offset = int(week_offset)
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    start_of_week = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    end_of_week = start_of_week + timedelta(days=6)
    tasks = Task.query.filter(Task.user_id == current_user.id, Task.date >= start_of_week, Task.date < end_of_week + timedelta(days=1)).all()
    week_tasks = {start_of_week + timedelta(days=i): [] for i in range(7)}
    for task in tasks:
        week_tasks[task.date.date()].append(task)
    prev_week = week_offset - 1
    next_week = week_offset + 1
    # Weekly stats
    total_tasks_count = len(tasks)
    total_done = sum(1 for t in tasks if t.completed)
    completion_rate = round(total_done / total_tasks_count * 100) if total_tasks_count > 0 else 0
    day_done_counts = {d: sum(1 for t in dt if t.completed) for d, dt in week_tasks.items()}
    most_productive_day = max(day_done_counts, key=day_done_counts.get) if any(day_done_counts.values()) else None
    week_start_dt = datetime.combine(start_of_week, datetime.min.time())
    week_end_dt   = datetime.combine(end_of_week + timedelta(days=1), datetime.min.time())
    focus_sessions = FocusSession.query.filter(
        FocusSession.user_id == current_user.id,
        FocusSession.date >= week_start_dt,
        FocusSession.date <  week_end_dt,
        FocusSession.completed == True
    ).all()
    total_focus_secs     = sum(s.duration for s in focus_sessions)
    total_session_count  = len(focus_sessions)
    longest_session_secs = max((s.duration for s in focus_sessions), default=0)
    def _fmt_dur(secs):
        h, m = secs // 3600, (secs % 3600) // 60
        if h > 0:
            return f"{h}h {m}m" if m else f"{h}h"
        return f"{m}m" if m else "0m"
    cat_focus = {}
    for t in tasks:
        if t.focus_time and t.focus_time > 0:
            cat = t.category or 'Uncategorized'
            cat_focus[cat] = cat_focus.get(cat, 0) + t.focus_time
    top_category     = max(cat_focus, key=cat_focus.get) if cat_focus else None
    top_category_fmt = _fmt_dur(cat_focus[top_category]) if top_category else None
    return render_template("weekly.html",
        week_tasks=week_tasks, start_of_week=start_of_week, end_of_week=end_of_week,
        prev_week=prev_week, next_week=next_week,
        total_done=total_done, total_tasks_count=total_tasks_count,
        completion_rate=completion_rate, most_productive_day=most_productive_day,
        total_focus_fmt=_fmt_dur(total_focus_secs), total_focus_secs=total_focus_secs,
        total_session_count=total_session_count,
        longest_session_fmt=_fmt_dur(longest_session_secs), longest_session_secs=longest_session_secs,
        top_category=top_category, top_category_fmt=top_category_fmt,
        active_page="weekly")

# ─────────────────────────────────────────────
# 🗓️ TIME MAP
# ─────────────────────────────────────────────
@views.route('/timemap', defaults={'date_str': None})
@views.route('/timemap/<date_str>')
@login_required
def timemap(date_str):
    if date_str:
        current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        current_date = datetime.now(timezone.utc).replace(tzinfo=None).date()
    blocks = TimeBlock.query.filter_by(
        user_id=current_user.id, date=current_date
    ).order_by(TimeBlock.start_minute).all()
    tasks = Task.query.filter(
        Task.user_id == current_user.id,
        db.func.date(Task.date) == current_date
    ).all()
    blocks_data = [
        {'id': b.id, 'title': b.title, 'start_minute': b.start_minute,
         'end_minute': b.end_minute, 'block_type': b.block_type,
         'category': b.category, 'color': b.color,
         'completed': b.completed, 'task_id': b.task_id}
        for b in blocks
    ]
    yesterday = (current_date - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow  = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")

    # ── Weekly data ──
    week_start = current_date - timedelta(days=current_date.weekday())
    week_days  = [week_start + timedelta(days=i) for i in range(7)]
    week_raw   = TimeBlock.query.filter(
        TimeBlock.user_id == current_user.id,
        TimeBlock.date >= week_start,
        TimeBlock.date <= week_days[-1]
    ).order_by(TimeBlock.start_minute).all()
    week_blocks_by_day = {d.strftime('%Y-%m-%d'): [] for d in week_days}
    for b in week_raw:
        key = b.date.strftime('%Y-%m-%d')
        if key in week_blocks_by_day:
            week_blocks_by_day[key].append({
                'id': b.id, 'title': b.title,
                'start_minute': b.start_minute, 'end_minute': b.end_minute,
                'block_type': b.block_type, 'color': b.color, 'completed': b.completed
            })

    # ── Monthly data ──
    _, days_in_month = calendar.monthrange(current_date.year, current_date.month)
    month_start = current_date.replace(day=1)
    month_end   = current_date.replace(day=days_in_month)
    month_raw   = TimeBlock.query.filter(
        TimeBlock.user_id == current_user.id,
        TimeBlock.date >= month_start,
        TimeBlock.date <= month_end
    ).all()
    month_blocks_by_day = {}
    for b in month_raw:
        key = b.date.strftime('%Y-%m-%d')
        month_blocks_by_day.setdefault(key, []).append({
            'color': b.color, 'block_type': b.block_type, 'completed': b.completed
        })

    # Convert stored wake_time ("7 AM") to HH:MM for the time input
    raw_wake = current_user.wake_time or '7 AM'
    try:
        wake_hhmm = datetime.strptime(raw_wake.strip(), '%I %p').strftime('%H:%M')
    except ValueError:
        wake_hhmm = '07:00'
    cat_records  = Category.query.filter_by(user_id=current_user.id).order_by(Category.name).all()
    task_cat_map = {t.id: {'category': t.category, 'color': next((c.color for c in cat_records if c.name == t.category), None)} for t in tasks}
    return render_template("timemap.html", blocks=blocks, blocks_data=blocks_data,
                           tasks=tasks, today=current_date,
                           yesterday=yesterday, tomorrow=tomorrow,
                           wake_hhmm=wake_hhmm,
                           week_days=week_days, week_blocks_by_day=week_blocks_by_day,
                           month_start=month_start, days_in_month=days_in_month,
                           month_blocks_by_day=month_blocks_by_day,
                           categories=cat_records, task_cat_map=task_cat_map,
                           active_page="timemap")


@views.route('/timemap/add-block', methods=['POST'])
@login_required
def add_time_block():
    data = request.get_json() or {}
    date_str = data.get('date', datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    try:
        block_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        block_date = datetime.now(timezone.utc).date()
    block = TimeBlock(
        user_id      = current_user.id,
        date         = block_date,
        start_minute = int(data.get('start_minute', 540)),
        end_minute   = int(data.get('end_minute', 600)),
        title        = str(data.get('title', 'Untitled'))[:200],
        block_type   = data.get('block_type', 'custom'),
        category     = data.get('category') or None,
        task_id      = data.get('task_id') or None,
        color        = data.get('color') or None,
    )
    db.session.add(block)
    db.session.commit()
    return jsonify({'status': 'success', 'id': block.id})


@views.route('/timemap/delete-block/<int:id>', methods=['POST'])
@login_required
def delete_time_block(id):
    block = TimeBlock.query.get_or_404(id)
    if block.user_id != current_user.id:
        abort(403)
    db.session.delete(block)
    db.session.commit()
    return jsonify({'status': 'success'})


@views.route('/timemap/update-block/<int:id>', methods=['POST'])
@login_required
def update_time_block(id):
    block = TimeBlock.query.get_or_404(id)
    if block.user_id != current_user.id:
        abort(403)
    data = request.get_json() or {}
    if 'title'        in data: block.title        = str(data['title'])[:200]
    if 'start_minute' in data: block.start_minute = int(data['start_minute'])
    if 'end_minute'   in data: block.end_minute   = int(data['end_minute'])
    if 'block_type'   in data: block.block_type   = data['block_type']
    if 'category'     in data: block.category     = data['category'] or None
    if 'color'        in data: block.color        = data['color'] or None
    if 'completed'    in data: block.completed    = bool(data['completed'])
    db.session.commit()
    return jsonify({'status': 'success'})


# ─────────────────────────────────────────────
# ✨ GENERATE SCHEDULE (AI)
# ─────────────────────────────────────────────
@views.route('/timemap/generate-schedule', methods=['POST'])
@login_required
def timemap_generate_schedule():
    data = request.get_json(silent=True) or {}
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'no key'}), 500

    lp = current_user.life_phase or 'other'
    age_map = {'school': 'High School Student', 'college': 'University Student',
               'working': 'Working Professional', 'other': 'General'}
    occ_map = {'school': 'Student', 'college': 'Student',
               'working': 'Professional', 'other': 'General'}

    from datetime import date as _date
    today_dow = _date.today().weekday()
    slots = ClassSlot.query.join(Subject).filter(
        Subject.user_id == current_user.id,
        ClassSlot.day_of_week == today_dow
    ).all()
    fixed_lines = [f"- {s.subject.name}: {s.start_time} – {s.end_time}" for s in slots]
    extra = data.get('fixed_events', '').strip()
    if extra:
        fixed_lines.append(extra)
    fixed_str = '\n'.join(fixed_lines) or 'None'

    today_tasks = Task.query.filter(
        Task.user_id == current_user.id,
        db.func.date(Task.date) == _date.today()
    ).all()
    tasks_json = json.dumps([{
        'name': t.content,
        'priority': (t.priority or 'medium').upper()
    } for t in today_tasks], indent=2)

    ws_map = {
        'deep': 'Deep Focus (long uninterrupted blocks)',
        'flexible': 'Flexible (short adaptable sessions)',
        'mixed': 'Mixed (balance of both)'
    }

    prompt = f"""You are Monad's Personal Planning Engine. Create a realistic, personalized daily schedule.

USER PROFILE
Name: {current_user.name or ''}
Age Group: {age_map.get(lp, 'General')}
Occupation: {occ_map.get(lp, 'General')}
Primary Goals: {current_user.main_goal or current_user.year_goal or 'Not specified'}
Biggest Challenges: {current_user.challenges or 'Not specified'}
Productivity Style: {ws_map.get(current_user.available_time or '', 'Mixed')}

DAILY RHYTHM
Wake Up Time: {current_user.wake_time or '07:00'}
Sleep Time: {current_user.sleep_time or '23:00'}
Breakfast: {data.get('breakfast_time', '08:00')}
Lunch: {data.get('lunch_time', '13:00')}
Dinner: {data.get('dinner_time', '19:00')}

FIXED COMMITMENTS
{fixed_str}

TASKS TO SCHEDULE
{tasks_json}

SCHEDULING RULES
1. Estimate a realistic duration for each task from its name — e.g. "Physics revision" → 60–90 min, "Read chapter 3" → 45 min, "Reply emails" → 20 min. Do NOT use a fixed 30-min default.
2. Schedule HIGH priority tasks during highest-energy hours (morning for most people)
3. Avoid deep work immediately after meals — allow 20-30 min digestion first
4. Insert 5-15 min breaks between major work sessions
5. Never schedule tasks during sleep hours or fixed commitments
6. Group similar tasks together to minimize context switching
7. If time is tight, skip LOW priority tasks rather than cramming
8. Leave at least one 15-min buffer for unexpected events
9. Reflect the user's year goal: {current_user.year_goal or 'Not set'}

Return ONLY valid JSON in this exact format:
{{
  "focus_summary": "One short paragraph about what matters most today.",
  "blocks": [
    {{"title": "Morning Routine", "start": "07:00", "end": "07:30", "type": "personal"}},
    {{"title": "Physics Revision", "start": "08:00", "end": "09:30", "type": "study"}}
  ],
  "why_this_works": ["insight 1", "insight 2", "insight 3"]
}}

Block type must be one of: study, work, personal, health, break, custom
Start and end must be HH:MM (24-hour)."""

    try:
        text = _gemini_call(api_key, prompt, timeout=60)
        return jsonify(json.loads(text))
    except Exception as e:
        return jsonify({'error': 'ai_failed', 'detail': str(e)}), 500


# ─────────────────────────────────────────────
# ✅ TOGGLE TASK
# ─────────────────────────────────────────────
@views.route('/toggle/<int:id>', methods=['GET', 'POST'])
@login_required
def toggle_task(id):
    task = Task.query.get_or_404(id)
    if task.user_id != current_user.id:
        abort(403)
    task.completed = not task.completed
    if not task.completed:
        task.quick_note = None
    db.session.commit()
    if task.completed:
        return redirect(url_for('views.quick_note', id=task.id))
    return redirect(request.referrer)

# ─────────────────────────────────────────────
# ✍️ QUICK NOTE
# ─────────────────────────────────────────────
@views.route('/quick-note/<int:id>', methods=['GET', 'POST'])
@login_required
def quick_note(id):
    task = Task.query.get_or_404(id)
    if task.user_id != current_user.id:
        abort(403)
    prompt = get_smart_prompt(task.content)
    if request.method == 'POST':
        note = request.form.get('quick_note')
        if note:
            task.quick_note = note
            db.session.commit()
            flash('Note saved! ✅', category='success')
        return redirect(url_for('views.daily'))
    return render_template("quick_note.html", task=task, prompt=prompt)

# ─────────────────────────────────────────────
# 📅 RESCHEDULE TASK
# ─────────────────────────────────────────────
@views.route('/reschedule/<int:id>', methods=['POST'])
@login_required
def reschedule_task(id):
    task = Task.query.get_or_404(id)
    if task.user_id != current_user.id:
        abort(403)
    date_str = request.form.get('new_date', '').strip()
    try:
        new_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        task.date = datetime.combine(new_date, datetime.min.time())
        db.session.commit()
        flash(f'Task moved to {new_date.strftime("%B %d")}!', category='success')
    except (ValueError, TypeError):
        flash('Invalid date.', category='error')
    return redirect(request.referrer or url_for('views.daily'))

# ─────────────────────────────────────────────
# ✏️ EDIT TASK
# ─────────────────────────────────────────────
@views.route('/edit/<int:id>', methods=['POST'])
@login_required
def edit_task(id):
    task = Task.query.get_or_404(id)
    if task.user_id != current_user.id:
        abort(403)
    content = request.form.get('content', '').strip()
    if content:
        task.content = content
    task.priority = request.form.get('priority') or None
    task.due_time = request.form.get('due_time') or None
    raw_days = request.form.get('recurring_days', '').strip()
    if not task.recurring_task_id:
        task.recurring_days = raw_days if raw_days else None
    raw_offset = request.form.get('reminder_offset', '')
    task.reminder_offset = int(raw_offset) if raw_offset != '' else None
    task.category = request.form.get('category', '').strip() or None
    task.end_time = request.form.get('end_time') or None
    upsert_category(current_user.id, task.category, request.form.get('category_color', ''))
    sync_task_timeblock(task)
    db.session.commit()
    flash('Task updated!', category='success')
    return redirect(request.referrer or url_for('views.daily'))

# ─────────────────────────────────────────────
# 🗑️ DELETE TASK
# ─────────────────────────────────────────────
@views.route('/delete/<int:id>')
@login_required
def delete_task(id):
    task = Task.query.get_or_404(id)
    if task.user_id != current_user.id:
        flash("You cannot delete this task.", category="error")
        return redirect(request.referrer)
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted!", category="success")
    return redirect(request.referrer)

# ─────────────────────────────────────────────
# 📝 NOTES
# ─────────────────────────────────────────────
@views.route('/note/add', methods=['POST'])
@login_required
def add_note():
    content = request.form.get('content', '').strip()
    date_str = request.form.get('date')
    try:
        note_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        note_date = datetime.now(timezone.utc).replace(tzinfo=None).date()
    if content:
        db.session.add(Note(user_id=current_user.id, content=content, date=note_date))
        db.session.commit()
    return redirect(url_for('views.daily', date_str=note_date.strftime('%Y-%m-%d')))

@views.route('/note/delete/<int:id>')
@login_required
def delete_note(id):
    note = Note.query.get_or_404(id)
    if note.user_id != current_user.id:
        abort(403)
    date_str = note.date.strftime('%Y-%m-%d')
    db.session.delete(note)
    db.session.commit()
    return redirect(url_for('views.daily', date_str=date_str))

# ─────────────────────────────────────────────
# 📅 HABIT TRACKER
# ─────────────────────────────────────────────
@views.route('/habits/<int:year>/<int:month>', methods=['GET', 'POST'])
@login_required
def habits_monthly(year, month):
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=year, month=month).first()
    if not habit_month:
        habit_month = HabitMonth(user_id=current_user.id, year=year, month=month)
        db.session.add(habit_month)
        db.session.commit()
    # Carry forward habits from the most recent prior month if this month has
    # none yet, so they don't have to be re-added every month.
    if not Habit.query.filter_by(habit_month_id=habit_month.id).first():
        prev_habit_month = (HabitMonth.query
            .filter_by(user_id=current_user.id)
            .filter(db.or_(HabitMonth.year < year,
                            db.and_(HabitMonth.year == year, HabitMonth.month < month)))
            .order_by(HabitMonth.year.desc(), HabitMonth.month.desc())
            .first())
        if prev_habit_month:
            prev_habits = Habit.query.filter_by(habit_month_id=prev_habit_month.id).all()
            for h in prev_habits:
                db.session.add(Habit(name=h.name, frequency=h.frequency, habit_month_id=habit_month.id))
            db.session.commit()
    if request.method == 'POST':
        habit_name = request.form.get('habit')
        if habit_name:
            frequency = request.form.get('frequency', 'daily')
            if frequency == 'custom':
                custom_days = request.form.get('custom_days', '').strip()
                frequency = f'days:{custom_days}' if custom_days else 'daily'
            new_habit = Habit(name=habit_name, habit_month_id=habit_month.id, frequency=frequency)
            db.session.add(new_habit)
            db.session.commit()
            flash('Habit added!', category='success')
        return redirect(url_for('views.habits_monthly', year=year, month=month))
    habits = Habit.query.filter_by(habit_month_id=habit_month.id).all()
    suggested_habits = get_habit_suggestions(current_user.life_phase or 'other')
    days_in_month = calendar.monthrange(year, month)[1]
    habit_progress = compute_habit_progress(habits, year, month)
    return render_template("habits_monthly.html", habits=habits, year=year, month=month,
                           suggested_habits=suggested_habits, habit_progress=habit_progress,
                           days_in_month=days_in_month, active_page="habits")

@views.route('/habit/delete/<int:id>')
@login_required
def delete_habit(id):
    habit = Habit.query.get_or_404(id)
    habit_month = db.session.get(HabitMonth, habit.habit_month_id)
    if not habit_month or habit_month.user_id != current_user.id:
        abort(403)
    db.session.delete(habit)
    db.session.commit()
    flash('Habit deleted!', category='success')
    return redirect(request.referrer)

@views.route('/habit/toggle/<int:id>/<date_str>', methods=['GET', 'POST'])
@login_required
def toggle_habit(id, date_str):
    habit = Habit.query.get_or_404(id)
    habit_month = db.session.get(HabitMonth, habit.habit_month_id)
    if not habit_month or habit_month.user_id != current_user.id:
        abort(403)
    date = datetime.strptime(date_str, "%Y-%m-%d").date()
    log = HabitLog.query.filter_by(habit_id=habit.id, date=date).first()
    if log:
        log.completed = not log.completed
    else:
        log = HabitLog(habit_id=habit.id, date=date, completed=True)
        db.session.add(log)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        from flask import jsonify
        return jsonify({'completed': log.completed, 'log_id': log.id})
    if log.completed:
        flash(date_str, 'habit_photo')
    return redirect(request.referrer)

@views.route('/habit/reflect', methods=['POST'])
@login_required
def habit_reflect():
    from flask import jsonify
    log_id = request.form.get('log_id')
    reflection = request.form.get('reflection', '').strip()

    log = HabitLog.query.get_or_404(int(log_id))
    habit = Habit.query.get(log.habit_id)
    habit_month = db.session.get(HabitMonth, habit.habit_month_id)
    if not habit_month or habit_month.user_id != current_user.id:
        abort(403)

    log.reflection = reflection or None
    db.session.commit()

    if 'photo' in request.files:
        file = request.files['photo']
        if file and file.filename:
            if allowed_file(file.filename):
                filename = secure_filename(
                    f"{current_user.id}_{log.date}_{int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp())}_{file.filename}"
                )
                storage_path =  upload_file(file, "daily_photos")
                photo = DailyPhoto(
                    user_id=current_user.id,
                    date=log.date,
                    filename=storage_path,
                    caption=reflection[:300] if reflection else habit.name,
                    habit_log_id=log.id
                    )
               
                db.session.add(photo)
                db.session.commit()

    return jsonify({'ok': True})

@views.route('/habit/streaks/<int:year>/<int:month>')
@login_required
def habit_streaks(year, month):
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=year, month=month).first()
    if not habit_month:
        flash("No habits found for this month.", category="error")
        return redirect(url_for('views.habits_monthly', year=year, month=month))
    habits = Habit.query.filter_by(habit_month_id=habit_month.id).all()
    streaks = {}
    for habit in habits:
        logs = HabitLog.query.filter_by(habit_id=habit.id, completed=True).order_by(HabitLog.date.desc()).all()
        streak = 0
        last_date = None
        for log in logs:
            if last_date is None or log.date == last_date - timedelta(days=1):
                streak += 1
                last_date = log.date
            else:
                break
        streaks[habit.name] = streak
    return render_template("habit_streaks.html", streaks=streaks, year=year, month=month, active_page="habits")

# ─────────────────────────────────────────────
# 🍅 POMODORO
# ─────────────────────────────────────────────
@views.route('/pomodoro', methods=['GET'])
@views.route('/pomodoro/<int:task_id>', methods=['GET'])
@login_required
def pomodoro(task_id=None):
    mode = request.args.get('mode', 'pomodoro')
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == today, Task.completed == False).all()
    selected_task = Task.query.get_or_404(task_id) if task_id else None
    return render_template("pomodoro.html", tasks=tasks, selected_task=selected_task, mode=mode, active_page="pomodoro")

@views.route('/pomodoro/save', methods=['POST'])
@login_required
def save_pomodoro():
    data = request.get_json()
    task_id = data.get('task_id')
    duration_minutes = data.get('duration', 0)
    mode = data.get('mode', 'pomodoro')
    partial = bool(data.get('partial', False))
    label = (data.get('label') or '').strip()[:80]
    if not duration_minutes or duration_minutes <= 0:
        return jsonify({'status': 'skipped'})

    duration_seconds = duration_minutes * 60

    # A task is optional — sessions started without one are still saved.
    task = Task.query.get(task_id) if task_id else None
    if task and task.user_id != current_user.id:
        task = None
    if task and not partial:
        task.focus_time = (task.focus_time or 0) + duration_seconds
        task.session_count = (task.session_count or 0) + 1

    session = FocusSession(
        task_id=task.id if task else None, user_id=current_user.id,
        duration=duration_seconds, mode=mode, completed=not partial,
        label=None if task else (label or None)
    )
    try:
        db.session.add(session)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('focus session save failed')
        return jsonify({'status': 'error', 'reason': str(e)[:200]}), 500
    return jsonify({'status': 'ok'})

@views.route('/pomodoro/sessions', methods=['GET'])
@login_required
def get_focus_sessions():
    sessions = (FocusSession.query
                .filter_by(user_id=current_user.id)
                .order_by(FocusSession.date.desc())
                .limit(20).all())
    result = []
    for s in sessions:
        task_name = s.label or (s.task.content if s.task else 'Focus session')
        result.append({
            'task_name': task_name,
            'session_mins': max(1, round(s.duration / 60)),
            'date': f"{s.date.day} {s.date.strftime('%b')}" if s.date else '',
            'wilted': not s.completed,
        })
    return jsonify(result)

# ─────────────────────────────────────────────
# 📔 DAILY JOURNAL
# ─────────────────────────────────────────────
@views.route('/journal', defaults={'date_str': None}, methods=['GET', 'POST'])
@views.route('/journal/<date_str>', methods=['GET', 'POST'])
@login_required
def daily_journal(date_str):
    if date_str:
        review_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        review_date = datetime.now(timezone.utc).replace(tzinfo=None).date()
    tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == review_date).all()
    completed_tasks = [t for t in tasks if t.completed]
    missed_tasks = [t for t in tasks if not t.completed]
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=review_date.year, month=review_date.month).first()
    habits_status = []
    if habit_month:
        habits = Habit.query.filter_by(habit_month_id=habit_month.id).all()
        for habit in habits:
            log = HabitLog.query.filter_by(habit_id=habit.id, date=review_date).first()
            habits_status.append({'name': habit.name, 'done': log.completed if log else False})
    journal = DailyJournal.query.filter_by(user_id=current_user.id, date=review_date).first()
    if request.method == 'POST':
        mood = request.form.get('mood')
        if journal:
            journal.mood = mood
            journal.accomplishments = request.form.get('accomplishments')
            journal.improvements = request.form.get('improvements')
            journal.learnings = request.form.get('learnings')
            journal.gratitude = request.form.get('gratitude')
        else:
            journal = DailyJournal(user_id=current_user.id, date=review_date, mood=mood,
                                   accomplishments=request.form.get('accomplishments'),
                                   improvements=request.form.get('improvements'),
                                   learnings=request.form.get('learnings'),
                                   gratitude=request.form.get('gratitude'))
            db.session.add(journal)
        db.session.commit()
        flash('Journal saved! 🌟', category='success')
        return redirect(url_for('views.daily_journal', date_str=review_date.strftime('%Y-%m-%d')))
    yesterday = (review_date - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (review_date + timedelta(days=1)).strftime("%Y-%m-%d")
    show_tomorrow = (review_date + timedelta(days=1)) <= datetime.now(timezone.utc).replace(tzinfo=None).date()
    return render_template("daily_journal.html", review_date=review_date, tasks=tasks,
                           completed_tasks=completed_tasks, missed_tasks=missed_tasks,
                           habits_status=habits_status, journal=journal,
                           yesterday=yesterday, tomorrow=tomorrow, show_tomorrow=show_tomorrow,
                           active_page="journal")

# ─────────────────────────────────────────────
# ⚙️ SETTINGS
# ─────────────────────────────────────────────
@views.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        theme = request.form.get('theme', 'default')
        new_name = request.form.get('name', '').strip()
        life_phase = request.form.get('life_phase', current_user.life_phase or 'other')
        current_user.theme = theme
        if new_name:
            current_user.name = new_name
        current_user.life_phase = life_phase
        current_user.time_format    = request.form.get('time_format', '12h')
        year_goal_val = request.form.get('year_goal', '').strip()[:200]
        current_user.year_goal      = year_goal_val if year_goal_val else None
        current_user.study_enabled  = request.form.get('study_enabled') == '1'
        db.session.commit()
        flash('Preferences saved!', category='success')
        return redirect(url_for('views.settings'))
    total_focus_sessions = FocusSession.query.filter_by(user_id=current_user.id, completed=True).count()
    return render_template("settings.html", active_page="settings", total_focus_sessions=total_focus_sessions)

# ─────────────────────────────────────────────
# 🧭 ONBOARDING
# ─────────────────────────────────────────────

def _gemini_call(api_key, prompt, timeout=15):
    import requests as _req
    resp = _req.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}',
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        },
        timeout=timeout
    )
    resp.raise_for_status()
    return resp.json()['candidates'][0]['content']['parts'][0]['text']


@views.route('/onboarding/suggest-habits', methods=['POST'])
@login_required
def onboarding_suggest_habits():
    data = request.get_json(silent=True) or {}
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return jsonify({'habits': []}), 500
    prompt = f"""You are helping a user set up a personal productivity system called Monad.

Based on this person's profile, suggest exactly 5 specific, actionable daily habits.

Profile:
- Name: {data.get('name', '')}
- Life stage: {data.get('life_stage', '')}
- Wake time: {data.get('wake_time', '')} / Sleep time: {data.get('sleep_time', '')}
- Biggest challenge: {data.get('challenge', '')}
- Work style: {data.get('work_style', '')}
- Goals: {data.get('goals', '')}
- Year goal: {data.get('year_goal', '')}

Rules:
- Each habit must be specific and completable daily (not vague like "exercise" — instead "30-minute morning walk")
- Match the user's life stage and goals closely
- Vary the categories: study/work, health, mindset, routine
- Keep each habit under 8 words
- Return ONLY valid JSON: {{"habits": ["habit 1", "habit 2", ...]}}"""
    try:
        text = _gemini_call(api_key, prompt)
        return jsonify(json.loads(text))
    except Exception:
        return jsonify({'habits': []}), 500


@views.route('/onboarding/analyze', methods=['POST'])
@login_required
def onboarding_analyze():
    data = request.get_json(silent=True) or {}
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'no key'}), 500
    name            = data.get('name', '')
    life_stage      = data.get('life_stage', '')
    wake_time       = data.get('wake_time', '')
    sleep_time      = data.get('sleep_time', '')
    challenge       = data.get('challenge', '')
    work_style      = data.get('work_style', '')
    goals           = data.get('goals', '')
    selected_habits = data.get('selected_habits', '')
    year_goal       = data.get('year_goal', '')
    prompt = f"""You are the onboarding intelligence behind Monad.

Monad is a personal operating system that helps people live intentionally.

Analyze this user's onboarding responses and generate a highly personalized profile.

USER PROFILE
Name: {name}
Life Stage: {life_stage}
Wake Time: {wake_time}
Sleep Time: {sleep_time}
Biggest Challenge: {challenge}
Work Style: {work_style}
Goals: {goals}
Selected Habits: {selected_habits}
Year Goal: {year_goal}

TASKS

Generate:
1. A Personal Insight
2. Three Starter Tasks
3. A Personal Motto

PERSONAL INSIGHT REQUIREMENTS
Write exactly 2 sentences.
- Speak directly to the user
- Feel thoughtful and specific
- Infer a behavioral pattern from their answers
- Mention a likely strength and a likely obstacle, connected together
- Sound like a thoughtful coach, never like a productivity app
- Never use: "Stay productive", "Achieve your goals", "Unlock your potential", "Reach success", "Stay organized"
- Do NOT repeat their answers or list their goals

STARTER TASK REQUIREMENTS
Generate exactly 3 tasks:
- Achievable today, under 30 minutes each
- Create momentum and match their goals/challenge/work style
- Be specific but concise — under 10 words each (not "Study" — instead "Review one chapter's notes for 20 min")

PERSONAL MOTTO REQUIREMENTS
One short sentence, under 12 words, that reflects their personality and goals.
Sound meaningful rather than inspirational.

Return ONLY valid JSON:
{{"insight": "", "starter_tasks": ["", "", ""], "motto": ""}}"""
    try:
        text = _gemini_call(api_key, prompt)
        return jsonify(json.loads(text))
    except Exception:
        return jsonify({'error': 'ai_failed'}), 500


@views.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if current_user.onboarding_complete:
        return redirect(url_for('views.home'))
    if request.method == 'POST':
        name = request.form.get('name', current_user.name)
        theme = request.form.get('theme', 'default')
        life_phase = request.form.get('life_stage', 'other')
        wake_time = request.form.get('wake_time', '')
        sleep_time = request.form.get('sleep_time', '')
        routine_text = request.form.get('routine_text', '').strip()
        selected_habits = request.form.getlist('habits')
        custom_habits = request.form.get('custom_habits', '')
        main_goal = request.form.get('main_goal', '').strip()
        challenges = request.form.get('challenges', '').strip()
        available_time = request.form.get('available_time', '').strip()
        current_user.name = name
        current_user.theme = theme
        year_goal_val = request.form.get('year_goal', '').strip()[:200]
        current_user.year_goal = year_goal_val if year_goal_val else None
        current_user.time_format = request.form.get('time_format', '12h')
        current_user.life_phase = life_phase
        current_user.main_goal = main_goal
        current_user.challenges = challenges
        current_user.available_time = available_time
        current_user.wake_time = wake_time
        current_user.sleep_time = sleep_time
        current_user.routine_text = routine_text
        current_user.onboarding_complete = True
        db.session.commit()
        # Save institution profile if school/college was chosen
        inst_type = request.form.get('inst_type', '').strip()
        if inst_type in ('school', 'college'):
            profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
            if not profile:
                profile = StudentProfile(user_id=current_user.id)
                db.session.add(profile)
            profile.institution_type = inst_type
            profile.institution      = request.form.get('inst_name', '').strip() or None
            profile.course           = request.form.get('inst_course', '').strip() or None
            profile.roll_number      = request.form.get('inst_roll', '').strip() or None
            if inst_type == 'school':
                profile.class_name   = request.form.get('inst_class', '').strip() or None
                profile.section      = request.form.get('inst_section', '').strip() or None
            else:
                profile.department   = request.form.get('inst_department', '').strip() or None
                profile.year         = request.form.get('inst_year', '').strip() or None
                profile.semester     = request.form.get('inst_semester', '').strip() or None
            db.session.commit()
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
        if not habit_month:
            habit_month = HabitMonth(user_id=current_user.id, year=today.year, month=today.month)
            db.session.add(habit_month)
            db.session.flush()
        added_habits = set()
        for habit_name in selected_habits:
            habit_name = habit_name.strip()
            if habit_name and habit_name not in added_habits:
                new_habit = Habit(name=habit_name, habit_month_id=habit_month.id)
                db.session.add(new_habit)
                added_habits.add(habit_name)
        if custom_habits.strip():
            for h in custom_habits.replace('\n', ',').split(','):
                h = h.strip()
                if h and h not in added_habits:
                    new_habit = Habit(name=h, habit_month_id=habit_month.id)
                    db.session.add(new_habit)
                    added_habits.add(h)
        db.session.commit()
        # Save AI insight + motto
        ai_summary_val = request.form.get('ai_summary', '').strip()
        if ai_summary_val:
            current_user.ai_summary = ai_summary_val
        motto_val = request.form.get('motto', '').strip()
        if motto_val:
            current_user.motto = motto_val
        # Create AI-generated starter tasks for today
        today_dt = datetime.now(timezone.utc).replace(tzinfo=None)
        for task_content in request.form.getlist('starter_task'):
            task_content = task_content.strip()
            if task_content:
                db.session.add(Task(content=task_content, user_id=current_user.id,
                                    date=today_dt, priority='high'))
        db.session.commit()
        flash(f'Welcome aboard! {len(added_habits)} habits added. ✨', 'success')
        return redirect(url_for('views.home'))
    suggested_habits = get_habit_suggestions(current_user.life_phase or 'other')
    themes = ['default', 'ocean', 'forest', 'sunset', 'dark', 'haikyuu']
    from datetime import datetime as _dt
    return render_template("onboarding.html", suggested_habits=suggested_habits, themes=themes, active_page="onboarding", now=_dt.utcnow())

# ─────────────────────────────────────────────
# 📸 MEMORIES
# ─────────────────────────────────────────────
@views.route('/memories', defaults={'year': None, 'month': None})
@views.route('/memories/<int:year>/<int:month>')
@login_required
def memories(year, month):
    if not year or not month:
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        year, month = today.year, today.month
    
    start_date = datetime(year, month, 1).date()
    if month == 12:
        end_date = datetime(year+1, 1, 1).date()
    else:
        end_date = datetime(year, month+1, 1).date()
    
    # Get ONLY final photos for the grid
    final_photos = DailyPhoto.query.filter(
        DailyPhoto.user_id == current_user.id,
        DailyPhoto.date >= start_date,
        DailyPhoto.date < end_date,
        DailyPhoto.is_final == True
    ).all()
    
    final_photo_by_day = {}
    for p in final_photos:
        final_photo_by_day[p.date.day] = p.filename
    
    # Get photo count for badge
    all_photos = DailyPhoto.query.filter(
        DailyPhoto.user_id == current_user.id,
        DailyPhoto.date >= start_date,
        DailyPhoto.date < end_date
    ).all()
    photo_count_by_day = {}
    for p in all_photos:
        photo_count_by_day[p.date.day] = photo_count_by_day.get(p.date.day, 0) + 1
    
    # Get journals
    journals = DailyJournal.query.filter(
        DailyJournal.user_id == current_user.id,
        DailyJournal.date >= start_date,
        DailyJournal.date < end_date
    ).all()
    journal_by_day = {j.date.day: j for j in journals}
    
    cal = calendar.Calendar()
    calendar_matrix = cal.monthdayscalendar(year, month)
    month_name = datetime(year, month, 1).strftime('%B %Y')
    
    if month == 1:
        prev_month, prev_year = 12, year-1
        next_month, next_year = month+1, year
    elif month == 12:
        prev_month, prev_year = month-1, year
        next_month, next_year = 1, year+1
    else:
        prev_month, prev_year = month-1, year
        next_month, next_year = month+1, year
    
    return render_template("memories.html",
                         calendar_matrix=calendar_matrix,
                         final_photo_by_day=final_photo_by_day,
                         photo_count_by_day=photo_count_by_day,
                         journal_by_day=journal_by_day,
                         month_name=month_name,
                         year=year, month=month,
                         prev_month=prev_month, prev_year=prev_year,
                         next_month=next_month, next_year=next_year,
                         active_page="memories")

@views.route('/memories/day/<date_str>')
@login_required
def memory_day(date_str):
    day_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    all_photos = DailyPhoto.query.filter_by(user_id=current_user.id, date=day_date).order_by(DailyPhoto.created_at.desc()).all()
    journal = DailyJournal.query.filter_by(user_id=current_user.id, date=day_date).first()
    tasks = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == day_date).all()
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=day_date.year, month=day_date.month).first()
    habits_status = []
    habit_photo_ids = set()
    if habit_month:
        for habit in Habit.query.filter_by(habit_month_id=habit_month.id).all():
            log = HabitLog.query.filter_by(habit_id=habit.id, date=day_date).first()
            habit_photo = DailyPhoto.query.filter_by(habit_log_id=log.id).first() if log else None
            if habit_photo:
                habit_photo_ids.add(habit_photo.id)
            habits_status.append({
                'name': habit.name,
                'done': log.completed if log else False,
                'reflection': log.reflection if log else None,
                'photo': habit_photo,
            })
    # Exclude habit-specific photos from general grid (shown inline with habits instead)
    general_photos = [p for p in all_photos if p.id not in habit_photo_ids]
    return render_template("memory_day.html", day_date=day_date, all_photos=general_photos,
                         journal=journal, tasks=tasks, habits_status=habits_status,
                         active_page="memories")

@views.route('/upload-photo', methods=['POST'])
@login_required
def upload_photo():
    date_str = request.form.get(
        'date',
        datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d')
    )
    day_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    task_id = request.form.get('task_id', None)

    if 'photo' not in request.files:
        flash('No file selected', 'error')
        return redirect(request.referrer or url_for('views.daily'))

    file = request.files['photo']

    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(request.referrer or url_for('views.daily'))

    if file and allowed_file(file.filename):

        # Upload to Supabase
        storage_path = upload_file(file, "daily_photos")

        caption = request.form.get('caption', '')

        if task_id and not caption:
            task = Task.query.get(int(task_id))
            if task:
                caption = f"Task: {task.content}"

        photo = DailyPhoto(
            user_id=current_user.id,
            date=day_date,
            filename=storage_path,
            caption=caption,
            task_id=int(task_id) if task_id else None
        )

        db.session.add(photo)
        db.session.commit()

        flash('Photo uploaded! 📸', 'success')

    return redirect(request.referrer or url_for('views.daily'))

@views.route('/set-final-photo/<int:photo_id>')
@login_required
def set_final_photo(photo_id):
    photo = DailyPhoto.query.get_or_404(photo_id)
    if photo.user_id != current_user.id:
        flash('Not your photo!', 'error')
        return redirect(request.referrer)
    
    DailyPhoto.query.filter_by(user_id=current_user.id, date=photo.date).update({'is_final': False})
    photo.is_final = True
    db.session.commit()
    flash('Photo set as your daily memory! ⭐', 'success')
    return redirect(request.referrer)

# ─────────────────────────────────────────────
# 👥 COMMUNITIES
# ─────────────────────────────────────────────
@views.route('/communities')
@login_required
def communities():
    my_communities = Community.query.join(CommunityMember).filter(CommunityMember.user_id == current_user.id).all()
    return render_template("communities.html", my_communities=my_communities, active_page="communities")

@views.route('/community/create', methods=['POST'])
@login_required
def create_community():
    name = request.form.get('name')
    description = request.form.get('description')
    category = request.form.get('category', 'other')
    invite_code = request.form.get('invite_code', '').strip().upper()
    if not invite_code:
        invite_code = generate_invite_code()
    if Community.query.filter_by(invite_code=invite_code).first():
        flash('That invite code is already taken!', 'error')
        return redirect(url_for('views.communities'))
    if name:
        goal = request.form.get('goal', '').strip()
        community = Community(name=name, description=description, category=category, goal=goal or None, invite_code=invite_code, created_by=current_user.id)
        db.session.add(community)
        db.session.flush()
        db.session.add(CommunityMember(community_id=community.id, user_id=current_user.id))
        # Turn the shared goal into a community habit so all members get it
        if goal:
            community_habit = CommunityHabit(name=goal, community_id=community.id)
            db.session.add(community_habit)
            db.session.flush()
            # Add to creator's habit tracker
            today = datetime.now(timezone.utc).replace(tzinfo=None).date()
            habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
            if not habit_month:
                habit_month = HabitMonth(user_id=current_user.id, year=today.year, month=today.month)
                db.session.add(habit_month)
                db.session.flush()
            if not Habit.query.filter_by(habit_month_id=habit_month.id, name=goal).first():
                db.session.add(Habit(name=goal, habit_month_id=habit_month.id))
        db.session.commit()
        flash(f'Community created! "{goal}" added to your habits. Share code: {invite_code} 🎉' if goal else f'Community created! Share code: {invite_code} 🎉', 'success')
    return redirect(url_for('views.communities'))

@views.route('/community/join', methods=['POST'])
@login_required
def join_community_by_code():
    invite_code = request.form.get('invite_code', '').strip().upper()
    community = Community.query.filter_by(invite_code=invite_code).first()
    if not community:
        flash('Invalid invite code!', 'error')
        return redirect(url_for('views.communities'))
    if CommunityMember.query.filter_by(community_id=community.id, user_id=current_user.id).first():
        flash('Already a member!', 'info')
    else:
        db.session.add(CommunityMember(community_id=community.id, user_id=current_user.id))
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
        if not habit_month:
            habit_month = HabitMonth(user_id=current_user.id, year=today.year, month=today.month)
            db.session.add(habit_month)
            db.session.flush()
        habits_added = 0
        for ch in CommunityHabit.query.filter_by(community_id=community.id).all():
            if not Habit.query.filter_by(habit_month_id=habit_month.id, name=ch.name).first():
                db.session.add(Habit(name=ch.name, habit_month_id=habit_month.id))
                habits_added += 1
        db.session.commit()
        flash(f'Welcome to {community.name}! {habits_added} habits added. 👥', 'success')
    return redirect(url_for('views.community_detail', community_id=community.id))

@views.route('/join/<invite_code>')
@login_required
def join_community_by_link(invite_code):
    invite_code = invite_code.strip().upper()
    community = Community.query.filter_by(invite_code=invite_code).first()
    if not community:
        flash('Invalid invite link!', 'error')
        return redirect(url_for('views.communities'))
    if not CommunityMember.query.filter_by(community_id=community.id, user_id=current_user.id).first():
        db.session.add(CommunityMember(community_id=community.id, user_id=current_user.id))
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
        if not habit_month:
            habit_month = HabitMonth(user_id=current_user.id, year=today.year, month=today.month)
            db.session.add(habit_month)
            db.session.flush()
        for ch in CommunityHabit.query.filter_by(community_id=community.id).all():
            if not Habit.query.filter_by(habit_month_id=habit_month.id, name=ch.name).first():
                db.session.add(Habit(name=ch.name, habit_month_id=habit_month.id))
        db.session.commit()
        flash(f'Welcome to {community.name}! 👥', 'success')
    return redirect(url_for('views.community_detail', community_id=community.id))

@views.route('/community/<int:community_id>', methods=['GET', 'POST'])
@login_required
def community_detail(community_id):
    community = Community.query.get_or_404(community_id)
    is_member = CommunityMember.query.filter_by(community_id=community_id, user_id=current_user.id).first() is not None
    if request.method == 'POST' and is_member:
        habit_name = request.form.get('habit_name', '').strip()
        if habit_name:
            existing = CommunityHabit.query.filter(
                CommunityHabit.community_id == community.id,
                db.func.lower(CommunityHabit.name) == habit_name.lower()
            ).first()
            if existing:
                flash('That habit already exists in this community.', 'info')
                return redirect(url_for('views.community_detail', community_id=community_id))
            new_habit = CommunityHabit(name=habit_name, community_id=community.id)
            db.session.add(new_habit)
            db.session.flush()
            today = datetime.now(timezone.utc).replace(tzinfo=None).date()
            for member in CommunityMember.query.filter_by(community_id=community_id).all():
                habit_month = HabitMonth.query.filter_by(user_id=member.user_id, year=today.year, month=today.month).first()
                if not habit_month:
                    habit_month = HabitMonth(user_id=member.user_id, year=today.year, month=today.month)
                    db.session.add(habit_month)
                    db.session.flush()
                if not Habit.query.filter_by(habit_month_id=habit_month.id, name=habit_name).first():
                    db.session.add(Habit(name=habit_name, habit_month_id=habit_month.id))
            db.session.commit()
            flash('Habit added to all members! ✅', 'success')
        return redirect(url_for('views.community_detail', community_id=community_id))
    habits = CommunityHabit.query.filter_by(community_id=community_id).all()
    members = User.query.join(CommunityMember).filter(CommunityMember.community_id == community_id).all()
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    habit_data = []
    for habit in habits:
        logs = CommunityHabitLog.query.filter_by(community_habit_id=habit.id, date=today).all()
        completed_users = [log.user_id for log in logs if log.completed]
        habit_data.append({'habit': habit, 'completed_users': completed_users, 'total_members': len(members)})
    habit_ids = [h.id for h in habits]
    member_count = len(members)

    # ── Streaks + weekly completions per member ──
    week_start = today - timedelta(days=6)
    member_streaks = {}
    member_weekly  = {}
    member_today   = {}  # checked in today?
    for member in members:
        # streak (consecutive days back from today)
        streak = 0
        check_date = today
        while True:
            done = CommunityHabitLog.query.filter(
                CommunityHabitLog.community_habit_id.in_(habit_ids),
                CommunityHabitLog.user_id == member.id,
                CommunityHabitLog.date == check_date,
                CommunityHabitLog.completed == True
            ).first()
            if done:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break
        member_streaks[member.id] = streak
        # completions this week (distinct days)
        week_logs = CommunityHabitLog.query.filter(
            CommunityHabitLog.community_habit_id.in_(habit_ids),
            CommunityHabitLog.user_id == member.id,
            CommunityHabitLog.date >= week_start,
            CommunityHabitLog.date <= today,
            CommunityHabitLog.completed == True
        ).all()
        member_weekly[member.id]  = len(set(log.date for log in week_logs))
        member_today[member.id]   = any(log.date == today for log in week_logs)

    # ── Leaderboard: sorted by streak desc, then weekly desc ──
    leaderboard = sorted(members,
        key=lambda m: (member_streaks[m.id], member_weekly[m.id]), reverse=True)

    # ── Perfect Day logic ──
    # today is perfect if every member checked in at least once
    today_checkins = set()
    if habit_ids:
        today_logs = CommunityHabitLog.query.filter(
            CommunityHabitLog.community_habit_id.in_(habit_ids),
            CommunityHabitLog.date == today,
            CommunityHabitLog.completed == True
        ).all()
        today_checkins = set(log.user_id for log in today_logs)
    today_is_perfect = member_count > 0 and len(today_checkins) >= member_count

    # count perfect days in last 30 days
    perfect_days = 0
    if habit_ids and member_count > 0:
        for d in range(30):
            check = today - timedelta(days=d)
            day_logs = CommunityHabitLog.query.filter(
                CommunityHabitLog.community_habit_id.in_(habit_ids),
                CommunityHabitLog.date == check,
                CommunityHabitLog.completed == True
            ).all()
            if len(set(log.user_id for log in day_logs)) >= member_count:
                perfect_days += 1

    return render_template("community_detail.html", community=community, is_member=is_member,
                         habits=habit_data, members=members, member_streaks=member_streaks,
                         leaderboard=leaderboard, member_weekly=member_weekly,
                         member_today=member_today, today_is_perfect=today_is_perfect,
                         perfect_days=perfect_days, active_page="communities")

@views.route('/community/habit/toggle/<int:habit_id>')
@login_required
def toggle_community_habit(habit_id):
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    community_habit = CommunityHabit.query.get_or_404(habit_id)
    if not CommunityMember.query.filter_by(community_id=community_habit.community_id, user_id=current_user.id).first():
        abort(403)
    log = CommunityHabitLog.query.filter_by(community_habit_id=habit_id, user_id=current_user.id, date=today).first()
    if log:
        log.completed = not log.completed
        new_status = log.completed
    else:
        log = CommunityHabitLog(community_habit_id=habit_id, user_id=current_user.id, date=today, completed=True)
        db.session.add(log)
        new_status = True
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
    if habit_month:
        personal_habit = Habit.query.filter_by(habit_month_id=habit_month.id, name=community_habit.name).first()
        if personal_habit:
            personal_log = HabitLog.query.filter_by(habit_id=personal_habit.id, date=today).first()
            if personal_log:
                personal_log.completed = new_status
            else:
                db.session.add(HabitLog(habit_id=personal_habit.id, date=today, completed=new_status))
    db.session.commit()
    if new_status:
        flash(today.strftime("%Y-%m-%d"), 'habit_photo')
    return redirect(request.referrer)

@views.route('/community/habit/delete/<int:habit_id>')
@login_required
def delete_community_habit(habit_id):
    community_habit = CommunityHabit.query.get_or_404(habit_id)
    if not CommunityMember.query.filter_by(community_id=community_habit.community_id, user_id=current_user.id).first():
        abort(403)
    community_id = community_habit.community_id
    db.session.delete(community_habit)
    db.session.commit()
    flash('Habit removed from community.', 'success')
    return redirect(url_for('views.community_detail', community_id=community_id))

@views.route('/community/leave/<int:community_id>')
@login_required
def leave_community(community_id):
    CommunityMember.query.filter_by(community_id=community_id, user_id=current_user.id).delete()
    db.session.commit()
    flash('Left the community.', 'info')
    return redirect(url_for('views.communities'))

@views.route('/community/<int:community_id>/goal', methods=['POST'])
@login_required
def edit_community_goal(community_id):
    community = Community.query.get_or_404(community_id)
    if community.created_by != current_user.id:
        abort(403)
    community.goal = request.form.get('goal', '').strip() or None
    db.session.commit()
    flash('Community goal updated!', 'success')
    return redirect(url_for('views.community_detail', community_id=community_id))

# ─────────────────────────────────────────────
# 💬 CONNECT (COMMUNITY & DM CHAT)
# ─────────────────────────────────────────────

@views.route('/connect')
@login_required
def connect():
    my_communities = Community.query.join(CommunityMember).filter(CommunityMember.user_id == current_user.id).all()
    # DM contacts: all users who share at least one community with current_user
    my_community_ids = [c.id for c in my_communities]
    dm_user_ids = set()
    for cid in my_community_ids:
        for m in CommunityMember.query.filter_by(community_id=cid).all():
            if m.user_id != current_user.id:
                dm_user_ids.add(m.user_id)
    dm_contacts = User.query.filter(User.id.in_(dm_user_ids)).all()
    return render_template('connect.html', my_communities=my_communities, dm_contacts=dm_contacts, active_page='connect')

@views.route('/connect/send', methods=['POST'])
@login_required
def connect_send():
    data = request.get_json()
    content = (data.get('content') or '').strip()
    community_id = data.get('community_id')
    recipient_id = data.get('recipient_id')
    if not content:
        return jsonify({'ok': False}), 400
    if community_id:
        community = Community.query.get_or_404(int(community_id))
        if not CommunityMember.query.filter_by(community_id=community.id, user_id=current_user.id).first():
            return jsonify({'ok': False}), 403
        msg = Message(sender_id=current_user.id, community_id=community.id, content=content)
    elif recipient_id:
        rid = int(recipient_id)
        my_comm_ids = {m.community_id for m in CommunityMember.query.filter_by(user_id=current_user.id).all()}
        their_comm_ids = {m.community_id for m in CommunityMember.query.filter_by(user_id=rid).all()}
        if not my_comm_ids.intersection(their_comm_ids):
            return jsonify({'ok': False}), 403
        recipient = User.query.get_or_404(rid)
        msg = Message(sender_id=current_user.id, recipient_id=recipient.id, content=content)
    else:
        return jsonify({'ok': False}), 400
    db.session.add(msg)
    db.session.commit()
    return jsonify({'ok': True, 'id': msg.id, 'created_at': msg.created_at.strftime('%H:%M')})

@views.route('/connect/upload', methods=['POST'])
@login_required
def connect_upload():
    community_id = request.form.get('community_id')
    recipient_id = request.form.get('recipient_id')
    file = request.files.get('file')

    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'No file'}), 400

    original = file.filename
    ext = os.path.splitext(secure_filename(original))[1].lower()

    ALLOWED_MSG_EXTS = {
        '.png', '.jpg', '.jpeg', '.gif', '.webp',
        '.pdf', '.docx', '.txt'
    }

    if ext not in ALLOWED_MSG_EXTS:
        return jsonify({'ok': False, 'error': 'File type not allowed'}), 400

    # Upload attachment to Supabase Storage
    stored = upload_file(file, "messages")

    if community_id:
        community = Community.query.get_or_404(int(community_id))

        if not CommunityMember.query.filter_by(
            community_id=community.id,
            user_id=current_user.id
        ).first():
            return jsonify({'ok': False}), 403

        msg = Message(
            sender_id=current_user.id,
            community_id=community.id,
            content='',
            filename=stored,
            original_name=original
        )

    elif recipient_id:
        rid = int(recipient_id)

        # Verify recipient shares at least one community with sender
        my_comm_ids = {
            m.community_id
            for m in CommunityMember.query.filter_by(user_id=current_user.id).all()
        }

        their_comm_ids = {
            m.community_id
            for m in CommunityMember.query.filter_by(user_id=rid).all()
        }

        if not my_comm_ids.intersection(their_comm_ids):
            return jsonify({'ok': False}), 403

        msg = Message(
            sender_id=current_user.id,
            recipient_id=rid,
            content='',
            filename=stored,
            original_name=original
        )

    else:
        return jsonify({'ok': False}), 400

    db.session.add(msg)
    db.session.commit()

    return jsonify({
        'ok': True,
        'id': msg.id,
        'created_at': msg.created_at.strftime('%H:%M')
    })

@views.route('/connect/messages/group/<int:community_id>')
@login_required
def connect_group_messages(community_id):
    community = Community.query.get_or_404(community_id)
    if not CommunityMember.query.filter_by(community_id=community_id, user_id=current_user.id).first():
        return jsonify({'ok': False}), 403
    since_id = request.args.get('since', 0, type=int)
    msgs = Message.query.filter(
        Message.community_id == community_id,
        Message.id > since_id
    ).order_by(Message.id.asc()).limit(100).all()
    return jsonify([_msg_json(m, current_user.id) for m in msgs])

@views.route('/connect/messages/dm/<int:user_id>')
@login_required
def connect_dm_messages(user_id):
    since_id = request.args.get('since', 0, type=int)
    msgs = Message.query.filter(
        Message.community_id == None,
        Message.id > since_id,
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.recipient_id == user_id),
            db.and_(Message.sender_id == user_id, Message.recipient_id == current_user.id)
        )
    ).order_by(Message.id.asc()).limit(100).all()
    return jsonify([_msg_json(m, current_user.id) for m in msgs])

def _msg_json(m, my_id):
    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    ext = os.path.splitext(m.filename)[1].lower() if m.filename else ''
    return {
        'id': m.id,
        'sender': m.sender.name,
        'sender_id': m.sender_id,
        'content': m.content,
        'filename': m.filename,
        'original_name': m.original_name,
        'is_image': ext in IMAGE_EXTS,
        'file_url': get_public_url(m.filename) if m.filename else None,
        'time': m.created_at.strftime('%H:%M'),
        'mine': m.sender_id == my_id
    }

# ─────────────────────────────────────────────
# 📞 CALL SIGNALING  (in-memory WebRTC relay)
# ─────────────────────────────────────────────
_call_lock        = _threading.Lock()
_call_sessions    = {}   # call_id -> session dict  (1-on-1)
_group_call_rooms = {}   # room_id -> room dict     (group)

@views.route('/connect/call/start', methods=['POST'])
@login_required
def call_start():
    data      = request.get_json(silent=True) or {}
    peer_id   = int(data.get('peer_id', 0))
    call_type = data.get('call_type', 'audio')
    sdp       = data.get('sdp', '')
    if not peer_id or not sdp:
        return jsonify({'error': 'missing'}), 400
    call_id = f"{current_user.id}-{peer_id}"
    with _call_lock:
        _call_sessions[call_id] = {
            'caller_id': current_user.id, 'callee_id': peer_id,
            'call_type': call_type, 'offer': sdp, 'answer': None,
            'caller_ice': [], 'callee_ice': [], 'status': 'ringing'
        }
    return jsonify({'call_id': call_id})

@views.route('/connect/call/incoming')
@login_required
def call_incoming():
    with _call_lock:
        for call_id, s in list(_call_sessions.items()):
            if s['callee_id'] == current_user.id and s['status'] == 'ringing':
                caller = User.query.get(s['caller_id'])
                return jsonify({
                    'call_id': call_id, 'caller_id': s['caller_id'],
                    'caller_name': caller.name if caller else 'Someone',
                    'call_type': s['call_type'], 'sdp': s['offer']
                })
    return jsonify(None)

@views.route('/connect/call/answer', methods=['POST'])
@login_required
def call_answer_route():
    data     = request.get_json(silent=True) or {}
    call_id  = data.get('call_id', '')
    accepted = data.get('accepted', False)
    sdp      = data.get('sdp', '')
    with _call_lock:
        s = _call_sessions.get(call_id)
        if not s or s['callee_id'] != current_user.id:
            return jsonify({'error': 'not found'}), 404
        if accepted:
            s['answer'] = sdp
            s['status'] = 'active'
        else:
            s['status'] = 'declined'
    return jsonify({'ok': True})

@views.route('/connect/call/poll/<call_id>')
@login_required
def call_poll(call_id):
    with _call_lock:
        s = _call_sessions.get(call_id)
        if not s:
            return jsonify({'status': 'ended', 'ice': [], 'answer': None})
        is_caller = s['caller_id'] == current_user.id
        ice = (s['callee_ice'] if is_caller else s['caller_ice']).copy()
        if is_caller:
            s['callee_ice'] = []
        else:
            s['caller_ice'] = []
        return jsonify({'status': s['status'], 'answer': s['answer'] if is_caller else None, 'ice': ice})

@views.route('/connect/call/ice', methods=['POST'])
@login_required
def call_ice():
    data      = request.get_json(silent=True) or {}
    call_id   = data.get('call_id', '')
    candidate = data.get('candidate')
    with _call_lock:
        s = _call_sessions.get(call_id)
        if not s:
            return jsonify({'error': 'not found'}), 404
        if s['caller_id'] == current_user.id:
            s['caller_ice'].append(candidate)
        else:
            s['callee_ice'].append(candidate)
    return jsonify({'ok': True})

@views.route('/connect/call/end', methods=['POST'])
@login_required
def call_end():
    data    = request.get_json(silent=True) or {}
    call_id = data.get('call_id', '')
    with _call_lock:
        if call_id in _call_sessions:
            _call_sessions[call_id]['status'] = 'ended'
    return jsonify({'ok': True})

# ── Group call routes ──────────────────────────
@views.route('/connect/call/group/start', methods=['POST'])
@login_required
def group_call_start():
    data         = request.get_json(silent=True) or {}
    community_id = int(data.get('community_id', 0))
    call_type    = data.get('call_type', 'audio')
    if not community_id:
        return jsonify({'error': 'missing community_id'}), 400
    if not CommunityMember.query.filter_by(community_id=community_id, user_id=current_user.id).first():
        return jsonify({'error': 'not a member'}), 403
    room_id = f"group-{community_id}"
    with _call_lock:
        if room_id not in _group_call_rooms or _group_call_rooms[room_id]['status'] == 'ended':
            _group_call_rooms[room_id] = {
                'community_id': community_id, 'call_type': call_type,
                'status': 'active', 'initiator_id': current_user.id,
                'participants': {}, 'offers': {}, 'answers': {}, 'ice': {}, 'left': []
            }
        room = _group_call_rooms[room_id]
        room['participants'][current_user.id] = {'name': current_user.name}
    return jsonify({'room_id': room_id, 'call_type': room['call_type']})

@views.route('/connect/call/group/any-incoming')
@login_required
def group_call_any_incoming():
    member_ids = {m.community_id for m in CommunityMember.query.filter_by(user_id=current_user.id).all()}
    with _call_lock:
        for room_id, room in _group_call_rooms.items():
            if (room['status'] == 'active'
                    and room['community_id'] in member_ids
                    and current_user.id not in room['participants']
                    and current_user.id not in room['left']):
                initiator = User.query.get(room['initiator_id'])
                comm      = Community.query.get(room['community_id'])
                return jsonify({
                    'room_id': room_id,
                    'community_id': room['community_id'],
                    'community_name': comm.name if comm else 'Group',
                    'call_type': room['call_type'],
                    'initiator_name': initiator.name if initiator else 'Someone',
                    'participant_count': len(room['participants'])
                })
    return jsonify(None)

@views.route('/connect/call/group/join', methods=['POST'])
@login_required
def group_call_join():
    data    = request.get_json(silent=True) or {}
    room_id = data.get('room_id', '')
    with _call_lock:
        room = _group_call_rooms.get(room_id)
        if not room:
            return jsonify({'error': 'not found'}), 404
        room['participants'][current_user.id] = {'name': current_user.name}
        if current_user.id in room['left']:
            room['left'].remove(current_user.id)
    return jsonify({'ok': True})

@views.route('/connect/call/group/offer', methods=['POST'])
@login_required
def group_call_offer_route():
    data    = request.get_json(silent=True) or {}
    room_id = data.get('room_id', ''); to_id = int(data.get('to_id', 0)); sdp = data.get('sdp', '')
    with _call_lock:
        room = _group_call_rooms.get(room_id)
        if not room: return jsonify({'error': 'not found'}), 404
        room['offers'][f"{current_user.id}-{to_id}"] = sdp
    return jsonify({'ok': True})

@views.route('/connect/call/group/answer', methods=['POST'])
@login_required
def group_call_answer_route():
    data    = request.get_json(silent=True) or {}
    room_id = data.get('room_id', ''); to_id = int(data.get('to_id', 0)); sdp = data.get('sdp', '')
    with _call_lock:
        room = _group_call_rooms.get(room_id)
        if not room: return jsonify({'error': 'not found'}), 404
        room['answers'][f"{current_user.id}-{to_id}"] = sdp
    return jsonify({'ok': True})

@views.route('/connect/call/group/ice', methods=['POST'])
@login_required
def group_call_ice_route():
    data      = request.get_json(silent=True) or {}
    room_id   = data.get('room_id', ''); to_id = int(data.get('to_id', 0)); candidate = data.get('candidate')
    key       = f"{current_user.id}-{to_id}"
    with _call_lock:
        room = _group_call_rooms.get(room_id)
        if not room: return jsonify({'error': 'not found'}), 404
        room['ice'].setdefault(key, []).append(candidate)
    return jsonify({'ok': True})

@views.route('/connect/call/group/poll/<room_id>')
@login_required
def group_call_poll(room_id):
    uid = current_user.id
    with _call_lock:
        room = _group_call_rooms.get(room_id)
        if not room or room['status'] == 'ended':
            return jsonify({'status': 'ended', 'participants': [], 'left': [], 'offers': {}, 'answers': {}, 'ice': {}})
        my_offers  = {k.split('-')[0]: v for k, v in list(room['offers'].items())  if int(k.split('-')[1]) == uid}
        my_answers = {k.split('-')[0]: v for k, v in list(room['answers'].items()) if int(k.split('-')[1]) == uid}
        my_ice     = {}
        for k in list(room['ice'].keys()):
            from_id, to_id = k.split('-')
            if int(to_id) == uid and room['ice'][k]:
                my_ice[from_id] = room['ice'][k].copy()
                room['ice'][k] = []
        for k in list(my_offers.keys()):  del room['offers'][f"{k}-{uid}"]
        for k in list(my_answers.keys()): del room['answers'][f"{k}-{uid}"]
        return jsonify({
            'status': room['status'],
            'participants': [{'id': pid, 'name': pdata['name']} for pid, pdata in room['participants'].items()],
            'left': room['left'],
            'offers': my_offers, 'answers': my_answers, 'ice': my_ice
        })

@views.route('/connect/call/group/leave', methods=['POST'])
@login_required
def group_call_leave():
    data    = request.get_json(silent=True) or {}
    room_id = data.get('room_id', '')
    with _call_lock:
        room = _group_call_rooms.get(room_id)
        if room:
            room['participants'].pop(current_user.id, None)
            if current_user.id not in room['left']:
                room['left'].append(current_user.id)
            if not room['participants']:
                room['status'] = 'ended'
    return jsonify({'ok': True})

# ─────────────────────────────────────────────
# 📤 SHARE STATS API
# ─────────────────────────────────────────────
@views.route('/api/share-stats')
@login_required
def share_stats():
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    # Tasks today
    tasks_today = Task.query.filter(Task.user_id == current_user.id, db.func.date(Task.date) == today).all()
    tasks_done  = sum(1 for t in tasks_today if t.completed)
    # Habits today
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
    habits_all  = habit_month.habits if habit_month else []
    logged_ids  = {l.habit_id for l in HabitLog.query.filter_by(date=today).filter(
                    HabitLog.habit_id.in_([h.id for h in habits_all])).all() if l.completed} if habits_all else set()
    # Stars this month
    start_of_month = today.replace(day=1)
    stars_month = Achievement.query.filter(
        Achievement.user_id == current_user.id,
        Achievement.earned == True,
        Achievement.date >= start_of_month,
        Achievement.date <= today
    ).count()
    # Current streak
    streak = 0
    check = today
    while True:
        a = Achievement.query.filter_by(user_id=current_user.id, date=check, earned=True).first()
        if a:
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    # Focus minutes today
    focus_sessions = FocusSession.query.filter_by(user_id=current_user.id, date=today, completed=True).all()
    focus_mins = sum(s.duration for s in focus_sessions) // 60 if focus_sessions else 0
    return jsonify({
        'tasks_done': tasks_done, 'tasks_total': len(tasks_today),
        'habits_done': len(logged_ids), 'habits_total': len(habits_all),
        'stars_month': stars_month, 'streak': streak, 'focus_mins': focus_mins
    })

# ─────────────────────────────────────────────
# 🔔 REMINDERS API
# ─────────────────────────────────────────────
@views.route('/api/due-reminders')
@login_required
def due_reminders():
    # Use client-provided local minute so timezone mismatches don't silence reminders
    local_minute = request.args.get('lm', type=int)
    local_date_str = request.args.get('ld', '')   # YYYY-MM-DD in client local time
    if local_minute is not None and local_date_str:
        try:
            today = datetime.strptime(local_date_str, '%Y-%m-%d').date()
            current_minute = local_minute
        except ValueError:
            today = datetime.now(timezone.utc).replace(tzinfo=None).date()
            current_minute = datetime.now(timezone.utc).replace(tzinfo=None).hour * 60 + datetime.now(timezone.utc).replace(tzinfo=None).minute
    else:
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        now   = datetime.now(timezone.utc).replace(tzinfo=None)
        current_minute = now.hour * 60 + now.minute
    tasks = Task.query.filter(
        Task.user_id == current_user.id,
        Task.due_time.isnot(None),
        Task.reminder_offset.isnot(None),
        Task.completed == False,
        db.func.date(Task.date) == today,
    ).all()
    due = []
    for task in tasks:
        try:
            h, m = map(int, task.due_time.split(':'))
            remind_at = h * 60 + m + task.reminder_offset
            # 2-minute window so a 30s polling interval never misses it
            if remind_at <= current_minute <= remind_at + 1:
                due.append({'id': task.id, 'content': task.content,
                            'due_time': task.due_time, 'offset': task.reminder_offset})
        except (ValueError, AttributeError):
            pass
    return jsonify({'reminders': due})

# ─────────────────────────────────────────────
# ⭐ ACHIEVEMENTS
# ─────────────────────────────────────────────
@views.route('/achievements')
@login_required
def achievements():
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    start_of_month = today.replace(day=1)
    if today.month == 12:
        end_of_month = today.replace(year=today.year+1, month=1, day=1) - timedelta(days=1)
    else:
        end_of_month = today.replace(month=today.month+1, day=1) - timedelta(days=1)
    calendar_data = []
    total_stars = 0
    total_crowns = 0
    current_streak = 0
    max_streak = 0
    current_day = start_of_month
    while current_day <= min(today, end_of_month):
        achievement = Achievement.query.filter_by(user_id=current_user.id, date=current_day).first()
        if current_day == today or not achievement:
            earned = check_daily_perfection(current_day)
            crown  = check_community_crown(current_user.id, current_day)
            if achievement:
                achievement.earned = earned
                achievement.stars  = 1 if earned else 0
                achievement.crowns = 1 if crown  else 0
            else:
                achievement = Achievement(user_id=current_user.id, date=current_day,
                                          earned=earned, stars=1 if earned else 0,
                                          crowns=1 if crown else 0)
                db.session.add(achievement)
        else:
            crown = bool(achievement.crowns)
        if achievement.earned:
            total_stars += 1
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
        if achievement.crowns:
            total_crowns += 1
        calendar_data.append({
            'day': current_day.day,
            'earned': achievement.earned,
            'crown': bool(achievement.crowns),
            'is_today': current_day == today,
            'is_future': current_day > today
        })
        current_day += timedelta(days=1)
    db.session.commit()
    all_time_stars  = Achievement.query.filter_by(user_id=current_user.id, earned=True).count()
    all_time_crowns = db.session.query(db.func.sum(Achievement.crowns)).filter_by(user_id=current_user.id).scalar() or 0
    month_name = today.strftime('%B %Y')
    return render_template("achievements.html", calendar_data=calendar_data,
                         total_stars=total_stars, total_crowns=total_crowns,
                         current_streak=current_streak, max_streak=max_streak,
                         all_time_stars=all_time_stars, all_time_crowns=all_time_crowns,
                         month_name=month_name, active_page="achievements")

# ─────────────────────────────────────────────
# 📊 ANALYTICS
# ─────────────────────────────────────────────
@views.route('/analytics')
@login_required
def analytics():
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    start_of_month = today.replace(day=1)
    if today.month == 12:
        end_of_month = today.replace(year=today.year+1, month=1, day=1) - timedelta(days=1)
    else:
        end_of_month = today.replace(month=today.month+1, day=1) - timedelta(days=1)
    
    weekly_tasks = Task.query.filter(Task.user_id == current_user.id, Task.date >= start_of_week, Task.date <= end_of_week).all()
    weekly_total = len(weekly_tasks)
    weekly_completed = sum(1 for t in weekly_tasks if t.completed)
    weekly_completion_rate = (weekly_completed / weekly_total * 100) if weekly_total > 0 else 0
    
    weekly_daily_stats = {}
    for i in range(7):
        day = start_of_week + timedelta(days=i)
        day_tasks = [t for t in weekly_tasks if t.date.date() == day]
        weekly_daily_stats[day.strftime('%a')] = {'total': len(day_tasks), 'completed': sum(1 for t in day_tasks if t.completed)}
    
    monthly_tasks = Task.query.filter(Task.user_id == current_user.id, Task.date >= start_of_month, Task.date <= end_of_month).all()
    monthly_total = len(monthly_tasks)
    monthly_completed = sum(1 for t in monthly_tasks if t.completed)
    monthly_completion_rate = (monthly_completed / monthly_total * 100) if monthly_total > 0 else 0
    
    monthly_weekly_stats = {}
    current_week_start = start_of_month
    week_num = 1
    while current_week_start <= end_of_month:
        current_week_end = min(current_week_start + timedelta(days=6), end_of_month)
        week_tasks = [t for t in monthly_tasks if current_week_start <= t.date.date() <= current_week_end]
        monthly_weekly_stats[f'Week {week_num}'] = {'total': len(week_tasks), 'completed': sum(1 for t in week_tasks if t.completed)}
        current_week_start = current_week_end + timedelta(days=1)
        week_num += 1
    
    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
    habit_stats = []
    if habit_month:
        habits = Habit.query.filter_by(habit_month_id=habit_month.id).all()
        for habit in habits:
            earliest_log = HabitLog.query.filter_by(habit_id=habit.id).order_by(HabitLog.date.asc()).first()
            if earliest_log and earliest_log.date <= start_of_month:
                effective_start = start_of_month
            elif earliest_log:
                effective_start = earliest_log.date
            else:
                effective_start = today
            total_days = max((today - effective_start).days + 1, 1)
            completed_days = HabitLog.query.filter(HabitLog.habit_id == habit.id, HabitLog.completed == True, HabitLog.date >= effective_start, HabitLog.date <= today).count()
            logs = HabitLog.query.filter_by(habit_id=habit.id, completed=True).order_by(HabitLog.date.desc()).all()
            streak = 0
            last_date = today
            for log in logs:
                if log.date == last_date or log.date == last_date - timedelta(days=1):
                    streak += 1
                    last_date = log.date
                else:
                    break
            habit_stats.append({'name': habit.name, 'completed': completed_days, 'total': total_days, 'rate': (completed_days / total_days * 100) if total_days > 0 else 0, 'streak': streak})
    
    today_start = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
    today_sessions = FocusSession.query.filter(FocusSession.user_id == current_user.id, FocusSession.date >= today_start).all()
    today_focus_seconds = sum(s.duration for s in today_sessions)
    week_sessions = FocusSession.query.filter(FocusSession.user_id == current_user.id, FocusSession.date >= start_of_week).all()
    week_focus_seconds = sum(s.duration for s in week_sessions)
    month_sessions = FocusSession.query.filter(FocusSession.user_id == current_user.id, FocusSession.date >= start_of_month).all()
    month_focus_seconds = sum(s.duration for s in month_sessions)
    
    task_time_stats = []
    all_tasks_with_time = Task.query.filter(Task.user_id == current_user.id, Task.focus_time > 0).order_by(Task.focus_time.desc()).limit(10).all()
    for task in all_tasks_with_time:
        hours = task.focus_time // 3600
        minutes = (task.focus_time % 3600) // 60
        task_time_stats.append({'name': task.content[:30], 'hours': round(task.focus_time / 3600, 1), 'sessions': task.session_count or 0, 'display_time': f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"})
    
    total_focus_seconds = sum(t.focus_time or 0 for t in Task.query.filter_by(user_id=current_user.id).all())
    total_focus_hours = round(total_focus_seconds / 3600, 1)
    recent_sessions = FocusSession.query.filter_by(user_id=current_user.id).order_by(FocusSession.date.desc()).limit(10).all()
    # Focus minutes per day this week
    focus_per_day = {}
    for i in range(7):
        day = start_of_week + timedelta(days=i)
        focus_per_day[day.strftime('%a')] = 0
    for s in week_sessions:
        key = s.date.strftime('%a') if hasattr(s.date, 'strftime') else day.strftime('%a')
        if key in focus_per_day:
            focus_per_day[key] += s.duration // 60
    # Category breakdown for tasks this month
    cat_breakdown = {}
    for t in monthly_tasks:
        cat = t.category or 'Uncategorised'
        cat_breakdown[cat] = cat_breakdown.get(cat, 0) + 1
    # Exams upcoming
    exams_upcoming = (Exam.query.join(Subject)
                      .filter(Subject.user_id == current_user.id, Exam.date >= today)
                      .order_by(Exam.date).limit(5).all())
    
    insights = []
    if weekly_completion_rate < 50:
        insights.append({'type': 'warning', 'message': 'Weekly completion below 50%. Try breaking tasks into smaller chunks.', 'icon': '⚠️'})
    elif weekly_completion_rate >= 80:
        insights.append({'type': 'success', 'message': 'Great job completing most of your weekly tasks!', 'icon': '🎉'})
    if monthly_completion_rate < 40:
        insights.append({'type': 'warning', 'message': 'Monthly completion rate is low. Consider prioritizing tasks.', 'icon': '📉'})
    for habit in habit_stats:
        if habit['rate'] < 30:
            insights.append({'type': 'danger', 'message': f'"{habit["name"]}" needs attention. Start with just 5 minutes a day.', 'icon': '🔴'})
        elif habit['rate'] >= 90:
            insights.append({'type': 'success', 'message': f'"{habit["name"]}" is a strong habit!', 'icon': '⭐'})
    journal_count = DailyJournal.query.filter_by(user_id=current_user.id).count()
    if journal_count == 0:
        insights.append({'type': 'info', 'message': 'Start writing daily journals for powerful reflections!', 'icon': '📝'})
    elif journal_count >= 5:
        insights.append({'type': 'success', 'message': f'You\'ve written {journal_count} journal entries!', 'icon': '🧠'})
    
    import json as _json
    cached_report = None
    if current_user.ai_report_json and current_user.ai_report_date == today:
        try:
            cached_report = _json.loads(current_user.ai_report_json)
        except Exception:
            pass

    return render_template("analytics.html", weekly_completion_rate=weekly_completion_rate,
                         weekly_total=weekly_total, weekly_completed=weekly_completed,
                         weekly_daily_stats=weekly_daily_stats, monthly_completion_rate=monthly_completion_rate,
                         monthly_total=monthly_total, monthly_completed=monthly_completed,
                         monthly_weekly_stats=monthly_weekly_stats, habit_stats=habit_stats,
                         today_focus_seconds=today_focus_seconds, week_focus_seconds=week_focus_seconds,
                         month_focus_seconds=month_focus_seconds, task_time_stats=task_time_stats,
                         total_focus_hours=total_focus_hours, recent_sessions=recent_sessions,
                         focus_per_day=focus_per_day, cat_breakdown=cat_breakdown,
                         exams_upcoming=exams_upcoming, cached_report=cached_report,
                         insights=insights, today=today, active_page="analytics")


@views.route('/analytics/generate-report', methods=['POST'])
@login_required
def analytics_generate_report():
    import json as _json
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    force = request.args.get('force') == '1'

    if not force and current_user.ai_report_json and current_user.ai_report_date == today:
        try:
            return jsonify(_json.loads(current_user.ai_report_json))
        except Exception:
            pass

    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'no_key'}), 500

    start_of_week  = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)

    weekly_tasks   = Task.query.filter(Task.user_id == current_user.id, Task.date >= start_of_week, Task.date <= today).all()
    monthly_tasks  = Task.query.filter(Task.user_id == current_user.id, Task.date >= start_of_month, Task.date <= today).all()
    w_total  = len(weekly_tasks)
    w_done   = sum(1 for t in weekly_tasks if t.completed)
    m_total  = len(monthly_tasks)
    m_done   = sum(1 for t in monthly_tasks if t.completed)

    habit_month = HabitMonth.query.filter_by(user_id=current_user.id, year=today.year, month=today.month).first()
    habit_summary = []
    if habit_month:
        for h in Habit.query.filter_by(habit_month_id=habit_month.id).all():
            total_days = max((today - start_of_month).days + 1, 1)
            done_days  = HabitLog.query.filter(HabitLog.habit_id == h.id, HabitLog.completed == True,
                                               HabitLog.date >= start_of_month, HabitLog.date <= today).count()
            habit_summary.append({'name': h.name, 'rate': round(done_days / total_days * 100)})

    week_sessions = FocusSession.query.filter(FocusSession.user_id == current_user.id, FocusSession.date >= start_of_week).all()
    focus_per_day = {}
    for i in range(7):
        d = start_of_week + timedelta(days=i)
        focus_per_day[d.strftime('%a')] = 0
    for s in week_sessions:
        key = s.date.strftime('%a') if hasattr(s.date, 'strftime') else ''
        if key in focus_per_day:
            focus_per_day[key] += s.duration // 60

    journal_count = DailyJournal.query.filter(DailyJournal.user_id == current_user.id,
                                              DailyJournal.date >= start_of_month).count()

    first_task = Task.query.filter_by(user_id=current_user.id).order_by(Task.date.asc()).first()
    data_age_days = (today - first_task.date.date()).days if first_task else 0

    cat_breakdown = {}
    for t in monthly_tasks:
        cat = t.category or 'Uncategorised'
        cat_breakdown[cat] = cat_breakdown.get(cat, 0) + 1

    stats = {
        'today': str(today),
        'year_goal': current_user.year_goal or '',
        'life_phase': current_user.life_phase or 'other',
        'challenges': current_user.challenges or '',
        'weekly_completion_rate': round(w_done / w_total * 100) if w_total else 0,
        'weekly_tasks_total': w_total,
        'monthly_completion_rate': round(m_done / m_total * 100) if m_total else 0,
        'monthly_tasks_total': m_total,
        'habits': habit_summary,
        'week_focus_minutes': sum(s.duration for s in week_sessions) // 60,
        'focus_per_day': focus_per_day,
        'journals_this_month': journal_count,
        'top_categories': list(cat_breakdown.items())[:5],
        'data_age_days': data_age_days,
    }

    prompt = f"""You are Monad Intelligence, a personal productivity coach analyzing real user data.

USER DATA (past 30 days):
{_json.dumps(stats, indent=2)}

SCORING RULES:
- momentum_score (0-100): weight habit consistency 40%, task completion 35%, focus time 25%. Be honest — if completion is 40%, score should be around 45-55.
- momentum_label: "Just Starting" (0-30), "Building Momentum" (31-60), "In the Zone" (61-80), "Peak Performance" (81-100)
- goal_forecast: "Insufficient Data" if data_age_days < 7. Otherwise "On Track" (monthly rate ≥65%), "Needs Attention" (40-64%), "At Risk" (<40%)
- burnout_risk: "Low", "Moderate", "High". High only if focus is very high AND habit completion is declining.
- peak_hours: look at focus_per_day — which days have most minutes. Translate to a readable time range guess like "evenings" or "mornings". If all zeros say "No data yet".
- time_leak_hours: how many hours this week were NOT spent in focused work vs what a typical productive week would look like for this life_phase. Give a single integer.
- insights: array of max 4 objects. Only include correlations if data_age_days >= 14. Mix at least 1 positive if warranted.
  Each: {{"type": "positive"|"warning"|"risk", "icon": "✓"|"⚠"|"⚡", "message": "short actionable sentence"}}
- recommendation: one concrete sentence on what to prioritize tomorrow.

Return ONLY valid JSON:
{{
  "momentum_score": 72,
  "momentum_label": "In the Zone",
  "goal_forecast": "On Track",
  "burnout_risk": "Low",
  "peak_hours": "Evenings",
  "time_leak_hours": 5,
  "insights": [{{"type": "positive", "icon": "✓", "message": "..."}}],
  "recommendation": "..."
}}"""

    try:
        text = _gemini_call(api_key, prompt, timeout=60)
        report = _json.loads(text)
        current_user.ai_report_json = _json.dumps(report)
        current_user.ai_report_date = today
        db.session.commit()
        return jsonify(report)
    except Exception as e:
        return jsonify({'error': 'ai_failed', 'detail': str(e)}), 500


# ─────────────────────────────────────────────
# 📚 ACADEMICS
# ─────────────────────────────────────────────

@views.route('/study')
@login_required
def study_subjects():
    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    subjects = Subject.query.filter_by(user_id=current_user.id).order_by(Subject.name).all()
    today = datetime.now(timezone.utc).date()
    dow   = today.weekday()  # 0=Mon … 5=Sat, 6=Sun
    today_slots = []
    if dow <= 5:
        for s in subjects:
            for sl in s.slots:
                if sl.day_of_week == dow:
                    today_slots.append({'subject': s, 'slot': sl})
        today_slots.sort(key=lambda x: x['slot'].start_time)
    return render_template('study_subjects.html', subjects=subjects,
                           today_slots=today_slots, today=today, active_page='study')

@views.route('/study/marks')
@login_required
def study_marks():
    results  = ExamResult.query.filter_by(user_id=current_user.id).order_by(ExamResult.created_at.desc()).all()
    subjects = Subject.query.filter_by(user_id=current_user.id).order_by(Subject.name).all()
    return render_template('marks.html', results=results, subjects=subjects, active_page='marks')

@views.route('/study/marks/add', methods=['POST'])
@login_required
def study_marks_add():
    exam_name = request.form.get('exam_name', '').strip()
    if not exam_name:
        flash('Please enter an exam name.', 'error')
        return redirect(url_for('views.study_marks'))
    exam_date = exam_end_date = None
    for attr, key in [('exam_date', 'exam_date'), ('exam_end_date', 'exam_end_date')]:
        val = request.form.get(key, '').strip()
        if val:
            try:
                d = datetime.strptime(val, '%Y-%m-%d').date()
                if attr == 'exam_date': exam_date = d
                else: exam_end_date = d
            except ValueError: pass
    result = ExamResult(user_id=current_user.id, name=exam_name, date=exam_date, end_date=exam_end_date)
    db.session.add(result)
    db.session.flush()
    names    = request.form.getlist('subject_name[]')
    obtained = request.form.getlist('marks_obtained[]')
    total_m  = request.form.getlist('marks_total[]')
    for i, sname in enumerate(names):
        sname = sname.strip()
        if not sname: continue
        try:
            got = float(obtained[i]) if i < len(obtained) else 0
            tot = float(total_m[i])  if i < len(total_m)  else 100
            if tot <= 0: tot = 100
        except (ValueError, IndexError): continue
        db.session.add(SubjectMark(
            exam_result_id=result.id, subject_name=sname,
            marks_obtained=min(got, tot), marks_total=tot
        ))
    db.session.commit()
    flash('Exam result saved!', 'success')
    return redirect(url_for('views.study_marks'))

@views.route('/study/marks/delete/<int:result_id>', methods=['POST'])
@login_required
def study_marks_delete(result_id):
    result = ExamResult.query.get_or_404(result_id)
    if result.user_id != current_user.id: abort(403)
    db.session.delete(result)
    db.session.commit()
    return redirect(url_for('views.study_marks'))

@views.route('/study/marks/analyse/<int:result_id>', methods=['POST'])
@login_required
def study_marks_analyse(result_id):
    result = ExamResult.query.get_or_404(result_id)
    if result.user_id != current_user.id: abort(403)
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key: return jsonify({'error': 'AI not configured'}), 500
    marks_lines = '\n'.join(
        f"- {m.subject_name}: {m.marks_obtained}/{m.marks_total} ({m.marks_obtained/m.marks_total*100:.1f}%)"
        for m in result.marks
    )
    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    course_line = f"Course: {profile.course}" if profile and getattr(profile, 'course', None) else ''
    prompt = f"""You are a personal study coach helping a student improve after their exam.

Exam: {result.name}
{course_line}

Results:
{marks_lines}

For EACH subject listed, give ONE specific, actionable study tip based on their score.
Score guide: <50% = urgent recovery strategy, 50-75% = focused improvement, >75% = maintain + excel.

Keep every tip under 25 words. Be warm and encouraging, not harsh.

Return ONLY valid JSON:
{{"tips": [{{"subject": "Exact Subject Name", "tip": "Study tip here.", "score_pct": 85.0}}]}}"""
    try:
        raw  = _gemini_call(api_key, prompt, timeout=30)
        data = json.loads(raw)
        tips_map = {t['subject'].lower().strip(): t for t in data.get('tips', [])}
        for mark in result.marks:
            td = tips_map.get(mark.subject_name.lower().strip())
            if td: mark.ai_tip = td.get('tip', '')
        db.session.commit()
        return jsonify({'ok': True, 'tips': data.get('tips', [])})
    except Exception as e:
        msg = str(e)
        if '503' in msg or 'unavailable' in msg.lower():
            user_msg = 'AI is busy right now. Please try again in a moment.'
        elif '429' in msg or 'quota' in msg.lower():
            user_msg = 'AI rate limit reached. Please wait a minute and try again.'
        elif 'api_key' in msg.lower() or '400' in msg:
            user_msg = 'AI configuration issue. Please contact support.'
        else:
            user_msg = 'AI could not generate tips right now. Please try again.'
        return jsonify({'error': user_msg}), 500

@views.route('/study/setup', methods=['POST'])
@login_required
def study_setup_save():
    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = StudentProfile(user_id=current_user.id)
        db.session.add(profile)
    inst_type = request.form.get('institution_type', 'school').strip()
    profile.institution_type = inst_type if inst_type in ('school', 'college') else 'school'
    profile.institution  = request.form.get('institution', '').strip()[:200] or None
    profile.course       = request.form.get('course', '').strip()[:200] or None
    profile.roll_number  = request.form.get('roll_number', '').strip()[:100] or None
    if inst_type == 'school':
        profile.class_name = request.form.get('class_name', '').strip()[:50] or None
        profile.section    = request.form.get('section', '').strip()[:20] or None
        profile.department = None; profile.year = None; profile.semester = None
    else:
        profile.department = request.form.get('department', '').strip()[:200] or None
        profile.year       = request.form.get('year', '').strip()[:50] or None
        profile.semester   = request.form.get('semester', '').strip()[:50] or None
        profile.class_name = None; profile.section = None

    photo = request.files.get('photo')
    if photo and photo.filename:
        ext = os.path.splitext(photo.filename)[1].lower()
        if ext in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}:
            profile.photo_filename = upload_file(photo, "profile_photos")

    db.session.commit()
    return redirect(url_for('views.study_profile'))

@views.route('/study/add-subject', methods=['POST'])
@login_required
def add_subject():
    name    = request.form.get('name', '').strip()[:100]
    code    = request.form.get('code', '').strip()[:20]
    color   = request.form.get('color', '#E07B39')
    teacher = request.form.get('teacher', '').strip()[:100]
    if name:
        db.session.add(Subject(user_id=current_user.id, name=name, code=code,
                               color=color, teacher=teacher))
        db.session.commit()
    return redirect(url_for('views.study_subjects'))

@views.route('/study/edit-subject/<int:id>', methods=['POST'])
@login_required
def edit_subject(id):
    s = Subject.query.get_or_404(id)
    if s.user_id != current_user.id: abort(403)
    s.name    = request.form.get('name', s.name).strip()[:100]
    s.code    = request.form.get('code', s.code or '').strip()[:20]
    s.color   = request.form.get('color', s.color)
    s.teacher = request.form.get('teacher', s.teacher or '').strip()[:100]
    db.session.commit()
    return redirect(url_for('views.study_subjects'))

@views.route('/study/delete-subject/<int:id>', methods=['POST'])
@login_required
def delete_subject(id):
    s = Subject.query.get_or_404(id)
    if s.user_id != current_user.id: abort(403)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for('views.study_subjects'))

@views.route('/study/timetable')
@login_required
def study_timetable():
    subjects = Subject.query.filter_by(user_id=current_user.id).order_by(Subject.name).all()
    all_slots = []
    for s in subjects:
        for sl in s.slots:
            all_slots.append({'id': sl.id, 'subject_id': s.id, 'subject_name': s.name,
                              'color': s.color, 'day': sl.day_of_week,
                              'start': sl.start_time, 'end': sl.end_time, 'room': sl.room or '',
                              'reminder_note': sl.reminder_note or '',
                              'reminder_time': sl.reminder_time or ''})
    today = datetime.now(timezone.utc).date()
    return render_template('study_timetable.html', subjects=subjects,
                           slots=all_slots, today=today, active_page='study')

@views.route('/study/timetable/add-slot', methods=['POST'])
@login_required
def add_class_slot():
    data = request.get_json() or {}
    subj = Subject.query.get_or_404(int(data.get('subject_id', 0)))
    if subj.user_id != current_user.id: abort(403)
    sl = ClassSlot(subject_id=subj.id,
                   day_of_week=int(data.get('day', 0)),
                   start_time=data.get('start_time', '08:00'),
                   end_time=data.get('end_time', '09:00'),
                   room=data.get('room', '') or None,
                   reminder_note=data.get('reminder_note', '').strip() or None,
                   reminder_time=data.get('reminder_time', '').strip() or None)
    db.session.add(sl)
    db.session.commit()
    return jsonify({'status': 'ok', 'id': sl.id, 'subject_name': subj.name,
                    'color': subj.color, 'room': sl.room or ''})

@views.route('/study/timetable/delete-slot/<int:id>', methods=['POST'])
@login_required
def delete_class_slot(id):
    sl = ClassSlot.query.get_or_404(id)
    if sl.subject.user_id != current_user.id: abort(403)
    db.session.delete(sl)
    db.session.commit()
    return jsonify({'status': 'ok'})

@views.route('/api/timetable-reminders-today')
@login_required
def timetable_reminders_today():
    today_dow = datetime.now(timezone.utc).weekday()
    if today_dow > 5:
        return jsonify({'slots': [], 'earliest_class': None})
    subjects = Subject.query.filter_by(user_id=current_user.id).all()
    result = []
    all_starts = []
    for s in subjects:
        for sl in s.slots:
            if sl.day_of_week == today_dow:
                all_starts.append(sl.start_time)
                if sl.reminder_note and sl.reminder_time:
                    result.append({
                        'subject': s.name,
                        'start_time': sl.start_time,
                        'reminder_note': sl.reminder_note,
                        'reminder_time': sl.reminder_time,
                    })
    result.sort(key=lambda x: x['start_time'])
    earliest = min(all_starts) if all_starts else None
    return jsonify({'slots': result, 'earliest_class': earliest})

# ── Push Notifications ────────────────────────────────────────────────────────

def _get_vapid_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    raw = os.environ.get('VAPID_PRIVATE_KEY', '')
    if not raw:
        return None
    padded   = raw + '=' * (-len(raw) % 4)
    d_bytes  = base64.urlsafe_b64decode(padded)
    d_int    = int.from_bytes(d_bytes, 'big')
    return ec.derive_private_key(d_int, ec.SECP256R1())

def _send_push(subscription, title, body, url='/'):
    import tempfile
    tmp = None
    try:
        from pywebpush import webpush, WebPushException
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
        priv_key = _get_vapid_key()
        if not priv_key:
            return False, 'VAPID_PRIVATE_KEY not set'
        pem = priv_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        tmp.write(pem)
        tmp.flush()
        tmp.close()
        webpush(
            subscription_info={
                'endpoint': subscription.endpoint,
                'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}
            },
            data=json.dumps({'title': title, 'body': body, 'url': url}),
            vapid_private_key=tmp.name,
            vapid_claims={'sub': 'mailto:' + os.environ.get('BREVO_SENDER_EMAIL', 'letsgomonad@gmail.com')}
        )
        return True, None
    except Exception as e:
        print(f'Push error: {e}')
        return False, str(e)
    finally:
        if tmp:
            try: os.unlink(tmp.name)
            except: pass

@views.route('/api/push/test', methods=['GET'])
@login_required
def push_test():
    subs = PushSubscription.query.filter_by(user_id=current_user.id).all()
    if not subs:
        return jsonify({'error': 'no subscriptions for your account'})
    results = []
    for sub in subs:
        ok, err = _send_push(sub, title='monad test', body='Push notifications are working!', url='/')
        results.append({'ok': ok, 'error': err})
    return jsonify({'results': results})

@views.route('/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    data = request.get_json()
    if not data or not data.get('endpoint'):
        return jsonify({'error': 'invalid'}), 400
    existing = PushSubscription.query.filter_by(endpoint=data['endpoint']).first()
    if existing:
        existing.p256dh = data['keys']['p256dh']
        existing.auth   = data['keys']['auth']
    else:
        sub = PushSubscription(
            user_id  = current_user.id,
            endpoint = data['endpoint'],
            p256dh   = data['keys']['p256dh'],
            auth     = data['keys']['auth']
        )
        db.session.add(sub)
    db.session.commit()
    return jsonify({'status': 'ok'})

@views.route('/api/user/tz', methods=['POST'])
@login_required
def save_tz():
    data = request.get_json()
    offset = data.get('offset') if data else None
    if offset is not None:
        current_user.tz_offset = int(offset)
        db.session.commit()
    return jsonify({'ok': True})

@views.route('/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    data = request.get_json()
    endpoint = data.get('endpoint') if data else None
    if endpoint:
        PushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user.id).delete()
    else:
        PushSubscription.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({'status': 'ok'})

@views.route('/api/push/habit-reminder', methods=['POST', 'GET'])
def send_habit_reminder():
    """Habit + journal evening reminder. Run once at 8 PM via cron."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret', '')
    if secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'error': 'unauthorized'}), 401
    today = date.today()
    subs  = PushSubscription.query.all()
    sent  = 0
    for sub in subs:
        user = User.query.get(sub.user_id)
        if not user:
            continue
        name = user.name.split()[0] if user.name else 'there'

        # habits check
        habit_month = HabitMonth.query.filter_by(
            user_id=sub.user_id, year=today.year, month=today.month
        ).first()
        habits = Habit.query.filter_by(habit_month_id=habit_month.id).all() if habit_month else []
        pending_habits = [
            h for h in habits
            if not HabitLog.query.filter_by(habit_id=h.id, date=today, completed=True).first()
        ]

        # journal check
        has_journal = DailyJournal.query.filter_by(user_id=sub.user_id, date=today).first()

        parts = []
        if pending_habits:
            parts.append(f"{len(pending_habits)} habit{'s' if len(pending_habits) != 1 else ''} pending")
        if not has_journal:
            parts.append("journal not filled")

        if not parts:
            continue

        ok, _ = _send_push(
            sub,
            title='monad · evening check-in',
            body=f"Hey {name}! {' · '.join(parts)}.",
            url='/daily'
        )
        if ok:
            sent += 1
    return jsonify({'sent': sent})

@views.route('/api/push/morning-reminder', methods=['POST', 'GET'])
def send_morning_reminder():
    """Morning push at each user's wake_time. Run every 15 min via cron."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret', '')
    if secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'error': 'unauthorized'}), 401
    now      = datetime.utcnow()
    today    = now.date()
    tomorrow = today + timedelta(days=1)
    now_min  = now.hour * 60 + now.minute  # current UTC minute-of-day
    subs     = PushSubscription.query.all()
    sent     = 0
    for sub in subs:
        user = User.query.get(sub.user_id)
        if not user or not user.wake_time:
            continue
        # Parse user's wake_time (stored as "HH:MM", treated as UTC)
        try:
            wh, wm  = map(int, user.wake_time.split(':'))
            wake_min = wh * 60 + wm
        except Exception:
            continue
        # Only fire if current time is within a 15-minute window of their wake time
        if not (wake_min <= now_min < wake_min + 15):
            continue
        name = user.name.split()[0] if user.name else 'there'
        tasks_today = Task.query.filter_by(user_id=sub.user_id, completed=False).filter(
            db.func.date(Task.date) == today
        ).all()
        exams_soon = Exam.query.join(Subject).filter(
            Subject.user_id == sub.user_id,
            Exam.date.in_([today, tomorrow])
        ).all()
        parts = []
        if tasks_today:
            parts.append(f"{len(tasks_today)} task{'s' if len(tasks_today) != 1 else ''} today")
        if exams_soon:
            for ex in exams_soon:
                label = 'today' if ex.date == today else 'tomorrow'
                parts.append(f"{ex.title} {label}")
        if not parts:
            continue
        body = f"Good morning, {name}! " + ", ".join(parts) + "."
        ok, _ = _send_push(sub, title='monad · good morning', body=body, url='/daily')
        if ok:
            sent += 1
    return jsonify({'sent': sent})


@views.route('/api/push/class-reminder', methods=['POST', 'GET'])
def send_class_reminder():
    """Class slot reminder push. Run every 15 min — fires at each slot's reminder_time."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret', '')
    if secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'error': 'unauthorized'}), 401
    now       = datetime.utcnow()
    today_dow = now.weekday()
    if today_dow > 5:
        return jsonify({'sent': 0})
    now_min = now.hour * 60 + now.minute
    win_end = now_min + 15
    subs    = PushSubscription.query.all()
    sent    = 0
    for sub in subs:
        user = User.query.get(sub.user_id)
        if not user:
            continue
        subjects = Subject.query.filter_by(user_id=sub.user_id).all()
        for subject in subjects:
            for slot in subject.slots:
                if slot.day_of_week != today_dow:
                    continue
                if not slot.reminder_time:
                    continue
                try:
                    rh, rm     = map(int, slot.reminder_time.split(':'))
                    remind_min = rh * 60 + rm
                except Exception:
                    continue
                if now_min <= remind_min < win_end and slot.reminder_note:
                    _send_push(
                        sub,
                        title=f'monad · {subject.name}',
                        body=slot.reminder_note,
                        url='/study/timetable'
                    )
                    sent += 1
    return jsonify({'sent': sent})

@views.route('/api/push/exam-reminder', methods=['POST', 'GET'])
def send_exam_reminder():
    """Exam countdown push. Run once daily (morning)."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret', '')
    if secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'error': 'unauthorized'}), 401
    today    = date.today()
    subs     = PushSubscription.query.all()
    sent     = 0
    for sub in subs:
        user = User.query.get(sub.user_id)
        if not user:
            continue
        subjects = Subject.query.filter_by(user_id=sub.user_id).all()
        subject_ids = [s.id for s in subjects]
        if not subject_ids:
            continue
        upcoming = Exam.query.filter(
            Exam.subject_id.in_(subject_ids),
            Exam.date >= today,
            Exam.date <= today + timedelta(days=7)
        ).order_by(Exam.date).all()
        for exam in upcoming:
            delta = (exam.date - today).days
            if delta == 0:
                label = 'today'
            elif delta == 1:
                label = 'tomorrow'
            else:
                label = f'in {delta} days'
            subj = Subject.query.get(exam.subject_id)
            time_str = f' at {exam.time}' if exam.time else ''
            ok, _ = _send_push(
                sub,
                title='monad · exam countdown',
                body=f'{subj.name} — {exam.title} {label}{time_str}',
                url='/study/exams'
            )
            if ok:
                sent += 1
    return jsonify({'sent': sent})

@views.route('/api/push/task-reminder', methods=['POST', 'GET'])
def send_task_reminder():
    """Task due-time and overdue push. Run every 15 min."""
    secret = request.headers.get('X-Cron-Secret') or request.args.get('secret', '')
    if secret != os.environ.get('CRON_SECRET', ''):
        return jsonify({'error': 'unauthorized'}), 401
    now_utc     = datetime.utcnow()
    now_min_utc = now_utc.hour * 60 + now_utc.minute
    subs        = PushSubscription.query.all()
    sent        = 0
    for sub in subs:
        user = User.query.get(sub.user_id)
        if not user or user.tz_offset is None:
            continue
        local_min  = (now_min_utc + user.tz_offset) % 1440
        local_date = (now_utc + timedelta(minutes=user.tz_offset)).date()
        tasks = Task.query.filter(
            Task.user_id            == sub.user_id,
            Task.due_time.isnot(None),
            Task.completed          == False,
            db.func.date(Task.date) == local_date,
        ).all()
        for task in tasks:
            try:
                h, m    = map(int, task.due_time.split(':'))
                due_min = h * 60 + m

                # pre-due reminder (requires reminder_offset set)
                if task.reminder_offset is not None:
                    remind_at = due_min + task.reminder_offset
                    if remind_at <= local_min < remind_at + 15:
                        label = 'Due now' if task.reminder_offset == 0 else f'Due in {abs(task.reminder_offset)} min'
                        ok, _ = _send_push(sub, title='monad · task', body=f'{label}: {task.content}', url='/daily')
                        if ok:
                            sent += 1
                        continue   # don't double-fire overdue on same cycle

                # overdue check: fires in the 15-min window 15 min after due time
                overdue_at = due_min + 15
                if overdue_at <= local_min < overdue_at + 15:
                    ok, _ = _send_push(sub, title='monad · overdue', body=f'Still pending: {task.content}', url='/daily')
                    if ok:
                        sent += 1
            except Exception:
                continue
    return jsonify({'sent': sent})

@views.route('/study/exams')
@login_required
def study_exams():
    from datetime import timedelta
    subjects = Subject.query.filter_by(user_id=current_user.id).order_by(Subject.name).all()
    today = datetime.now(timezone.utc).date()
    exams = Exam.query.join(Subject).filter(Subject.user_id == current_user.id,
                                             Exam.date >= today).order_by(Exam.date).all()
    this_week_end = today + timedelta(days=7)
    next_week_end = today + timedelta(days=14)
    return render_template('study_exams.html', subjects=subjects, exams=exams,
                           today=today, this_week_end=this_week_end,
                           next_week_end=next_week_end, active_page='study')

@views.route('/study/exams/add', methods=['POST'])
@login_required
def add_exam():
    data       = request.get_json() or {}
    subject_id = int(data.get('subject_id', 0))
    subj       = Subject.query.get_or_404(subject_id)
    if subj.user_id != current_user.id: abort(403)
    date_str   = data.get('date', '')
    try:
        exam_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'status': 'error', 'msg': 'Invalid date'}), 400
    exam = Exam(subject_id=subject_id,
                title=str(data.get('title', 'Exam'))[:200],
                exam_type=data.get('exam_type', 'exam'),
                date=exam_date,
                time=data.get('time') or None,
                weightage=int(data.get('weightage', 0)) or None)
    db.session.add(exam)
    db.session.commit()
    return jsonify({'status': 'ok', 'id': exam.id})

@views.route('/study/exams/delete/<int:id>', methods=['POST'])
@login_required
def delete_exam(id):
    exam = Exam.query.get_or_404(id)
    if exam.subject.user_id != current_user.id: abort(403)
    db.session.delete(exam)
    db.session.commit()
    return jsonify({'status': 'ok'})

# ── Student Profile ───────────────────────────────────────────────────────────

@views.route('/study/profile')
@login_required
def study_profile():
    profile  = StudentProfile.query.filter_by(user_id=current_user.id).first()
    subjects = Subject.query.filter_by(user_id=current_user.id).all()
    today    = datetime.now(timezone.utc).date()
    # classes per week
    total_slots = sum(len(s.slots) for s in subjects)
    # upcoming exams
    exams_soon = (Exam.query.join(Subject)
                  .filter(Subject.user_id == current_user.id, Exam.date >= today)
                  .count())
    # study hours this month
    month_start = today.replace(day=1)
    focus_rows  = FocusSession.query.filter(
        FocusSession.user_id == current_user.id,
        FocusSession.date >= month_start,
        FocusSession.completed == True).all()
    study_hours = round(sum(f.duration for f in focus_rows) / 3600, 1)
    return render_template('study_profile.html',
        profile=profile, subjects=subjects,
        total_slots=total_slots, exams_soon=exams_soon,
        study_hours=study_hours, active_page='study')

@views.route('/study/profile/save', methods=['POST'])
@login_required
def study_profile_save():
    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = StudentProfile(user_id=current_user.id)
        db.session.add(profile)
    inst_type = request.form.get('institution_type', '').strip()
    profile.institution_type = inst_type if inst_type in ('school', 'college') else None
    profile.institution = request.form.get('institution', '').strip()[:200] or None
    profile.course      = request.form.get('course', '').strip()[:200] or None
    profile.roll_number = request.form.get('roll_number', '').strip()[:100] or None
    if inst_type == 'school':
        profile.class_name  = request.form.get('class_name', '').strip()[:50] or None
        profile.section     = request.form.get('section', '').strip()[:20] or None
        profile.department  = None
        profile.year        = None
        profile.semester    = None
    else:
        profile.department  = request.form.get('department', '').strip()[:200] or None
        profile.year        = request.form.get('year', '').strip()[:50] or None
        profile.semester    = request.form.get('semester', '').strip()[:50] or None
        profile.class_name  = None
        profile.section     = None
    db.session.commit()
    return redirect(url_for('views.study_profile'))

@views.route('/study/profile/upload-photo', methods=['POST'])
@login_required
def study_profile_upload_photo():
    f = request.files.get('photo')
    if not f or not f.filename:
        return jsonify({'status': 'error', 'msg': 'No file'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}:
        return jsonify({'status': 'error', 'msg': 'Invalid type'}), 400
    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        profile = StudentProfile(user_id=current_user.id)
        db.session.add(profile)
    storage_path = upload_file(f, "profile_photos")
    profile.photo_filename = storage_path
    db.session.commit()
    return jsonify({'status': 'ok', 'url': get_public_url(storage_path)})

@views.route('/study/profile/delete-photo', methods=['POST'])
@login_required
def study_profile_delete_photo():
    profile = StudentProfile.query.filter_by(user_id=current_user.id).first()
    if not profile or not profile.photo_filename:
        return jsonify({'status': 'ok'})

    profile.photo_filename = None
    db.session.commit()
    return jsonify({'status': 'ok'})

@views.route('/study/exams/<int:id>/create-task', methods=['POST'])
@login_required
def exam_create_task(id):
    exam = Exam.query.get_or_404(id)
    if exam.subject.user_id != current_user.id: abort(403)
    task = Task(user_id=current_user.id,
                content=f"Prepare for {exam.subject.name} {exam.exam_type}: {exam.title}",
                date=datetime.combine(exam.date, datetime.min.time()),
                category=exam.subject.name,
                priority='high')
    db.session.add(task)
    exam.linked_task_id = task.id
    db.session.commit()
    return jsonify({'status': 'ok', 'task_id': task.id})

@views.route('/study/notes/add', methods=['POST'])
@login_required
def add_study_note():
    subject_id = int(request.form.get('subject_id', 0))
    subj = Subject.query.get_or_404(subject_id)
    if subj.user_id != current_user.id: abort(403)
    title   = request.form.get('title', '').strip()[:200]
    content = request.form.get('content', '').strip()
    topic   = request.form.get('topic', '').strip()[:100]
    file_path = file_type = None
    f = request.files.get('file')
    if f and f.filename:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext in {'pdf', 'docx', 'png', 'jpg', 'jpeg', 'gif', 'webp'}:
            file_path = upload_file(f, "study_notes")
            file_type = 'img' if ext in {'png','jpg','jpeg','gif','webp'} else ext
    note = StudyNote(subject_id=subject_id, title=title or f.filename or 'Note',
                     content=content or None, file_path=file_path,
                     file_type=file_type, topic=topic or None)
    db.session.add(note)
    db.session.commit()
    return redirect(url_for('views.study_subject_detail', id=subject_id))

@views.route('/study/notes/delete/<int:id>', methods=['POST'])
@login_required
def delete_study_note(id):
    note = StudyNote.query.get_or_404(id)
    if note.subject.user_id != current_user.id: abort(403)
    db.session.delete(note)
    db.session.commit()
    return jsonify({'status': 'ok'})

@views.route('/study/subject/<int:id>')
@login_required
def study_subject_detail(id):
    subj  = Subject.query.get_or_404(id)
    if subj.user_id != current_user.id: abort(403)
    today = datetime.now(timezone.utc).date()
    exams = Exam.query.filter_by(subject_id=id).filter(Exam.date >= today).order_by(Exam.date).all()
    notes = StudyNote.query.filter_by(subject_id=id).order_by(StudyNote.created_at.desc()).all()
    return render_template('study_subject_detail.html', subject=subj, upcoming_exams=exams,
                           notes=notes, today=today, active_page='study')
