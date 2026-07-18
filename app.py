from flask import Flask, render_template, request, jsonify, send_from_directory
import hashlib
import json
import os
import threading

import detector

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file'}), 400
    video = request.files['video']
    if video.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    original_path = os.path.join(app.config['UPLOAD_FOLDER'], 'original', video.filename)
    video.save(original_path)
    return jsonify({'filename': video.filename, 'path': f'/static/uploads/original/{video.filename}'})

@app.route('/api/videos')
def list_videos():
    original_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'original')
    edited_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'edited')

    originals = os.listdir(original_dir) if os.path.exists(original_dir) else []
    edited = os.listdir(edited_dir) if os.path.exists(edited_dir) else []

    videos = []
    for f in originals:
        if f.startswith('.'):
            continue
        vid = _video_id(f)
        videos.append({
            'filename': f,
            'video_id': vid,
            'analyzed': os.path.exists(_result_path(vid)),
            'original': f'/static/uploads/original/{f}',
            'edited': f'/static/uploads/edited/{f}' if f in edited else None,
        })
    return jsonify(videos)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'analysis')
os.makedirs(RESULTS_DIR, exist_ok=True)
_jobs = {}  # video_id -> {"status": ..., "progress": ...}


def _video_id(filename):
    return hashlib.md5(filename.encode()).hexdigest()[:10]


def _result_path(video_id):
    return os.path.join(RESULTS_DIR, f'{video_id}.json')


def _run_detection(filename, video_id):
    path = os.path.join(app.config['UPLOAD_FOLDER'], 'original', filename)
    try:
        def cb(msg):
            _jobs[video_id]['progress'] = msg
        result = detector.detect(path, video_id, progress_cb=cb)
        result['filename'] = filename
        with open(_result_path(video_id), 'w') as f:
            json.dump(result, f)
        _jobs[video_id] = {'status': 'done'}
    except Exception as e:
        _jobs[video_id] = {'status': 'error', 'error': str(e)}


@app.route('/api/detect', methods=['POST'])
def start_detection():
    filename = request.json.get('filename')
    if not filename:
        return jsonify({'error': 'filename required'}), 400
    video_id = _video_id(filename)
    if _jobs.get(video_id, {}).get('status') == 'running':
        return jsonify({'video_id': video_id, 'status': 'running'})
    _jobs[video_id] = {'status': 'running', 'progress': 'starting'}
    threading.Thread(target=_run_detection, args=(filename, video_id), daemon=True).start()
    return jsonify({'video_id': video_id, 'status': 'running'})


SESSIONS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'sessions')
SESSIONS_INDEX = os.path.join(SESSIONS_DIR, 'sessions.json')


def _load_sessions():
    if os.path.exists(SESSIONS_INDEX):
        with open(SESSIONS_INDEX) as f:
            return json.load(f)
    return []


def _save_sessions(sessions):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    with open(SESSIONS_INDEX, 'w') as f:
        json.dump(sessions, f, indent=1)


def _restore_session(session_id, filename):
    """Make an archived session continuable: restore its video as the
    working edited file so the next chained edit stacks on top of it."""
    import shutil
    src = os.path.join(SESSIONS_DIR, f'{session_id}.mp4')
    if not os.path.exists(src):
        raise ValueError(f'session {session_id} has no archived video')
    dst = os.path.join(app.config['UPLOAD_FOLDER'], 'edited', filename)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def _session_setup(data):
    """Common per-request session handling: returns (chain, session_id).
    Passing session_id reopens that session (restores its video, forces chain)."""
    session_id = data.get('session_id')
    chain = bool(data.get('chain')) or bool(session_id)
    if session_id:
        _restore_session(session_id, data['filename'])
    return chain, session_id


def _record_edit(kind, filename, result, chain, session_id=None):
    """Sessions = one stack of chained edits on a video, archived as a unit.
    session_id appends to that session; chain appends to the newest matching
    session; unchained starts a new one."""
    import shutil
    import time
    import uuid
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    sessions = _load_sessions()
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    edit = {'kind': kind, 'at': now,
            'detail': {k: v for k, v in result.items() if k != 'output'}}

    session = None
    if session_id:
        session = next((s for s in sessions if s['id'] == session_id), None)
    elif chain and sessions and sessions[0]['filename'] == filename:
        session = sessions[0]
    if session is None:
        session = {
            'id': uuid.uuid4().hex[:10],
            'filename': filename,
            'created_at': now,
            'edits': [],
        }
        session['video'] = f"/static/uploads/sessions/{session['id']}.mp4"
        sessions.insert(0, session)

    session['edits'].append(edit)
    session['updated_at'] = now
    src = os.path.join(os.path.dirname(__file__), result['output'].lstrip('/'))
    shutil.copyfile(src, os.path.join(SESSIONS_DIR, f"{session['id']}.mp4"))
    _save_sessions(sessions)
    return session['id']


