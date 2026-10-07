"""
routes/auth.py
--------------
Login and logout routes.
"""
import os
from flask import Blueprint, render_template, request, redirect, url_for, session

auth_bp = Blueprint('auth', __name__)


import threading

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == os.getenv('ADMIN_PASSWORD', 'admin123'):
            session['logged_in'] = True
            return redirect(url_for('dashboard.index'))
        return render_template('login.html', error='Invalid password')
        
    # GET Request: CACHE WARMING!
    # Silently fetch the 3095 sellers into RAM while the user types their password.
    from backend.routes.dashboard import get_seller_map
    threading.Thread(target=get_seller_map, daemon=True).start()
    
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('auth.login'))
