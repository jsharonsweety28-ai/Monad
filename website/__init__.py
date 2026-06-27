from flask import Flask, app
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
from authlib.integrations.flask_client import OAuth
import os

db = SQLAlchemy()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
mail = Mail()
oauth = OAuth()

def create_app():
    app = Flask(__name__)

    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        raise RuntimeError('SECRET_KEY environment variable is required')
    app.config['SECRET_KEY'] = secret_key


    # Database (Supabase PostgreSQL)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    
    # ✅ Max upload size: 10 MB
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

    # Flask-Mail (Gmail SMTP)
    app.config['MAIL_SERVER']   = 'smtp.gmail.com'
    app.config['MAIL_PORT']     = 587
    app.config['MAIL_USE_TLS']  = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

    # Google OAuth
    app.config['GOOGLE_CLIENT_ID']     = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)
    oauth.init_app(app)
    from .storage import get_public_url
    app.jinja_env.globals["get_public_url"] = get_public_url

    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    # ✅ Import blueprints
    from .views import views
    from .auth import auth
    app.register_blueprint(views, url_prefix='/')
    app.register_blueprint(auth, url_prefix='/')

    # ✅ Import models so db.create_all() knows about them
    from .models import User, Task, HabitMonth, Habit, HabitLog, DailyJournal, DailyPhoto, FocusSession, Community, CommunityMember, CommunityHabit, CommunityHabitLog, Achievement, TimeBlock, Category, Subject, ClassSlot, Exam, StudyNote, Message, StudentProfile, ExamResult, SubjectMark
    # ✅ Permanent fix: always ensure tables exist
    with app.app_context():
        db.create_all()
        # SQLite column migrations — safe to run on every startup
        from sqlalchemy import text
        with db.engine.connect() as conn:
            for stmt in [
                "ALTER TABLE task ADD COLUMN recurring_days VARCHAR(20)",
                "ALTER TABLE task ADD COLUMN recurring_task_id INTEGER REFERENCES task(id)",
                "ALTER TABLE task ADD COLUMN reminder_offset INTEGER",
                "ALTER TABLE habit ADD COLUMN frequency VARCHAR(30) DEFAULT 'daily'",
                "ALTER TABLE user ADD COLUMN time_format VARCHAR(3) DEFAULT '12h'",
                "ALTER TABLE task ADD COLUMN category VARCHAR(100)",
                "ALTER TABLE task ADD COLUMN end_time VARCHAR(5)",
                "ALTER TABLE habit_log ADD COLUMN reflection TEXT",
                "ALTER TABLE daily_photo ADD COLUMN habit_log_id INTEGER REFERENCES habit_log(id)",
                "ALTER TABLE time_block ADD COLUMN category VARCHAR(100)",
                "ALTER TABLE message ADD COLUMN filename VARCHAR(300)",
                "ALTER TABLE message ADD COLUMN original_name VARCHAR(300)",
                "ALTER TABLE class_slot ADD COLUMN reminder_note TEXT",
                "ALTER TABLE class_slot ADD COLUMN reminder_time VARCHAR(5)",
                "ALTER TABLE student_profile ADD COLUMN institution_type VARCHAR(20)",
                "ALTER TABLE student_profile ADD COLUMN class_name VARCHAR(50)",
                "ALTER TABLE student_profile ADD COLUMN section VARCHAR(20)",
                "ALTER TABLE user ADD COLUMN pet_name VARCHAR(50)",
                "ALTER TABLE user ADD COLUMN year_goal VARCHAR(200)",
                "ALTER TABLE user ADD COLUMN motto VARCHAR(200)",
                "ALTER TABLE achievement ADD COLUMN crowns INTEGER DEFAULT 0",
                "ALTER TABLE user ADD COLUMN ai_report_json TEXT",
                "ALTER TABLE user ADD COLUMN ai_report_date DATE",
                "ALTER TABLE exam_result ADD COLUMN end_date DATE",
                "ALTER TABLE user ADD COLUMN study_enabled BOOLEAN DEFAULT 1",
            ]:
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception:
                    pass
        print("Database checked/initialized successfully!")

    # ✅ Login manager setup
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(id):
        return db.session.get(User, int(id))

    return app
