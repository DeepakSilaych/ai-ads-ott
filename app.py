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
    os.makedirs(os.path.dirname(original_path), exist_ok=True)
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


class SessionRequired(Exception):
    pass


def _session_setup(data):
    """Every edit MUST run inside an explicitly created session.
    Returns (chain, session). chain=True once the session has edits — the
    session's archived video is restored as the working file."""
    session_id = data.get('session_id')
    if not session_id:
        raise SessionRequired('session_id is required — create one via POST /api/sessions')
    session = next((s for s in _load_sessions() if s['id'] == session_id), None)
    if session is None:
        raise SessionRequired(f'session {session_id} not found')
    if session['filename'] != data['filename']:
        raise SessionRequired('session belongs to a different video')
    chain = len(session['edits']) > 0
    if chain:
        _restore_session(session_id, data['filename'])
    return chain, session_id


@app.errorhandler(SessionRequired)
def _session_required(e):
    return jsonify({'error': str(e)}), 400



def _create_session(filename, note=None):
    import time
    import uuid
    sessions = _load_sessions()
    session = {
        'id': uuid.uuid4().hex[:10],
        'filename': filename,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'edits': [],
    }
    if note:
        session['note'] = note
    session['video'] = f"/static/uploads/sessions/{session['id']}.mp4"
    sessions.insert(0, session)
    _save_sessions(sessions)
    return session


@app.route('/api/sessions', methods=['POST'])
def create_session():
    """Explicitly start an ad-integration session for a video."""
    import time
    import uuid
    filename = request.json.get('filename')
    if not filename:
        return jsonify({'error': 'filename required'}), 400
    return jsonify(_create_session(filename))


def _record_edit(kind, filename, result, chain, session_id=None):
    """Append an edit to its (mandatory, pre-existing) session and refresh
    the session's archived video."""
    import shutil
    import time
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    sessions = _load_sessions()
    session = next((s for s in sessions if s['id'] == session_id), None)
    if session is None:
        raise SessionRequired(f'session {session_id} not found')
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    session['edits'].append({
        'kind': kind, 'at': now,
        'detail': {k: v for k, v in result.items() if k != 'output'}})

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


@app.route('/api/rescan_swaps', methods=['POST'])
def rescan_swaps():
    """Re-run dialogue swap detection targeted at a user-chosen brand."""
    data = request.json
    try:
        video_id = _video_id(data['filename'])
        with open(_result_path(video_id)) as f:
            analysis = json.load(f)
        scene_ctx = '; '.join(i.get('description', '') for i in analysis.get('integrations', []))
        swaps = detector.detect_dialogue_swaps(
            analysis['transcript'], detector._api_key(),
            scene_context=scene_ctx, brand=data.get('brands') or data.get('brand'),
            profile=data.get('audience'))
        frames_dir = os.path.join(detector.FRAMES_DIR, video_id)
        frames = [((int(n[1:5]) - 1) * detector.FRAME_INTERVAL_S, os.path.join(frames_dir, n))
                  for n in sorted(os.listdir(frames_dir))] if os.path.exists(frames_dir) else []
        for swap in swaps:
            try:
                swap['lip_sync'] = detector.check_lip_sync(swap, frames, detector._api_key())
            except Exception:
                swap['lip_sync'] = {'risk': 'unknown'}
        analysis['dialogue_swaps'] = swaps
        with open(_result_path(video_id), 'w') as f:
            json.dump(analysis, f)
        return jsonify({'dialogue_swaps': swaps})
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/place_variants', methods=['POST'])
def place_variants():
    """Multi-brand generation: render the SAME placement once per brand,
    each variant in its own auto-created session."""
    data = dict(request.json)
    brands = data.pop('brands', None) or []
    kind = data.pop('kind', 'visual')  # visual | gap_spot
    if not brands:
        return jsonify({'error': 'brands list required'}), 400
    results = []
    for brand in brands:
        session = _create_session(data['filename'], note=f'variant: {brand}')
        payload = dict(data, brand=brand, session_id=session['id'])
        with app.test_request_context(json=payload):
            resp = place_visual() if kind == 'visual' else place_audio()
        body = resp[0].get_json() if isinstance(resp, tuple) else resp.get_json()
        body['brand'] = brand
        body['session_id'] = session['id']
        results.append(body)
    return jsonify({'variants': results})


