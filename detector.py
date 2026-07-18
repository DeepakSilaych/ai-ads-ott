"""Feature 1: Ad placement opportunity detector (visual + audio)."""
import base64
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

import requests

from prompts import (VISUAL_PLACEMENT_PROMPT, SCENE_INTEGRATION_PROMPT,
                     DIALOGUE_SWAP_PROMPT, LIP_SYNC_CHECK_PROMPT,
                     SURFACE_INDEX_PROMPT)
from brands_catalog import catalog_for_prompt

BASE_DIR = os.path.dirname(__file__)
FRAMES_DIR = os.path.join(BASE_DIR, "static", "uploads", "frames")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = "z-ai/glm-4.6v"

FRAME_INTERVAL_S = 2.0  # sample one frame every N seconds

VISION_PROMPT = VISUAL_PLACEMENT_PROMPT


def _api_key():
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            if line.startswith("API="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("API key not found in .env")


def video_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def extract_frames(video_path, video_id):
    """Extract frames every FRAME_INTERVAL_S seconds. Returns [(timestamp, filepath)]."""
    out_dir = os.path.join(FRAMES_DIR, video_id)
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vf", f"fps=1/{FRAME_INTERVAL_S},scale=768:-2",
         "-q:v", "5",
         os.path.join(out_dir, "f%04d.jpg")],
        capture_output=True, check=True,
    )
    frames = []
    for name in sorted(os.listdir(out_dir)):
        idx = int(name[1:5])
        ts = (idx - 1) * FRAME_INTERVAL_S
        frames.append((ts, os.path.join(out_dir, name)))
    return frames


