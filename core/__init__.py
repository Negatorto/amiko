import os
from datetime import timedelta
from flask import Flask
from flask_socketio import SocketIO
from core.plugins import load_modules
from core.config import settings
from core.auth import init_auth

app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.config['SECRET_KEY'] = os.environ.get('AMIKO_SECRET_KEY') or settings._load_or_create_key().decode('utf-8')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('AMIKO_HTTPS', '0').lower() in ('1', 'true', 'yes')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
    minutes=int(os.environ.get('AMIKO_SESSION_TIMEOUT_MIN', '60'))
)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

_cors_env = os.environ.get('AMIKO_CORS_ORIGINS', '').strip()
_cors_origins = [o.strip() for o in _cors_env.split(',') if o.strip()] if _cors_env else '*'
socketio = SocketIO(app, cors_allowed_origins=_cors_origins)


@app.after_request
def apply_security_headers(response):
    """Apply hardened HTTP security headers to all responses."""
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data: https://cdn.jsdelivr.net; "
        "connect-src 'self' ws: wss: https://cdn.jsdelivr.net; "
        "frame-ancestors 'none';"
    )
    if app.config.get('SESSION_COOKIE_SECURE'):
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response


# Load modules on startup
active_modules = load_modules(app)

# Register RBAC & Least Privilege guards
init_auth(app)

# Import routes and handlers so they are registered
from core import routes
from core import handlers
