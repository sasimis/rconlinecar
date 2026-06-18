"""Launcher for app.py on an available port (default 5002 since port 5000 is taken by HQueue)."""
import os
import sys

# Force port to 5002
os.environ['PORT'] = '5002'

# Now import and run the actual app
from app import app, socketio

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    print(f"Starting server on http://0.0.0.0:{port}/")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