@app.route('/api/brands')
def list_brands():
    """Catalog, optionally ranked for ?audience=<profile id> so the picker
    surfaces on-target brands first (each tagged with audience_score)."""
    from brands_catalog import load_catalog
    import audience
    catalog = load_catalog()
    profile = request.args.get('audience')
    if profile:
        catalog = audience.rank(catalog, profile)
    return jsonify(catalog)


@app.route('/api/audiences')
def list_audiences():
    """Preset viewer segments available for targeting."""
    import audience
    return jsonify(audience.PROFILES)


@app.route('/api/recommend', methods=['POST'])
def recommend_brands():
    """Rank brands for a viewer segment, blended with scene fit from this
    video's analysis. Drives 'which product for which demographic'."""
    import audience
    from brands_catalog import load_catalog
    data = request.json or {}
    profile = data.get('audience')
    scene_terms = set()
    if data.get('filename'):
        try:
            with open(_result_path(_video_id(data['filename']))) as f:
                analysis = json.load(f)
            blob = ' '.join(i.get('description', '')
                            for i in analysis.get('integrations', [])).lower()
            blob += ' ' + ' '.join(c for i in analysis.get('integrations', [])
                                   for c in i.get('example_categories', [])).lower()
            scene_terms = {w for w in blob.replace(',', ' ').split() if len(w) > 3}
        except (OSError, json.JSONDecodeError, KeyError):
            pass  # no analysis yet — fall back to pure audience ranking

    out = []
    for b in audience.rank(load_catalog(), profile):
        hits = [s for s in b.get('scene_fit', []) + b.get('products', [])
                if any(t in s.lower() or s.lower() in t for t in scene_terms)]
        scene_score = min(1.0, len(hits) / 2) if scene_terms else 0.5
        b['scene_score'] = round(scene_score, 3)
        b['match_score'] = round(0.6 * b['audience_score'] + 0.4 * scene_score, 3)
        b['why'] = (f"audience fit {b['audience_score']:.2f}"
                    + (f"; matches scene: {', '.join(hits[:3])}" if hits else ""))
        out.append(b)
    out.sort(key=lambda b: -b['match_score'])
    return jsonify({'audience': audience.describe(profile), 'brands': out})


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
            profile=data.get('audience'),
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
        if engine in ('auto', 'seed'):
            try:
                result = dialogue_placer.run_seed(
                    filename=data['filename'], swap=swap,
                    transcript=analysis['transcript'], chain=chain)
            except Exception:
                if engine == 'seed':
                    raise
                result = None
        else:
            result = None
        if result is None and engine in ('auto', 'voicecraft'):
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
        if result is None:
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
            # dense on-demand index of the chosen surface across the WHOLE
            # video (cached in the analysis) — detection keyframes are sparse
            if 'indexed' not in track:
                video_path = os.path.join(
                    app.config['UPLOAD_FOLDER'], 'original', data['filename'])
                track['indexed'] = detector.index_surface(
                    video_path, video_id, track['surface'])
                with open(_result_path(video_id), 'w') as f:
                    json.dump(analysis, f)
            quality = data.get('quality', 'final')
            model = 'gemini_omni_flash' if quality == 'draft' else 'aleph2'
            result = visual_placer.run_track(
                filename=data['filename'], track=track,
                brand_name=data['brand'], chain=chain,
                duration=analysis.get('duration'),
                windows=[list(w) for w in (data.get('windows') or track['indexed']['windows'])] or None,
                model=model)
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
    # threaded: long-running placement renders must not block other requests
    app.run(debug=True, use_reloader=False, threaded=True, port=5050)
