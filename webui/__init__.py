"""MTDP Web UI - Flask-based interface."""

import logging
import os
import re
import sys
import threading

_app = None
_scheduler_state = None
_config_path = None
_log_path = None
_trailer_tracker = None
_version = None


class _TeeWriter:
    """Write to both the original stream and a log file.

    Werkzeug API access-log lines are written to the log file but
    suppressed from the console (Docker stdout) to reduce noise.
    Log lines are prefixed with timestamps for the web UI.
    """

    _API_LOG_RE = re.compile(
        r'\d+\.\d+\.\d+\.\d+\s+-\s+-\s+\[.*?\]\s+"'
        r'(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+/api/'
    )
    _ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

    def __init__(self, original, log_path):
        self._original = original
        self._log_path = log_path
        self._lock = threading.Lock()
        self._at_line_start = True

    def write(self, data):
        # Always persist to log file
        if data:
            from datetime import datetime
            cleaned = self._ANSI_RE.sub('', data)
            with self._lock:
                try:
                    with open(self._log_path, 'a', encoding='utf-8') as f:
                        # Add timestamps at the start of each new line
                        lines = cleaned.split('\n')
                        for i, line in enumerate(lines):
                            if i > 0:
                                f.write('\n')
                                self._at_line_start = True
                            if line:
                                if self._at_line_start:
                                    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    f.write(f'[{ts}] {line}')
                                else:
                                    f.write(line)
                                self._at_line_start = False
                        if cleaned.endswith('\n'):
                            self._at_line_start = True
                except Exception:
                    pass
            # Suppress noisy API polling lines from console
            if self._API_LOG_RE.search(data):
                return
        self._original.write(data)

    def flush(self):
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


def start_webui(scheduler_state=None, config_path=None, trailer_tracker=None,
                host="0.0.0.0", port=2121, version=None):
    """Start the Flask web UI in a daemon thread."""
    global _app, _scheduler_state, _config_path, _log_path, _trailer_tracker, _version

    from flask import Flask

    _scheduler_state = scheduler_state
    _trailer_tracker = trailer_tracker
    _version = version

    if config_path is None:
        if os.environ.get('IS_DOCKER', 'false').lower() == 'true':
            config_path = '/config/config.yml'
        else:
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.yml')

    _config_path = config_path

    # Set up log file capture (tee stdout/stderr to a log file)
    if os.environ.get('IS_DOCKER', 'false').lower() == 'true':
        log_dir = '/app/logs'
    else:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    _log_path = os.path.join(log_dir, 'mtdp.log')

    # Truncate log at start of each session
    try:
        with open(_log_path, 'w', encoding='utf-8') as f:
            pass
    except Exception:
        pass

    sys.stdout = _TeeWriter(sys.stdout, _log_path)
    sys.stderr = _TeeWriter(sys.stderr, _log_path)

    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    _app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    # ── Authentication setup ──────────────────────────────────────────
    from . import routes
    from .auth import get_or_create_secret_key, register_auth_routes

    def _load_auth_config():
        import yaml
        try:
            with open(_config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    def _save_auth_config(config):
        routes._save_yaml(_config_path, config)

    # Set Flask secret key (for session cookies) — generated on first run
    auth_config = _load_auth_config()
    _app.secret_key = get_or_create_secret_key(auth_config, _save_auth_config)
    _app.config['SESSION_COOKIE_HTTPONLY'] = True
    _app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Register auth routes + before_request enforcement before other routes
    register_auth_routes(_app, _load_auth_config, _save_auth_config)

    routes.register_routes(_app)

    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    def _run():
        _app.run(host=host, port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=_run, daemon=True, name="webui")
    t.start()
    print("WebUI started")
