import os
import re
import sys
import time
import yaml
import threading
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response, stream_with_context

app = Flask(__name__)

# Paths
MTDP_DIR    = os.environ.get('MTDP_DIR', '/app')
CONFIG_PATH = os.environ.get('CONFIG_PATH', '/config/config.yml')
LOGS_BASE   = os.path.join(MTDP_DIR, 'Logs')
MOVIES_SCRIPT = os.path.join(MTDP_DIR, 'Modules', 'Movies.py')
TV_SCRIPT     = os.path.join(MTDP_DIR, 'Modules', 'TV.py')

ANSI_RE = re.compile(r'\033\[[0-9;]*[mKJ]')

def strip_ansi(text):
    return ANSI_RE.sub('', text)

# ── Run state ──────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_state = {
    'active': False,
    'process': None,
    'type': None,
    'started_at': None,
    'trigger_time': None,
}

# ── Log helpers ────────────────────────────────────────────────────────────────

def get_log_files():
    """Return all log files sorted newest-first."""
    files = []
    for subdir in ['Movies', 'TV Shows']:
        log_dir = os.path.join(LOGS_BASE, subdir)
        if not os.path.isdir(log_dir):
            continue
        for name in os.listdir(log_dir):
            if name.startswith('log_') and name.endswith('.txt'):
                path = os.path.join(log_dir, name)
                try:
                    mtime = os.path.getmtime(path)
                    size  = os.path.getsize(path)
                except OSError:
                    continue
                files.append({'path': path, 'name': name, 'type': subdir,
                               'mtime': mtime, 'size': size})
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return files


