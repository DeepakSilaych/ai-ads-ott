"""Stage 3: place audio branding into a detected gap.

Pipeline: LLM writes a diegetic ad line -> edge-tts synthesizes it ->
ffmpeg ducks the original mix in the gap and overlays the spot ->
remuxed video written to static/uploads/edited/.
"""
import asyncio
import json
import os
import re
import subprocess

import requests

from prompts import AUDIO_AD_SCRIPT_PROMPT
from brands_catalog import load_catalog

BASE_DIR = os.path.dirname(__file__)
EDITED_DIR = os.path.join(BASE_DIR, "static", "uploads", "edited")
TTS_DIR = os.path.join(BASE_DIR, "static", "uploads", "tts")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TEXT_MODEL = "z-ai/glm-4.6v"

# PA / announcer-ish neural voice
TTS_VOICE = "en-US-GuyNeural"


def _api_key():
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            if line.startswith("API="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("API key not found in .env")


def _brand_entry(brand_name):
    for b in load_catalog():
        if b["name"].lower() == brand_name.lower():
            return b
    raise ValueError(f"brand {brand_name!r} not in catalog")


def write_ad_script(brand_name, scene_context, duration_s):
    """LLM writes the spoken line for the audio spot."""
    b = _brand_entry(brand_name)
    max_words = max(6, int(duration_s * 2.3))
    prompt = (AUDIO_AD_SCRIPT_PROMPT
              .replace("{brand}", json.dumps(b))
              .replace("{scene_context}", scene_context or "unknown")
              .replace("{duration}", f"{duration_s:.0f}")
              .replace("{max_words}", str(max_words)))
    for _attempt in range(3):
        resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={"model": TEXT_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 2500,
                  "reasoning": {"enabled": False}},
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"].get("content") or ""
        lines = [l.strip() for l in text.strip().strip('"').splitlines() if l.strip()]
        if lines:
            return lines[0]
    raise RuntimeError("LLM returned no ad script after 3 attempts")


def synthesize(text, out_path, voice=TTS_VOICE):
    """edge-tts text -> mp3."""
    import edge_tts

    async def run():
        tts = edge_tts.Communicate(text, voice)
        await tts.save(out_path)

    asyncio.run(run())
    return out_path


def place_audio_ad(video_path, out_path, ad_audio_path, start_ts, duck_db=-14):
    """Mix the ad spot into the video's audio at start_ts, ducking the original."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", ad_audio_path],
        capture_output=True, text=True, check=True)
    ad_dur = float(probe.stdout.strip())
    end_ts = start_ts + ad_dur

    duck = f"volume=enable='between(t,{start_ts},{end_ts})':volume={10 ** (duck_db / 20):.3f}"
    filter_complex = (
        f"[0:a]{duck}[ducked];"
        f"[1:a]adelay={int(start_ts * 1000)}|{int(start_ts * 1000)},volume=1.6[ad];"
        f"[ducked][ad]amix=inputs=2:duration=first:normalize=0[out]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", ad_audio_path,
         "-filter_complex", filter_complex,
         "-map", "0:v", "-map", "[out]",
         "-c:v", "copy", "-c:a", "aac", out_path],
        capture_output=True, check=True)
    return {"start_ts": start_ts, "end_ts": round(end_ts, 2), "duration": round(ad_dur, 2)}


def run(filename, brand_name, start_ts, gap_duration, scene_context="", chain=False):
    """Full stage-3 pass. Returns metadata dict.

    chain=True uses the previously edited output as the source, so multiple
    ad placements can stack in one video."""
    os.makedirs(EDITED_DIR, exist_ok=True)
    os.makedirs(TTS_DIR, exist_ok=True)

    video_path = os.path.join(BASE_DIR, "static", "uploads", "original", filename)
    out_path = os.path.join(EDITED_DIR, filename)
    if chain and os.path.exists(out_path):
        prev = out_path + ".prev.mp4"
        os.replace(out_path, prev)
        video_path = prev

    script = write_ad_script(brand_name, scene_context, min(gap_duration - 1, 8))
    safe = re.sub(r"[^a-z0-9]+", "_", brand_name.lower())
    tts_path = os.path.join(TTS_DIR, f"{safe}_{int(start_ts)}.mp3")
    synthesize(script, tts_path)

    info = place_audio_ad(video_path, out_path, tts_path, start_ts + 0.5)
    return {
        "script": script,
        "brand": brand_name,
        "tts_audio": tts_path,
        "output": out_path,
        **info,
    }
