import gzip
import hashlib
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, render_template, request, url_for  # noqa: E402
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

    # UI_THEME=monolith switches to the new templates; anything else keeps the classic UI.
    theme = str((test_config or {}).get('UI_THEME') or os.getenv('UI_THEME', 'classic')).strip().lower()
    folder = 'templates_monolith' if theme == 'monolith' else 'templates'
    app = Flask(__name__, template_folder=folder)
    app.config['UI_THEME'] = 'monolith' if folder == 'templates_monolith' else 'classic'
    app.secret_key = os.environ['SECRET_KEY']
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    if test_config:
        app.config.update(test_config)
    csrf.init_app(app)
    _add_static_caching(app)
    _add_gzip(app)

    from backend.routes.auth import auth_bp
    from backend.routes.dashboard import dashboard_bp
    from backend.routes.data import data_bp
    from backend.routes.sellers import sellers_bp
    from backend.routes.simulator import simulator_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(sellers_bp)
    app.register_blueprint(simulator_bp)
    app.register_blueprint(data_bp)

    @app.route('/favicon.ico')
    @app.route('/.well-known/appspecific/com.chrome.devtools.json')
    def no_content():          # browser probes; answering them stops 404 noise in the log
        return '', 204

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


GZIP_TYPES = ('text/html', 'text/css', 'text/javascript', 'application/javascript', 'application/json', 'image/svg+xml')


def _add_static_caching(app):
    """Static files get content-hash URLs (?v=...) so browsers can cache them for a year."""
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000
    versions = {}

    def asset(path):
        if path not in versions:
            with open(os.path.join(app.static_folder, path), 'rb') as f:
                versions[path] = hashlib.sha1(f.read()).hexdigest()[:10]
        return url_for('static', filename=path, v=versions[path])

    app.context_processor(lambda: {'asset': asset})


def _add_gzip(app):
    """Compress text responses. Plain gzip only: Cloud Run has known issues passing other encodings through."""
    @app.after_request
    def gzip_response(resp):
        if (resp.status_code != 200 or 'Content-Encoding' in resp.headers
                or 'gzip' not in request.headers.get('Accept-Encoding', '').lower()
                or not (resp.mimetype or '').startswith(GZIP_TYPES)):
            return resp
        resp.direct_passthrough = False          # static css/js arrive as file streams
        data = resp.get_data()
        if len(data) < 1024:
            return resp
        resp.set_data(gzip.compress(data, compresslevel=6))
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Content-Length'] = str(len(resp.get_data()))
        resp.vary.add('Accept-Encoding')
        return resp


app = create_app()

if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='127.0.0.1', port=5000, debug=debug, threaded=True)
