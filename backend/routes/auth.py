"""
routes/auth.py
--------------
Login and logout routes.
"""
import hmac
import os
import threading

from flask import Blueprint, redirect, render_template, request, session, url_for

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password') or ''
        if hmac.compare_digest(pwd.encode(), os.environ['ADMIN_PASSWORD'].encode()):
            session.clear()
            session['logged_in'] = True
            return redirect(url_for('dashboard.index'))
        return render_template('login.html', error='Invalid password'), 401

    # Warm the seller cache in the background while the user types.
    from backend.routes.dashboard import get_sellers
    threading.Thread(target=get_sellers, daemon=True).start()
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
