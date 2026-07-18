"""User-directed ad placement from a plain-English request.

The detection pass decides where an ad *could* go; a directive lets the user
say where it *will* go. Flow:

    parse()   free text ------------------> structured intent
    resolve() intent ---------------------> an actionable slot

resolve() tries the user's exact spot first (scanning frames around their
timestamp for the surface they described). When that spot yields nothing
usable it does not silently relocate the ad — it returns the nearest
detected alternatives and lets the caller choose.
"""
import base64
import json
import os
import re

import requests

from prompts import DIRECTIVE_PARSE_PROMPT, SURFACE_INDEX_PROMPT
from brands_catalog import catalog_for_prompt

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VISION_MODEL = "z-ai/glm-4.6v"

# How far around the requested timestamp we look for the described surface.
SEARCH_PAD_S = 3.0
SEARCH_STEP_S = 0.5

# bbox sanity, as a fraction of frame area (coords are normalised 0-1000).
# A box covering most of the frame means the VLM answered "yes, somewhere"
# instead of localising — compositing onto that would paint the whole shot.
MAX_AREA_FRAC = 0.70
MIN_AREA_FRAC = 0.01
_FRAME_AREA = 1000.0 * 1000.0


def _fmt_slots(slots):
    return "\n".join(
        f"- t={s.get('timestamp')}s {s.get('surface')} (score {s.get('score')})"
        for s in (slots or [])[:20]) or "(none detected)"


def _fmt_gaps(gaps):
    return "\n".join(
        f"- {g['start_ts']}-{g['end_ts']}s ({g['duration']}s)"
        for g in (gaps or [])[:20]) or "(none detected)"


def _fmt_transcript(transcript):
    return "\n".join(
        f"{s['text']} [{s['start_ts']}-{s['end_ts']}]"
        for s in (transcript or [])[:120]) or "(no dialogue)"


def parse(request_text, analysis, api_key):
    """Free-text request -> structured intent dict."""
    prompt = (DIRECTIVE_PARSE_PROMPT
              .replace("{request}", request_text)
              .replace("{duration}", str(round(analysis.get("duration", 0))))
              .replace("{visual_slots}", _fmt_slots(analysis.get("visual_slots")))
              .replace("{audio_gaps}", _fmt_gaps(analysis.get("audio_slots")))
              .replace("{transcript}", _fmt_transcript(analysis.get("transcript")))
              .replace("{catalog}", catalog_for_prompt()))

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": VISION_MODEL,
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 900},
        timeout=120)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("could not parse a directive from that request")
    intent = json.loads(match.group(0))
    if intent.get("kind") not in ("visual", "dialogue", "audio"):
        raise ValueError(f"unsupported directive kind: {intent.get('kind')!r}")
    return intent


def _frames_near(frames_dir, start_ts, end_ts):
    """Indexed frames whose timestamp falls in the padded search window."""
    if not os.path.isdir(frames_dir):
        return []
    out = []
    for name in sorted(os.listdir(frames_dir)):
        m = re.match(r"[a-z](\d{4})\.jpg$", name)
        if not m:
            continue
        ts = (int(m.group(1)) - 1) * SEARCH_STEP_S
        if start_ts <= ts <= end_ts:
            out.append((ts, os.path.join(frames_dir, name)))
    return out


def locate_surface(video_path, video_id, target, start_ts, end_ts, api_key,
                   frames_root=None):
    """Scan frames in [start_ts, end_ts] for the user's described surface.

    Returns the best (largest, fully-visible) hit as a slot dict, or None.
    Extracts its own frames at SEARCH_STEP_S so the window is dense even
    though detection keyframes are sparse."""
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    frames_root = frames_root or os.path.join(
        os.path.dirname(__file__), "static", "uploads", "frames")
    out_dir = os.path.join(frames_root, f"{video_id}_directive")
    os.makedirs(out_dir, exist_ok=True)
    for stale in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, stale))

    dur = max(SEARCH_STEP_S, end_ts - start_ts)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(max(0, start_ts)), "-t", str(dur),
         "-i", video_path, "-vf", f"fps=1/{SEARCH_STEP_S},scale=640:-2",
         "-q:v", "6", os.path.join(out_dir, "d%04d.jpg")],
        capture_output=True, check=True)

    names = sorted(n for n in os.listdir(out_dir) if n.endswith(".jpg"))
    frames = [(max(0, start_ts) + i * SEARCH_STEP_S, os.path.join(out_dir, n))
              for i, n in enumerate(names)]
    if not frames:
        return None

    prompt = SURFACE_INDEX_PROMPT.replace("{surface}", target)

    def check(item):
        ts, fpath = item
        with open(fpath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": VISION_MODEL,
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url",
                           "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                          {"type": "text", "text": prompt}]}],
                      "max_tokens": 400},
                timeout=120)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", text, re.DOTALL)
            d = json.loads(m.group(0)) if m else {}
        except Exception:
            d = {}
        return ts, d

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, frames))

    want_ts = (start_ts + end_ts) / 2
    best, best_rank = None, 0
    for ts, d in results:
        bbox = d.get("bbox")
        if not d.get("visible") or not bbox or len(bbox) != 4:
            continue
        frac = (max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])) / _FRAME_AREA
        # a near-full-frame or hairline box is a non-answer, not a surface
        if not (MIN_AREA_FRAC <= frac <= MAX_AREA_FRAC):
            continue
        rank = frac
        if d.get("fully_visible"):
            rank *= 1.5
        # The user picked this moment deliberately, so drift is expensive:
        # a squared penalty keeps a merely-bigger surface further away from
        # hijacking a decent one at the requested time.
        rank /= (1.0 + abs(ts - want_ts)) ** 2
        if rank > best_rank:
            best, best_rank = {
                "timestamp": round(ts, 2),
                "surface": target,
                "bbox": bbox,
                "score": 8,
                "fully_visible": bool(d.get("fully_visible")),
                "user_directed": True,
            }, rank
    return best


def nearest(items, ts, key, limit=3):
    """The `limit` entries closest in time to ts — the fallback list."""
    if ts is None:
        return list(items or [])[:limit]
    return sorted((items or []), key=lambda x: abs(x.get(key, 0) - ts))[:limit]
