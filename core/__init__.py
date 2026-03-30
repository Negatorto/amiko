from flask import Flask
from flask_socketio import SocketIO
from core.plugins import load_modules

app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.config['SECRET_KEY'] = 'secret!' # Change this in production
socketio = SocketIO(app, cors_allowed_origins='*')

# Load modules on startup
active_modules = load_modules(app)

# Import routes and handlers so they are registered
from core import routes
from core import handlers
