from . import db
from flask_login import UserMixin
from datetime import datetime, date

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150))
    password = db.Column(db.String(150), nullable=False)
    theme = db.Column(db.String(20), default='default')
    pet = db.Column(db.String(20), default='dog')
    pet_name = db.Column(db.String(50), nullable=True)
    year_goal = db.Column(db.String(200), nullable=True)
    motto = db.Column(db.String(200), nullable=True)
    time_format = db.Column(db.String(3), default='12h')
    onboarding_complete = db.Column(db.Boolean, default=False)
    goals = db.Column(db.Text, nullable=True)
    life_phase = db.Column(db.String(20), default='other')
    # Extended profile — collected at onboarding, used by all AI features
    main_goal = db.Column(db.String(50), nullable=True)
    challenges = db.Column(db.String(200), nullable=True)   # comma-separated
    available_time = db.Column(db.String(20), nullable=True)
    wake_time = db.Column(db.String(20), nullable=True)
    sleep_time = db.Column(db.String(20), nullable=True)
    routine_text = db.Column(db.Text, nullable=True)
    ai_summary = db.Column(db.Text, nullable=True)          # AI-written profile paragraph
    ai_report_json = db.Column(db.Text, nullable=True)
    ai_report_date = db.Column(db.Date, nullable=True)
    study_enabled  = db.Column(db.Boolean, default=True)
    tasks = db.relationship('Task', backref='user', lazy=True)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(500), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    completed = db.Column(db.Boolean, default=False)
    quick_note = db.Column(db.String(200), nullable=True)
    focus_time = db.Column(db.Integer, default=0)
    session_count = db.Column(db.Integer, default=0)
    priority = db.Column(db.String(10), nullable=True)
    due_time = db.Column(db.String(5), nullable=True)
    end_time = db.Column(db.String(5), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    recurring_days = db.Column(db.String(20), nullable=True)      # e.g. "0,2,4" Mon=0…Sun=6
    recurring_task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)
    reminder_offset = db.Column(db.Integer, nullable=True)         # None=off, 0=at due time, -5=5 min before
    category = db.Column(db.String(100), nullable=True)
    focus_sessions = db.relationship('FocusSession', backref='task', lazy=True, cascade="all, delete-orphan")

class FocusSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    mode = db.Column(db.String(20))
    date = db.Column(db.DateTime, default=datetime.utcnow)
    completed = db.Column(db.Boolean, default=True)

