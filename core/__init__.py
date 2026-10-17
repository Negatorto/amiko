import os
from flask import Flask
from flask_socketio import SocketIO
from core.plugins import load_modules
from core.config import settings
from core.auth import init_auth

app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.config['SECRET_KEY'] = os.environ.get('AMIKO_SECRET_KEY') or settings._load_or_create_key().decode('utf-8')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

socketio = SocketIO(app, cors_allowed_origins='*')

# Load modules on startup
active_modules = load_modules(app)

# Register RBAC & Least Privilege guards
init_auth(app)

# Import routes and handlers so they are registered
from core import routes
from core import handlers
