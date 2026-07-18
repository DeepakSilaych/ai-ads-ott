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


RUNS_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'runs')
RUNS_INDEX = os.path.join(RUNS_DIR, 'runs.json')


def _load_runs():
    if os.path.exists(RUNS_INDEX):
        with open(RUNS_INDEX) as f:
            return json.load(f)
    return []


def _record_run(kind, filename, result):
    """Archive the run's output video + metadata; kept until manually deleted."""
    import shutil
    import time
    import uuid
    os.makedirs(RUNS_DIR, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    src = os.path.join(os.path.dirname(__file__), result['output'].lstrip('/'))
    archived = f'{run_id}.mp4'
    shutil.copyfile(src, os.path.join(RUNS_DIR, archived))
    runs = _load_runs()
    runs.insert(0, {
        'id': run_id,
        'kind': kind,
        'filename': filename,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'video': f'/static/uploads/runs/{archived}',
        'detail': {k: v for k, v in result.items() if k != 'output'},
    })
    with open(RUNS_INDEX, 'w') as f:
        json.dump(runs, f, indent=1)
    return run_id


@app.route('/api/runs')
def list_runs():
    return jsonify(_load_runs())


@app.route('/api/runs/<run_id>', methods=['DELETE'])
def delete_run(run_id):
    runs = _load_runs()
    keep = [r for r in runs if r['id'] != run_id]
    if len(keep) == len(runs):
        return jsonify({'error': 'not found'}), 404
    path = os.path.join(RUNS_DIR, f'{run_id}.mp4')
    if os.path.exists(path):
        os.remove(path)
    with open(RUNS_INDEX, 'w') as f:
        json.dump(keep, f, indent=1)
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
        result = audio_placer.run(
            filename=data['filename'],
            chain=bool(data.get('chain')),
            brand_name=data['brand'],
            start_ts=float(data['start_ts']),
            gap_duration=float(data.get('gap_duration', 10)),
            scene_context=data.get('scene_context', ''),
        )
        result['tts_audio'] = '/' + os.path.relpath(result['tts_audio'], os.path.dirname(__file__))
        result['output'] = '/' + os.path.relpath(result['output'], os.path.dirname(__file__))
        result['run_id'] = _record_run('gap_spot', data['filename'], result)
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
        engine = data.get('engine', 'auto')
        if engine in ('auto', 'voicecraft'):
            try:
                result = dialogue_placer.run_voicecraft(
                    filename=data['filename'], swap=swap,
                    transcript=analysis['transcript'], chain=bool(data.get('chain')))
            except Exception:
                if engine == 'voicecraft':
                    raise
                result = dialogue_placer.run(
                    filename=data['filename'], swap=swap,
                    transcript=analysis['transcript'], chain=bool(data.get('chain')))
        else:
            result = dialogue_placer.run(
                filename=data['filename'], swap=swap,
                transcript=analysis['transcript'], chain=bool(data.get('chain')))
        result['output'] = '/' + os.path.relpath(result['output'], os.path.dirname(__file__))
        result['run_id'] = _record_run('dialogue_swap', data['filename'], result)
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
        slot = analysis['visual_slots'][int(data['slot_index'])]
        result = visual_placer.run(
            filename=data['filename'],
            slot=slot,
            visual_slots=analysis['visual_slots'],
            brand_name=data['brand'],
            chain=bool(data.get('chain')),
        )
        result['output'] = '/' + os.path.relpath(result['output'], os.path.dirname(__file__))
        result['run_id'] = _record_run('visual', data['filename'], result)
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
    app.run(debug=True, port=5050)
