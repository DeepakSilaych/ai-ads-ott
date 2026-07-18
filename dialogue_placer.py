"""Seamless dialogue branding: overlay a cloned-voice brand word into the
natural pause after the anchor word, so timing and video sync are untouched.

Pipeline:
1. Cut a reference clip of the speaker's own lines (for voice cloning).
2. Synthesize the inserted word(s) in that voice (XTTS if installed,
   edge-tts fallback with a mismatch warning).
3. Overlay at the swap timestamp with a slight duck of the original bed.
"""
import os
import subprocess

BASE_DIR = os.path.dirname(__file__)
EDITED_DIR = os.path.join(BASE_DIR, "static", "uploads", "edited")
TTS_DIR = os.path.join(BASE_DIR, "static", "uploads", "tts")

_xtts = None


def _have_xtts():
    try:
        import TTS  # noqa: F401
        return True
    except ImportError:
        return False


def extract_reference(video_path, spans, out_path):
    """Concatenate the speaker's dialogue spans into one reference wav."""
    parts = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-af", f"aselect='{parts}',asetpts=N/SR/TB",
         "-ar", "22050", "-ac", "1", out_path],
        capture_output=True, check=True)
    return out_path


def synthesize_cloned(text, reference_wav, out_path):
    """XTTS v2 zero-shot cloning; returns (path, engine_used)."""
    global _xtts
    if _have_xtts():
        from TTS.api import TTS
        if _xtts is None:
            _xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        _xtts.tts_to_file(text=text, speaker_wav=reference_wav,
                          language="en", file_path=out_path)
        return out_path, "xtts_v2"
    # fallback: neural TTS without cloning (audible voice mismatch)
    import asyncio
    import edge_tts

    async def run():
        await edge_tts.Communicate(text, "en-US-ChristopherNeural", rate="+15%").save(out_path)

    asyncio.run(run())
    return out_path, "edge-tts (no cloning — voice mismatch)"


def overlay_word(video_path, out_path, word_audio, at_ts, duck_db=-10):
    """Mix the word into the original audio at at_ts. No time shift."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", word_audio],
        capture_output=True, text=True, check=True)
    dur = float(probe.stdout.strip())
    end = at_ts + dur
    duck = f"volume=enable='between(t,{at_ts},{end})':volume={10 ** (duck_db / 20):.3f}"
    fc = (f"[0:a]{duck}[bed];"
          f"[1:a]adelay={int(at_ts * 1000)}|{int(at_ts * 1000)},volume=1.8[w];"
          f"[bed][w]amix=inputs=2:duration=first:normalize=0[out]")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", word_audio,
         "-filter_complex", fc, "-map", "0:v", "-map", "[out]",
         "-c:v", "copy", "-c:a", "aac", out_path],
        capture_output=True, check=True)
    return dur


def run(filename, swap, transcript, chain=False):
    """Place a seamless dialogue brand mention from a stage-1 swap proposal.

    swap: dict from detector dialogue_swaps (start_ts/end_ts anchor the
    replaced words; we insert right after end_ts).
    """
    os.makedirs(EDITED_DIR, exist_ok=True)
    os.makedirs(TTS_DIR, exist_ok=True)
    video_path = os.path.join(BASE_DIR, "static", "uploads", "original", filename)
    out_path = os.path.join(EDITED_DIR, filename)
    if chain and os.path.exists(out_path):
        prev = out_path + ".prev.mp4"
        os.replace(out_path, prev)
        video_path = prev

    # reference: all dialogue spans (speaker separation is future work —
    # in short clips the target speaker dominates)
    spans = [(s["start_ts"], s["end_ts"]) for s in transcript]
    ref_path = os.path.join(TTS_DIR, "ref_speaker.wav")
    extract_reference(video_path, spans, ref_path)

    # what to speak: just the inserted word(s) — the difference between
    # replacement_text and original_text when it's an insertion, else the
    # full replacement phrase
    orig = (swap.get("original_text") or "").strip()
    repl = (swap.get("replacement_text") or "").strip()
    if orig and repl.lower().startswith(orig.lower()):
        # pure insertion after the anchor words: light duck keeps ambience
        speak = repl[len(orig):].strip()
        at_ts = swap["end_ts"] + 0.05
        duck_db = -10
    else:
        # replacement: nearly mute the original words underneath
        speak = repl
        at_ts = swap["start_ts"]
        duck_db = -28

    word_path = os.path.join(TTS_DIR, f"word_{int(swap['start_ts'] * 100)}.wav")
    _, engine = synthesize_cloned(speak, ref_path, word_path)

    dur = overlay_word(video_path, out_path, word_path, at_ts, duck_db=duck_db)
    return {
        "spoken": speak,
        "at_ts": round(at_ts, 2),
        "duration": round(dur, 2),
        "engine": engine,
        "output": out_path,
        "line_after": swap.get("full_line_after"),
    }
