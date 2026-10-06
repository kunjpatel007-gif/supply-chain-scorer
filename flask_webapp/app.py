import os
from flask import Flask

def create_app():
    app = Flask(__name__)
    app.secret_key = 'super_secret_flask_key'

    # Register blueprints
    from backend.routes.auth import auth_bp
    from backend.routes.dashboard import dashboard_bp
    from backend.routes.sellers import sellers_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(sellers_bp)

    return app

app = create_app()

if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='127.0.0.1', port=5000, debug=debug, threaded=True)
