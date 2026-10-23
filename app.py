import os
from gevent import monkey
monkey.patch_all()

from core import app, socketio

if __name__ == '__main__':
    host = os.environ.get('AMIKO_HOST', '0.0.0.0')
    port = int(os.environ.get('AMIKO_PORT', '5423'))
    debug_mode = os.environ.get('AMIKO_DEBUG', '0').lower() in ('1', 'true', 'yes')

    print(f"AMIKO Server on: http://{host}:{port} (debug={'ON' if debug_mode else 'OFF'})")
    socketio.run(app, debug=debug_mode, use_reloader=False, host=host, port=port)
