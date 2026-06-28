from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session,current_app
from .models import User
from . import db, limiter, mail, oauth
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import login_user, login_required, logout_user, current_user
from flask_mail import Message
import re, random, secrets, time

auth = Blueprint('auth', __name__)


def _send_otp(email, otp):
    print("=== OTP: Creating email ===", flush=True)

    msg = Message(
        subject='monad: your verification code',
        recipients=[email],
        body=f"Your monad verification code is: {otp}\n\nThis code expires in 10 minutes. If you didn't request this, you can safely ignore this email.",
        html=f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5;padding:32px 0">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#FFFDF9;border-radius:16px;border:1px solid #F0E8DF;padding:40px 32px;max-width:480px">
        <tr><td align="center" style="padding-bottom:20px">
          <div style="font-size:28px">✦</div>
          <h2 style="color:#2D2A26;font-size:20px;margin:12px 0 8px;font-family:Arial,sans-serif">Verify your email</h2>
          <p style="color:#8B7E74;font-size:14px;margin:0 0 28px;line-height:1.6">Enter this code in monad to continue.</p>
          <div style="background:#FDF6F0;border:2px solid #F0E8DF;border-radius:12px;padding:24px 20px;letter-spacing:12px;font-size:34px;font-weight:bold;color:#E07B39;font-family:Courier,monospace">
            {otp}
          </div>
          <p style="color:#8B7E74;font-size:12px;margin:24px 0 0;line-height:1.6">This code expires in 10 minutes.<br>If you didn't request this, ignore this email.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    )

    print("=== OTP: About to call mail.send() ===", flush=True)

    try:
        print("MAIL_USERNAME:", current_app.config["MAIL_USERNAME"])
        print("MAIL_SERVER:", current_app.config["MAIL_SERVER"])
        print("MAIL_PORT:", current_app.config["MAIL_PORT"])

        mail.send(msg)

        print("MAIL SENT SUCCESSFULLY")

    except Exception as e:
        print("MAIL ERROR:", repr(e))
        raise

    print("=== OTP: mail.send() finished successfully ===", flush=True)


@auth.route('/login', methods=['GET', 'POST'])
@limiter.limit("3 per minute; 10 per hour", methods=["POST"])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=True)
            flash('Logged in successfully!', category='success')
            if not user.onboarding_complete:
                return redirect(url_for('views.onboarding'))
            return redirect(url_for('views.home'))
        else:
            flash('Invalid email or password', category='error')

    return render_template("login.html")


@auth.route('/sign-up', methods=['GET', 'POST'])
@limiter.limit("5 per hour", methods=["POST"])
def sign_up():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash('Invalid email format.', category='error')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered. Please log in instead.', category='error')
            return redirect(url_for('auth.login'))
        else:
            otp = str(secrets.randbelow(900000) + 100000)
            session['pending_email'] = email
            session['otp_code']      = otp
            session['otp_expires']   = time.time() + 600

            try:
                _send_otp(email, otp)
            except Exception:
                flash('Could not send verification email. Check mail settings.', category='error')
                return render_template("sign_up.html")

            flash(f'A 6-digit code was sent to {email}', category='success')
            return redirect(url_for('auth.verify_email'))

    return render_template("sign_up.html")


@auth.route('/verify-email', methods=['GET', 'POST'])
@limiter.limit("10 per hour", methods=["POST"])
def verify_email():
    if 'pending_email' not in session:
        return redirect(url_for('auth.sign_up'))

    if request.method == 'POST':
        entered = request.form.get('otp', '').strip()

        if time.time() > session.get('otp_expires', 0):
            session.pop('pending_email', None)
            session.pop('otp_code', None)
            session.pop('otp_expires', None)
            flash('Code expired. Please sign up again.', category='error')
            return redirect(url_for('auth.sign_up'))

        if entered != session.get('otp_code'):
            flash('Incorrect code. Please try again.', category='error')
            return render_template("verify_email.html", email=session['pending_email'])

        # OTP correct — mark email verified, move to password step
        session.pop('otp_code', None)
        session.pop('otp_expires', None)
        session['email_verified'] = True
        return redirect(url_for('auth.complete_signup'))

    return render_template("verify_email.html", email=session['pending_email'])


@auth.route('/resend-otp', methods=['POST'])
@limiter.limit("3 per hour")
def resend_otp():
    if 'pending_email' not in session:
        return redirect(url_for('auth.sign_up'))

    otp = str(secrets.randbelow(900000) + 100000)
    session['otp_code']    = otp
    session['otp_expires'] = time.time() + 600

    try:
        _send_otp(session['pending_email'], otp)
        flash('A new code has been sent.', category='success')
    except Exception:
        flash('Could not send email. Try again shortly.', category='error')

    return redirect(url_for('auth.verify_email'))


