import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, render_template  # noqa: E402
from flask_wtf.csrf import CSRFProtect  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from project_env import load_env  # noqa: E402

csrf = CSRFProtect()
log = logging.getLogger(__name__)
REQUIRED_ENV = ('SECRET_KEY', 'ADMIN_PASSWORD')


def create_app(test_config=None):
    load_env()
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Set {', '.join(missing)} in .env or the environment before starting.")

    app = Flask(__name__)
    app.secret_key = os.environ['SECRET_KEY']
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    if test_config:
        app.config.update(test_config)
    csrf.init_app(app)

    from backend.routes.auth import auth_bp
    from backend.routes.dashboard import dashboard_bp
    from backend.routes.sellers import sellers_bp
    from backend.routes.simulator import simulator_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(sellers_bp)
    app.register_blueprint(simulator_bp)

    @app.errorhandler(Exception)
    def handle_error(exc):
        if isinstance(exc, HTTPException):
            if exc.code == 404:
                return render_template('error.html', title='Not found',
                                       message="That page or seller doesn't exist."), 404
            if exc.code == 400:
                return render_template('error.html', title='Request rejected',
                                       message='The form expired or was invalid. Go back, refresh and try again.'), 400
            return exc
        log.exception("Unhandled error")
        return render_template('error.html', title='Something went wrong',
                               message='The database may be unavailable. Try again in a moment.'), 503

    return app


app = create_app()

if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='127.0.0.1', port=5000, debug=debug, threaded=True)