def parse_log(path):
    """Parse a log file and return a stats dict."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read()
    except OSError:
        return {}

    lines = [strip_ansi(l) for l in raw.split('\n')]

    result = {
        'downloaded': [], 'missing': [], 'errors': [], 'skipped': [],
        'runtime': None, 'total': 0, 'checked': 0,
        'libraries': [], 'completed': False,
    }

    section = None
    progress_re = re.compile(r'Checking (?:movie|show) (\d+)/(\d+):')
    library_re  = re.compile(r'Checking your (.+?) library for missing trailers')

    section_headers = {
        'skipped (Matching Genre):': 'skipped',
        'missing trailers:':         'missing',
        'successfully downloaded trailers:': 'downloaded',
        'failed trailer downloads:': 'errors',
    }

    for line in lines:
        s = line.strip()
        if not s:
            continue

        m = library_re.search(s)
        if m:
            lib = m.group(1).strip()
            if lib not in result['libraries']:
                result['libraries'].append(lib)
            section = None
            continue

        m = progress_re.search(s)
        if m:
            n, total = int(m.group(1)), int(m.group(2))
            result['checked'] = n
            if total > result['total']:
                result['total'] = total
            section = None
            continue

        if 'Run Time:' in s:
            rt = s.split('Run Time:')[-1].strip()
            if rt:
                result['runtime'] = rt
            result['completed'] = True
            section = None
            continue

        if 'No missing trailers!' in s:
            result['completed'] = True
            section = None
            continue

        # Section header?
        new_section = None
        for pattern, sec in section_headers.items():
            if pattern in s:
                new_section = sec
                break
        if new_section:
            section = new_section
            continue

        # Section item
        if section and s:
            result[section].append(s)

    return result


# ── API ────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def api_status():
    with _lock:
        return jsonify({
            'running':    _state['active'],
            'type':       _state['type'],
            'started_at': _state['started_at'],
        })


@app.route('/api/runs')
def api_runs():
    files = get_log_files()[:60]
    runs = []
    for f in files:
        stats = parse_log(f['path'])
        runs.append({
            'filename': f['name'],
            'type':     f['type'],
            'date':     datetime.fromtimestamp(f['mtime']).strftime('%Y-%m-%d %H:%M'),
            'mtime':    f['mtime'],
            'stats':    stats,
        })
    return jsonify(runs)


@app.route('/api/runs/latest')
def api_runs_latest():
    files = get_log_files()
    if not files:
        return jsonify(None)
    f = files[0]
    return jsonify({
        'filename': f['name'],
        'type':     f['type'],
        'date':     datetime.fromtimestamp(f['mtime']).strftime('%Y-%m-%d %H:%M'),
        'mtime':    f['mtime'],
        'stats':    parse_log(f['path']),
    })


@app.route('/api/log')
def api_log_content():
    log_type = request.args.get('type', '')
    filename  = request.args.get('file', '')
    if log_type not in ('Movies', 'TV Shows'):
        return jsonify({'error': 'Invalid type'}), 400
    if '/' in filename or '..' in filename or not filename.endswith('.txt'):
        return jsonify({'error': 'Invalid filename'}), 400
    path = os.path.join(LOGS_BASE, log_type, filename)
    if not os.path.isfile(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        content = strip_ansi(fh.read())
    return jsonify({'content': content})


@app.route('/api/log/stream')
def api_log_stream():
    """SSE: tail the newest log file created at or after `since`."""
    since = float(request.args.get('since', 0))

    def generate():
        # Wait up to 15 s for a log file matching `since`
        path     = None
        deadline = time.time() + 15
        while time.time() < deadline:
            for f in get_log_files():
                if f['mtime'] >= since - 2:
                    path = f['path']
                    break
            if path:
                break
            yield ': waiting\n\n'
            time.sleep(1)

        if not path:
            yield 'data: [No log file found]\n\n'
            return

        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                while True:
                    line = fh.readline()
                    if line:
                        clean = strip_ansi(line.rstrip())
                        if clean:
                            yield f'data: {clean}\n\n'
                    else:
                        with _lock:
                            still_running = _state['active']
                        if not still_running:
                            yield 'event: done\ndata: complete\n\n'
                            return
                        time.sleep(0.3)
        except Exception as e:
            yield f'data: [Stream error: {e}]\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/trigger', methods=['POST'])
def api_trigger():
    data     = request.json or {}
    run_type = str(data.get('type', '3'))
    if run_type not in ('1', '2', '3'):
        return jsonify({'error': 'Invalid type'}), 400

    with _lock:
        if _state['active']:
            return jsonify({'error': 'A run is already in progress'}), 409

    trigger_time = time.time()
    type_labels  = {'1': 'Movies', '2': 'TV Shows', '3': 'Both'}

    def do_run():
        scripts = []
        if run_type in ('1', '3'):
            scripts.append(MOVIES_SCRIPT)
        if run_type in ('2', '3'):
            scripts.append(TV_SCRIPT)

        env = os.environ.copy()
        env['IS_DOCKER']        = 'true'
        env['PYTHONUNBUFFERED'] = '1'

        with _lock:
            _state['active']       = True
            _state['type']         = type_labels[run_type]
            _state['started_at']   = datetime.now().isoformat()
            _state['trigger_time'] = trigger_time

        try:
            for script in scripts:
                if not os.path.isfile(script):
                    print(f'Script not found: {script}', flush=True)
                    continue
                proc = subprocess.Popen(
                    [sys.executable, script],
                    env=env,
                    cwd=MTDP_DIR,
                )
                with _lock:
                    _state['process'] = proc
                proc.wait()
        except Exception as e:
            print(f'Run error: {e}', flush=True)
        finally:
            with _lock:
                _state['active']  = False
                _state['process'] = None

    threading.Thread(target=do_run, daemon=True).start()
    return jsonify({'status': 'started', 'type': run_type, 'trigger_time': trigger_time})


@app.route('/api/trigger/stop', methods=['POST'])
def api_trigger_stop():
    with _lock:
        if not _state['active']:
            return jsonify({'error': 'No run in progress'}), 409
        proc = _state['process']
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    return jsonify({'status': 'stopping'})


@app.route('/api/config', methods=['GET'])
def api_config_get():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return jsonify({'content': f.read()})
    except OSError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
def api_config_save():
    content = (request.json or {}).get('content', '')
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return jsonify({'error': f'Invalid YAML: {e}'}), 400
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'status': 'saved'})
    except OSError as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7879, debug=False, threaded=True)