@auth.route('/complete-signup', methods=['GET', 'POST'])
def complete_signup():
    if not session.get('email_verified') or 'pending_email' not in session:
        return redirect(url_for('auth.sign_up'))

    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')

        if not name:
            flash('Please enter your name.', category='error')
        elif len(password) < 7:
            flash('Password must be at least 7 characters.', category='error')
        elif not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            flash('Password must contain at least one special character.', category='error')
        elif password != confirm:
            flash('Passwords do not match.', category='error')
        else:
            email = session.pop('pending_email')
            session.pop('email_verified', None)

            new_user = User(
                email=email, name=name,
                password=generate_password_hash(password, method='pbkdf2:sha256')
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user, remember=True)
            flash('Account created successfully!', category='success')
            return redirect(url_for('views.onboarding'))

    return render_template("complete_signup.html", email=session['pending_email'])


@auth.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()
        # Always show the same message to avoid revealing whether email exists
        if user:
            otp = str(secrets.randbelow(900000) + 100000)
            session['reset_email']       = email
            session['reset_otp']         = otp
            session['reset_otp_expires'] = time.time() + 600
            try:
                _send_otp(email, otp)
            except Exception:
                flash('Could not send email. Check your mail settings.', category='error')
                return render_template("forgot_password.html")
        flash('If that email is registered, a code has been sent.', category='success')
        return redirect(url_for('auth.reset_verify'))
    return render_template("forgot_password.html")


@auth.route('/reset-verify', methods=['GET', 'POST'])
@limiter.limit("10 per hour", methods=["POST"])
def reset_verify():
    if 'reset_email' not in session:
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        entered = request.form.get('otp', '').strip()

        if time.time() > session.get('reset_otp_expires', 0):
            session.pop('reset_email', None)
            session.pop('reset_otp', None)
            session.pop('reset_otp_expires', None)
            flash('Code expired. Please try again.', category='error')
            return redirect(url_for('auth.forgot_password'))

        if entered != session.get('reset_otp'):
            flash('Incorrect code. Please try again.', category='error')
            return render_template("reset_verify.html", email=session['reset_email'])

        # OTP correct — allow password reset
        session.pop('reset_otp', None)
        session.pop('reset_otp_expires', None)
        session['reset_verified'] = True
        return redirect(url_for('auth.reset_password'))

    return render_template("reset_verify.html", email=session['reset_email'])


@auth.route('/resend-reset-otp', methods=['POST'])
@limiter.limit("3 per hour")
def resend_reset_otp():
    if 'reset_email' not in session:
        return redirect(url_for('auth.forgot_password'))
    otp = str(secrets.randbelow(900000) + 100000)
    session['reset_otp']         = otp
    session['reset_otp_expires'] = time.time() + 600
    try:
        _send_otp(session['reset_email'], otp)
        flash('A new code has been sent.', category='success')
    except Exception:
        flash('Could not send email. Try again shortly.', category='error')
    return redirect(url_for('auth.reset_verify'))


@auth.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if not session.get('reset_verified') or 'reset_email' not in session:
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')

        if len(password) < 7:
            flash('Password must be at least 7 characters.', category='error')
        elif not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            flash('Password must contain at least one special character.', category='error')
        elif password != confirm:
            flash('Passwords do not match.', category='error')
        else:
            user = User.query.filter_by(email=session['reset_email']).first()
            if user:
                user.password = generate_password_hash(password, method='pbkdf2:sha256')
                db.session.commit()
            session.pop('reset_email', None)
            session.pop('reset_verified', None)
            flash('Password updated! Please log in with your new password.', category='success')
            return redirect(url_for('auth.login'))

    return render_template("reset_password.html")


@auth.route('/auth/google')
def google_login():
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth.route('/auth/google/callback')
def google_callback():
    token     = oauth.google.authorize_access_token()
    user_info = token.get('userinfo')
    if not user_info:
        flash('Google login failed. Please try again.', category='error')
        return redirect(url_for('auth.login'))

    email = user_info['email'].lower()
    name  = user_info.get('name', email.split('@')[0])

    user = User.query.filter_by(email=email).first()
    if not user:
        # New user — create account instantly, no password needed
        user = User(
            email=email, name=name,
            password=generate_password_hash(random.randbytes(32).hex(), method='pbkdf2:sha256')
        )
        db.session.add(user)
        db.session.commit()
        login_user(user, remember=True)
        flash('Account created successfully!', category='success')
        return redirect(url_for('views.onboarding'))

    login_user(user, remember=True)
    flash('Logged in successfully!', category='success')
    if not user.onboarding_complete:
        return redirect(url_for('views.onboarding'))
    return redirect(url_for('views.home'))


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', category='success')
    return redirect(url_for('views.home'))


@auth.app_errorhandler(429)
def rate_limit_handler(e):
    flash('Too many attempts. Please wait a minute and try again.', category='error')
    return redirect(url_for('auth.login'))


@auth.route('/check-email')
def check_email():
    email = request.args.get('email')
    if not email:
        return jsonify({'exists': False})
    user = User.query.filter_by(email=email).first()
    return jsonify({'exists': bool(user)})