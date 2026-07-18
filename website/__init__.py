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
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE']   = True


    # Database (Supabase PostgreSQL)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    
    # ✅ Max upload size: 10 MB
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

    # Flask-Mail (Gmail SMTP)
    app.config['MAIL_SERVER']        = 'smtp.gmail.com'
    app.config['MAIL_PORT']          = 587
    app.config['MAIL_USE_TLS']       = True
    app.config['MAIL_USERNAME']      = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD']      = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_CONNECT_TIMEOUT'] = 10
    app.config['MAIL_TIMEOUT']         = 10
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

    # Google OAuth
    app.config['GOOGLE_CLIENT_ID']     = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)
    print(app.config["MAIL_USERNAME"])
    oauth.init_app(app)
    from .storage import get_public_url
    from datetime import datetime
    app.jinja_env.globals["get_public_url"] = get_public_url
    app.jinja_env.globals["vapid_public_key"] = os.environ.get("VAPID_PUBLIC_KEY", "")
    app.jinja_env.globals["datetime"] = datetime

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

    # Import models so SQLAlchemy is aware of them
    from .models import User, Task, HabitMonth, Habit, HabitLog, DailyJournal, DailyPhoto, FocusSession, Community, CommunityMember, CommunityHabit, CommunityHabitLog, Achievement, TimeBlock, Category, Subject, ClassSlot, Exam, StudyNote, Message, StudentProfile, ExamResult, SubjectMark, PushSubscription

    # Create any missing tables (safe — skips tables that already exist)
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print(f'db.create_all warning: {e}')

    # ✅ Login manager setup
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(id):
        return db.session.get(User, int(id))

    return app
