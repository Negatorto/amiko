from gevent import monkey
monkey.patch_all()

from core import app, socketio

if __name__ == '__main__':

    print("AMIKO Server on: http://0.0.0.0:5423")
    # Disable reloader to prevent Gevent compatibility issues
    socketio.run(app, debug=True, use_reloader=False, host='0.0.0.0', port=5423)