class HabitMonth(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    habits = db.relationship('Habit', backref='habit_month', lazy=True, cascade="all, delete-orphan")

class Habit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    frequency = db.Column(db.String(30), nullable=True, default='daily')  # daily|2x|3x|4x|5x|days:0,2,4
    habit_month_id = db.Column(db.Integer, db.ForeignKey('habit_month.id'))
    logs = db.relationship('HabitLog', backref='habit', lazy=True, cascade="all, delete-orphan")

class HabitLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    habit_id = db.Column(db.Integer, db.ForeignKey('habit.id'))
    date = db.Column(db.Date, nullable=False)
    completed = db.Column(db.Boolean, default=False)
    reflection = db.Column(db.Text, nullable=True)

class DailyJournal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    mood = db.Column(db.String(20))
    accomplishments = db.Column(db.Text)
    improvements = db.Column(db.Text)
    learnings = db.Column(db.Text)
    gratitude = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DailyPhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    caption = db.Column(db.String(300), nullable=True)
    is_final = db.Column(db.Boolean, default=False)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)
    habit_log_id = db.Column(db.Integer, db.ForeignKey('habit_log.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Community(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    invite_code = db.Column(db.String(20), unique=True, nullable=False)
    category = db.Column(db.String(50))
    goal = db.Column(db.String(200), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    members = db.relationship('CommunityMember', backref='community', lazy=True, cascade="all, delete-orphan")
    habits = db.relationship('CommunityHabit', backref='community', lazy=True, cascade="all, delete-orphan")

class CommunityMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey('community.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

class CommunityHabit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.Integer, db.ForeignKey('community.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    logs = db.relationship('CommunityHabitLog', backref='community_habit', lazy=True, cascade="all, delete-orphan")

class CommunityHabitLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community_habit_id = db.Column(db.Integer, db.ForeignKey('community_habit.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    completed = db.Column(db.Boolean, default=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    community_id = db.Column(db.Integer, db.ForeignKey('community.id'), nullable=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    content = db.Column(db.Text, nullable=True)
    filename = db.Column(db.String(300), nullable=True)
    original_name = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='received_messages')

class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    earned = db.Column(db.Boolean, default=False)
    stars  = db.Column(db.Integer, default=0)
    crowns = db.Column(db.Integer, default=0)

class Category(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name    = db.Column(db.String(100), nullable=False)
    color   = db.Column(db.String(20), default='#E07B39')

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TimeBlock(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date         = db.Column(db.Date, nullable=False)
    start_minute = db.Column(db.Integer, nullable=False)
    end_minute   = db.Column(db.Integer, nullable=False)
    title        = db.Column(db.String(200), nullable=False)
    block_type   = db.Column(db.String(20), default='custom')
    category     = db.Column(db.String(100), nullable=True)
    task_id      = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)
    color        = db.Column(db.String(20), nullable=True)
    completed    = db.Column(db.Boolean, default=False)

class StudentProfile(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    institution_type = db.Column(db.String(20),  nullable=True)   # 'school' or 'college'
    institution      = db.Column(db.String(200), nullable=True)
    course           = db.Column(db.String(200), nullable=True)
    class_name       = db.Column(db.String(50),  nullable=True)   # school only (e.g. "10th")
    section          = db.Column(db.String(20),  nullable=True)   # school only (e.g. "A")
    department       = db.Column(db.String(200), nullable=True)   # college only
    year             = db.Column(db.String(50),  nullable=True)   # college only
    semester         = db.Column(db.String(50),  nullable=True)   # college only
    roll_number      = db.Column(db.String(100), nullable=True)
    photo_filename   = db.Column(db.String(300), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

# ── Academics ────────────────────────────────────────────────────────────────

class Subject(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name    = db.Column(db.String(100), nullable=False)
    code    = db.Column(db.String(20), nullable=True)
    color   = db.Column(db.String(20), default='#E07B39')
    teacher = db.Column(db.String(100), nullable=True)
    slots   = db.relationship('ClassSlot',  backref='subject', lazy=True, cascade='all, delete-orphan')
    exams   = db.relationship('Exam',       backref='subject', lazy=True, cascade='all, delete-orphan')
    notes   = db.relationship('StudyNote',  backref='subject', lazy=True, cascade='all, delete-orphan')

class ClassSlot(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    subject_id    = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    day_of_week   = db.Column(db.Integer, nullable=False)   # 0=Mon … 5=Sat
    start_time    = db.Column(db.String(5), nullable=False)  # HH:MM
    end_time      = db.Column(db.String(5), nullable=False)
    room          = db.Column(db.String(50), nullable=True)
    reminder_note = db.Column(db.Text, nullable=True)
    reminder_time = db.Column(db.String(5), nullable=True)  # HH:MM, e.g. "08:00"

class Exam(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    subject_id     = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    title          = db.Column(db.String(200), nullable=False)
    exam_type      = db.Column(db.String(30), default='exam')   # exam/assignment/quiz/project
    date           = db.Column(db.Date, nullable=False)
    time           = db.Column(db.String(5), nullable=True)     # HH:MM
    weightage      = db.Column(db.Integer, nullable=True)       # percentage
    linked_task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True)

class StudyNote(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    title      = db.Column(db.String(200), nullable=False)
    content    = db.Column(db.Text, nullable=True)
    file_path  = db.Column(db.String(300), nullable=True)
    file_type  = db.Column(db.String(10), nullable=True)   # pdf/img/docx
    topic      = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ExamResult(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name       = db.Column(db.String(200), nullable=False)
    date       = db.Column(db.Date, nullable=True)
    end_date   = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    marks      = db.relationship('SubjectMark', backref='exam_result', lazy=True, cascade='all, delete-orphan')

class PushSubscription(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint   = db.Column(db.Text, nullable=False, unique=True)
    p256dh     = db.Column(db.Text, nullable=False)
    auth       = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SubjectMark(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    exam_result_id = db.Column(db.Integer, db.ForeignKey('exam_result.id'), nullable=False)
    subject_name   = db.Column(db.String(100), nullable=False)
    marks_obtained = db.Column(db.Float, nullable=False)
    marks_total    = db.Column(db.Float, nullable=False, default=100.0)
    ai_tip         = db.Column(db.Text, nullable=True)