def analyze_frame(frame_path, api_key):
    """Ask the VLM for ad-placement surfaces in one frame."""
    with open(frame_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
            "max_tokens": 1500,
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return []


def analyze_scene(frames, api_key, max_frames=24, transcript=None):
    """Send the frame sequence in ONE multi-image call for scene-level
    integration opportunities (product interactions, prop placements...)."""
    step = max(1, len(frames) // max_frames)
    sampled = frames[::step][:max_frames]

    content = []
    for ts, fpath in sampled:
        with open(fpath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "text", "text": f"[t={ts:.0f}s]"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    prompt = SCENE_INTEGRATION_PROMPT.replace("{interval}", str(FRAME_INTERVAL_S))
    if transcript:
        lines = "\n".join(f"[{s['start_ts']:.0f}-{s['end_ts']:.0f}s] {s['text']}" for s in transcript)
        prompt += "\n\n## Dialogue transcript (use it: spoken product/brand/desire mentions are strong integration signals)\n" + lines
    content.append({"type": "text", "text": prompt})

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 2000,
        },
        timeout=300,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        out = json.loads(match.group(0))
        return [o for o in out if isinstance(o, dict) and "kind" in o]
    except json.JSONDecodeError:
        return []


_whisper_model = None


def transcribe(video_path):
    """Transcribe the audio track with segment timestamps (local faster-whisper)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = _whisper_model.transcribe(video_path, vad_filter=True, word_timestamps=True)
    out = []
    for s in segments:
        out.append({
            "start_ts": round(s.start, 2),
            "end_ts": round(s.end, 2),
            "text": s.text.strip(),
            "words": [
                {"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)}
                for w in (s.words or [])
            ],
        })
    return out


def detect_dialogue_swaps(transcript, api_key, scene_context="", samples=5):
    """Find minimal brand-mention dialogue edits using the brands catalog.

    Samples the LLM several times and merges deduped results — single runs
    are flaky about proposing vs. withholding borderline swaps."""
    if not transcript:
        return []
    if samples > 1:
        merged = {}
        with ThreadPoolExecutor(max_workers=samples) as pool:
            runs = pool.map(
                lambda _: detect_dialogue_swaps(transcript, api_key, scene_context, samples=1),
                range(samples))
        for run in runs:
            for s in run:
                k = (s["brand"], round(s.get("start_ts", 0)))
                if k not in merged or s.get("score", 0) > merged[k].get("score", 0):
                    merged[k] = s
        return sorted(merged.values(), key=lambda s: -s.get("score", 0))
    lines = []
    for seg in transcript:
        words = " ".join(f"{w['w']}[{w['s']}-{w['e']}]" for w in seg.get("words", []))
        lines.append(words or f"{seg['text']} [{seg['start_ts']}-{seg['end_ts']}]")
    prompt = (DIALOGUE_SWAP_PROMPT
              .replace("{catalog}", catalog_for_prompt())
              .replace("{scene_context}", scene_context or "unknown"))
    prompt += "\n\n## Transcript (word[start-end] format)\n" + "\n".join(lines)

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1500,
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        out = json.loads(match.group(0))
        return [o for o in out if isinstance(o, dict) and "brand" in o]
    except json.JSONDecodeError:
        return []


def check_lip_sync(swap, frames, api_key, pad_s=0.4):
    """Look at the actual frames during a swap window: is a speaking mouth visible?"""
    lo, hi = swap["start_ts"] - pad_s, swap["end_ts"] + pad_s
    window = [(ts, fp) for ts, fp in frames if lo <= ts <= hi]
    if not window:
        # no sampled frame in window; take nearest frame
        window = [min(frames, key=lambda f: abs(f[0] - swap["start_ts"]))]

    content = []
    for ts, fp in window[:4]:
        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "text", "text": f"[t={ts:.0f}s]"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": LIP_SYNC_CHECK_PROMPT})

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": VISION_MODEL,
              "messages": [{"role": "user", "content": content}],
              "max_tokens": 500},
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"mouth_visible": None, "risk": "unknown", "note": ""}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"mouth_visible": None, "risk": "unknown", "note": ""}


def dialogue_gaps(transcript, duration, min_gap_s=2.0):
    """Gaps between speech segments — better audio-ad slots than raw silence."""
    gaps = []
    prev_end = 0.0
    boundaries = [(s["start_ts"], s["end_ts"]) for s in transcript] + [(duration, duration)]
    for start, end in boundaries:
        if start - prev_end >= min_gap_s:
            gaps.append({
                "type": "audio",
                "kind": "dialogue_gap",
                "start_ts": round(prev_end, 2),
                "end_ts": round(start, 2),
                "duration": round(start - prev_end, 2),
            })
        prev_end = max(prev_end, end)
    return gaps


def index_surface(video_path, video_id, surface, step_s=0.5):
    """Densely index the WHOLE video for one chosen surface: sample every
    step_s, ask the VLM only 'is it visible (bbox)?', and merge consecutive
    positives into visibility windows. Run on demand when a surface is
    picked for editing — cheap (~2 calls/sec of video) and far more accurate
    than the sparse detection keyframes."""
    api_key = _api_key()
    out_dir = os.path.join(FRAMES_DIR, f"{video_id}_index")
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vf", f"fps=1/{step_s},scale=640:-2", "-q:v", "6",
         os.path.join(out_dir, "i%04d.jpg")],
        capture_output=True, check=True)
    frames = [(int(n[1:5]) - 1) * step_s for n in sorted(os.listdir(out_dir))]
    paths = [os.path.join(out_dir, n) for n in sorted(os.listdir(out_dir))]

    prompt = SURFACE_INDEX_PROMPT.replace("{surface}", surface)

    def check(args):
        ts, fpath = args
        with open(fpath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VISION_MODEL,
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                          {"type": "text", "text": prompt}]}],
                      "max_tokens": 400},
                timeout=120)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", text, re.DOTALL)
            d = json.loads(m.group(0)) if m else {}
        except Exception:
            d = {}
        return ts, bool(d.get("visible")), d.get("bbox")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = sorted(pool.map(check, zip(frames, paths)))

    windows = []
    samples = []
    for ts, visible, bbox in results:
        samples.append({"ts": ts, "visible": visible, "bbox": bbox})
        if visible:
            if windows and ts - windows[-1][1] <= step_s + 0.01:
                windows[-1][1] = ts + step_s
            else:
                windows.append([ts, ts + step_s])
    return {"surface": surface, "step_s": step_s,
            "windows": [[round(a, 2), round(b, 2)] for a, b in windows],
            "samples": samples}


def build_visual_tracks(visual_slots, max_gap_s=6.0):
    """Link detections of the same physical surface across frames into
    tracks. Same surface = fuzzy label match + temporal proximity (bbox IoU
    is useless under camera motion). Editing a track edits every occurrence."""
    def same(a, b):
        a, b = a.lower(), b.lower()
        return a in b or b in a or a.replace("refrigerator", "fridge") == b.replace("refrigerator", "fridge")

    tracks = []
    for idx, s in enumerate(sorted(visual_slots, key=lambda x: x["timestamp"])):
        home = None
        for t in tracks:
            if same(t["surface"], s["surface"]) and s["timestamp"] - t["end_ts"] <= max_gap_s:
                home = t
                break
        if home is None:
            home = {
                "track_id": len(tracks),
                "surface": s["surface"],
                "start_ts": s["timestamp"],
                "end_ts": s["timestamp"],
                "score": s["score"],
                "keyframes": [],
            }
            tracks.append(home)
        home["end_ts"] = s["timestamp"]
        home["score"] = max(home["score"], s["score"])
        home["keyframes"].append({"ts": s["timestamp"], "bbox": s["bbox"]})
        s["track_id"] = home["track_id"]
    return tracks


def detect_audio_slots(video_path, min_gap_s=0.8, silence_db=-30):
    """Find low-energy gaps in the audio track using ffmpeg silencedetect."""
    out = subprocess.run(
        ["ffmpeg", "-i", video_path, "-af",
         f"silencedetect=noise={silence_db}dB:d={min_gap_s}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    slots = []
    start = None
    for line in out.stderr.splitlines():
        if "silence_start" in line:
            start = float(line.split("silence_start:")[1].strip())
        elif "silence_end" in line and start is not None:
            parts = line.split("silence_end:")[1].strip().split("|")
            end = float(parts[0].strip())
            slots.append({
                "type": "audio",
                "kind": "silence_gap",
                "start_ts": round(start, 2),
                "end_ts": round(end, 2),
                "duration": round(end - start, 2),
            })
            start = None
    return slots


def detect(video_path, video_id, progress_cb=None):
    """Full detection pass. Returns dict with visual + audio slots."""
    api_key = _api_key()
    duration = video_duration(video_path)

    frames = extract_frames(video_path, video_id)
    visual_slots = []
    done_count = 0

    def _worker(item):
        ts, fpath = item
        try:
            return ts, fpath, analyze_frame(fpath, api_key)
        except Exception:
            return ts, fpath, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        for ts, fpath, detections in pool.map(_worker, frames):
            done_count += 1
            if progress_cb:
                progress_cb(f"frame {done_count}/{len(frames)}")
            rel = os.path.relpath(fpath, BASE_DIR)
            for d in detections:
                if not isinstance(d, dict) or "bbox" not in d:
                    continue
                visual_slots.append({
                    "type": "visual",
                    "timestamp": ts,
                    "surface": d.get("surface", "unknown"),
                    "bbox": d["bbox"],
                    "score": d.get("score", 0),
                    "reason": d.get("reason", ""),
                    "frame": "/" + rel.replace(os.sep, "/"),
                })

    if progress_cb:
        progress_cb("transcribing")
    try:
        transcript = transcribe(video_path)
    except Exception:
        transcript = []

    if progress_cb:
        progress_cb("scene analysis")
    try:
        integrations = analyze_scene(frames, api_key, transcript=transcript)
    except Exception:
        integrations = []

    if progress_cb:
        progress_cb("dialogue swaps")
    try:
        scene_ctx = "; ".join(i.get("description", "") for i in integrations) or ""
        dialogue_swaps = detect_dialogue_swaps(transcript, api_key, scene_context=scene_ctx)
        for swap in dialogue_swaps:
            if progress_cb:
                progress_cb("lip-sync check")
            try:
                swap["lip_sync"] = check_lip_sync(swap, frames, api_key)
            except Exception:
                swap["lip_sync"] = {"mouth_visible": None, "risk": "unknown", "note": ""}
    except Exception:
        dialogue_swaps = []

    audio_slots = detect_audio_slots(video_path)
    if transcript:
        audio_slots = dialogue_gaps(transcript, duration) or audio_slots

    visual_tracks = build_visual_tracks(visual_slots)

    return {
        "duration": duration,
        "visual_tracks": visual_tracks,
        "visual_slots": visual_slots,
        "audio_slots": audio_slots,
        "transcript": transcript,
        "integrations": integrations,
        "dialogue_swaps": dialogue_swaps,
    }