@app.route('/api/sessions')
def list_sessions():
    return jsonify(_load_sessions())


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    sessions = _load_sessions()
    keep = [s for s in sessions if s['id'] != session_id]
    if len(keep) == len(sessions):
        return jsonify({'error': 'not found'}), 404
    path = os.path.join(SESSIONS_DIR, f'{session_id}.mp4')
    if os.path.exists(path):
        os.remove(path)
    _save_sessions(keep)
    return jsonify({'ok': True})


@app.route('/api/brands')
def list_brands():
    from brands_catalog import load_catalog
    return jsonify(load_catalog())


@app.route('/api/place_audio', methods=['POST'])
def place_audio():
    import audio_placer
    data = request.json
    try:
        chain, session_id = _session_setup(data)
        result = audio_placer.run(
            filename=data['filename'],
            chain=chain,
            brand_name=data['brand'],
            start_ts=float(data['start_ts']),
            gap_duration=float(data.get('gap_duration', 10)),
            scene_context=data.get('scene_context', ''),
        )
        result['tts_audio'] = '/' + os.path.relpath(result['tts_audio'], os.path.dirname(__file__))
        result['output'] = '/' + os.path.relpath(result['output'], os.path.dirname(__file__))
        result['session_id'] = _record_edit('gap_spot', data['filename'], result, chain, session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/place_dialogue', methods=['POST'])
def place_dialogue():
    import dialogue_placer
    data = request.json
    try:
        video_id = _video_id(data['filename'])
        with open(_result_path(video_id)) as f:
            analysis = json.load(f)
        swap = analysis['dialogue_swaps'][int(data['swap_index'])]
        chain, session_id = _session_setup(data)
        engine = data.get('engine', 'auto')
        if engine in ('auto', 'voicecraft'):
            try:
                result = dialogue_placer.run_voicecraft(
                    filename=data['filename'], swap=swap,
                    transcript=analysis['transcript'], chain=chain)
            except Exception:
                if engine == 'voicecraft':
                    raise
                result = dialogue_placer.run(
                    filename=data['filename'], swap=swap,
                    transcript=analysis['transcript'], chain=chain)
        else:
            result = dialogue_placer.run(
                filename=data['filename'], swap=swap,
                transcript=analysis['transcript'], chain=chain)
        result['output'] = '/' + os.path.relpath(result['output'], os.path.dirname(__file__))
        result['session_id'] = _record_edit('dialogue_swap', data['filename'], result, chain, session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/place_visual', methods=['POST'])
def place_visual():
    import visual_placer
    data = request.json
    try:
        video_id = _video_id(data['filename'])
        with open(_result_path(video_id)) as f:
            analysis = json.load(f)
        chain, session_id = _session_setup(data)
        tracks = analysis.get('visual_tracks') or []
        track = None
        if data.get('track_id') is not None:
            track = tracks[int(data['track_id'])]
        elif tracks:
            slot = analysis['visual_slots'][int(data['slot_index'])]
            tid = slot.get('track_id')
            track = tracks[tid] if tid is not None else None
        if track:
            result = visual_placer.run_track(
                filename=data['filename'], track=track,
                brand_name=data['brand'], chain=chain,
                duration=analysis.get('duration'))
        else:
            slot = analysis['visual_slots'][int(data['slot_index'])]
            result = visual_placer.run(
                filename=data['filename'], slot=slot,
                visual_slots=analysis['visual_slots'],
                brand_name=data['brand'], chain=chain)
        result['output'] = '/' + os.path.relpath(result['output'], os.path.dirname(__file__))
        result['session_id'] = _record_edit('visual', data['filename'], result, chain, session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/detect/<video_id>')
def detection_status(video_id):
    job = _jobs.get(video_id)
    if os.path.exists(_result_path(video_id)) and (not job or job.get('status') == 'done'):
        with open(_result_path(video_id)) as f:
            return jsonify({'status': 'done', 'result': json.load(f)})
    if not job:
        return jsonify({'status': 'none'})
    return jsonify(job)


if __name__ == '__main__':
    # reloader off: code edits during long-running placement requests were
    # killing in-flight jobs; restart manually after backend changes
    app.run(debug=True, use_reloader=False, port=5050)
