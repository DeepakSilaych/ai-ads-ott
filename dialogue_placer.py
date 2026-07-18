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
import sys

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


def fit_word_audio(word_audio, max_dur):
    """Trim silence from the synthesized clip and time-compress to fit max_dur."""
    trimmed = word_audio + ".fit.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", word_audio, "-af",
         "silenceremove=start_periods=1:start_threshold=-40dB,"
         "areverse,silenceremove=start_periods=1:start_threshold=-40dB,areverse",
         trimmed],
        capture_output=True, check=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", trimmed],
        capture_output=True, text=True, check=True)
    dur = float(probe.stdout.strip())
    if dur > max_dur:
        tempo = min(dur / max_dur, 1.6)
        fitted = word_audio + ".tempo.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", trimmed, "-af", f"atempo={tempo:.3f}", fitted],
            capture_output=True, check=True)
        return fitted
    return trimmed


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


def _rms_db(path, start=None, dur=None):
    cmd = ["ffmpeg"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-af", "astats=metadata=1", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    for line in out.stderr.splitlines():
        if "RMS level dB" in line:
            try:
                return float(line.rsplit(":", 1)[1])
            except ValueError:
                return None
    return None


def _splice_clip(video_path, out_path, new_clip_wav, clip_start, clip_end):
    """Replace [clip_start, clip_end] of the video's audio with new_clip_wav,
    padded/trimmed to the exact original span so timing never shifts.
    Loudness-matches the new clip to the original span (encodec decodes quiet)."""
    span = clip_end - clip_start
    orig_db = _rms_db(video_path, clip_start, span)
    new_db = _rms_db(new_clip_wav)
    gain = 0.0
    if orig_db is not None and new_db is not None:
        gain = max(min(orig_db - new_db, 20), -20)
    fitted = new_clip_wav + ".span.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", new_clip_wav, "-af",
         f"volume={gain:.1f}dB,apad=whole_dur={span:.3f},atrim=0:{span:.3f}",
         "-ar", "44100", fitted],
        capture_output=True, check=True)
    fc = (f"[0:a]atrim=0:{clip_start:.3f},asetpts=N/SR/TB[pre];"
          f"[0:a]atrim=start={clip_end:.3f},asetpts=N/SR/TB[post];"
          f"[1:a]asetpts=N/SR/TB[mid];"
          f"[pre][mid][post]concat=n=3:v=0:a=1[out]")
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", fitted,
         "-filter_complex", fc, "-map", "0:v", "-map", "[out]",
         "-c:v", "copy", "-c:a", "aac", out_path],
        capture_output=True, check=True)


def run_voicecraft(filename, swap, transcript, chain=False, pad_s=1.5):
    """Seamless re-voicing via VoiceCraft span infill: regenerate the edited
    words conditioned on the audio on both sides of the cut."""
    import voicecraft_editor

    os.makedirs(EDITED_DIR, exist_ok=True)
    os.makedirs(TTS_DIR, exist_ok=True)
    video_path = os.path.join(BASE_DIR, "static", "uploads", "original", filename)
    out_path = os.path.join(EDITED_DIR, filename)
    if chain and os.path.exists(out_path):
        prev = out_path + ".prev.mp4"
        os.replace(out_path, prev)
        video_path = prev

    # the transcript segment containing the swap = the line we regenerate
    seg = next((s for s in transcript
                if s["start_ts"] <= swap["start_ts"] and swap["end_ts"] <= s["end_ts"] + 0.5),
               None)
    if seg is None:
        raise RuntimeError("swap span not inside any transcript segment")

    # VoiceCraft needs the clip's audio and transcript to match EXACTLY.
    # Whisper segments can span disfluent gaps containing untranscribed
    # speech, so restrict the clip to the contiguous word run around the
    # swap (split runs at word gaps > 0.8s).
    words = seg.get("words") or []
    if not words:
        raise RuntimeError("segment has no word timestamps")
    runs, cur = [], [words[0]]
    for w in words[1:]:
        if w["s"] - cur[-1]["e"] > 0.8:
            runs.append(cur)
            cur = [w]
        else:
            cur.append(w)
    runs.append(cur)
    run = next((r for r in runs
                if r[0]["s"] - 0.2 <= swap["start_ts"] and swap["end_ts"] <= r[-1]["e"] + 0.2),
               runs[-1])

    clip_start = max(run[0]["s"] - min(pad_s, 0.3), 0)
    clip_end = run[-1]["e"] + min(pad_s, 0.3)
    full_text = " ".join(w["w"] for w in run)
    orig_words = (swap.get("original_text") or "").strip()
    repl_words = (swap.get("replacement_text") or "").strip()
    if orig_words and orig_words.lower() in full_text.lower():
        i = full_text.lower().index(orig_words.lower())
        clip_target = full_text[:i] + repl_words + full_text[i + len(orig_words):]
    else:
        clip_target = swap.get("full_line_after") or full_text
    clip_wav = os.path.join(TTS_DIR, "vc_clip.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{clip_start:.3f}", "-to", f"{clip_end:.3f}",
         "-i", video_path, "-ar", "44100", clip_wav],
        capture_output=True, check=True)

    # Demucs stem split: VoiceCraft conditions on CLEAN vocals only — editing
    # the full mix (voice+music) derails generation. Background stem is
    # remixed untouched afterwards.
    stems_dir = os.path.join(TTS_DIR, "stems")
    subprocess.run(
        [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
         "-o", stems_dir, clip_wav],
        capture_output=True, check=True)
    stem_base = os.path.join(stems_dir, "htdemucs",
                             os.path.splitext(os.path.basename(clip_wav))[0])
    vocals_wav = os.path.join(TTS_DIR, "vc_vocals16k.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", os.path.join(stem_base, "vocals.wav"),
         "-ar", "16000", "-ac", "1", vocals_wav],
        capture_output=True, check=True)
    background_wav = os.path.join(stem_base, "no_vocals.wav")

    edited_wav = os.path.join(TTS_DIR, "vc_edited.wav")

    def _says_brand(wav):
        """Whisper-check: did the generation actually speak the brand word?"""
        from faster_whisper import WhisperModel
        global _verify_model
        try:
            _verify_model
        except NameError:
            _verify_model = WhisperModel("base", device="cpu", compute_type="int8")
        segs, _ = _verify_model.transcribe(wav)
        heard = " ".join(s.text for s in segs).lower()
        brand = repl_words.lower().strip("s")
        loose = brand.replace("gg", "g")  # whisper often mangles brand spellings
        return brand[:4] in heard or loose[:4] in heard

    # give the model breathing room: extend the masked span one word to the
    # left when the replaced words are very short
    mask_start = swap["start_ts"]
    if swap["end_ts"] - mask_start < 0.5:
        prev = [w for w in run if w["e"] <= mask_start + 0.01]
        if prev:
            mask_start = prev[-1]["s"]

    ok = False
    for seed in (11, 111, 1, 7, 42, 123, 888, 2, 5, 99):
        voicecraft_editor.edit_speech(
            vocals_wav,
            orig_transcript=full_text,
            target_transcript=clip_target,
            edit_start_s=mask_start - clip_start,
            edit_end_s=swap["end_ts"] - clip_start,
            out_wav=edited_wav,
            seed=seed,
        )
        if _says_brand(edited_wav):
            ok = True
            break
    if not ok:
        raise RuntimeError("VoiceCraft never spoke the brand word across seeds")

    # remix edited vocals with the untouched background stem
    final_clip = os.path.join(TTS_DIR, "vc_final_clip.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", edited_wav, "-i", background_wav,
         "-filter_complex",
         "[0:a]aresample=44100[v];[1:a]aresample=44100[b];"
         "[v][b]amix=inputs=2:duration=longest:normalize=0[out]",
         "-map", "[out]", final_clip],
        capture_output=True, check=True)

    _splice_clip(video_path, out_path, final_clip, clip_start, clip_end)
    return {
        "spoken": swap.get("replacement_text"),
        "at_ts": swap["start_ts"],
        "engine": "voicecraft",
        "output": out_path,
        "line_after": clip_target,
    }


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

    # keep the insert tight: it must fit the replaced span plus a small tail
    span = max(swap["end_ts"] - swap["start_ts"], 0.3)
    word_path = fit_word_audio(word_path, span + 0.6)

    dur = overlay_word(video_path, out_path, word_path, at_ts, duck_db=duck_db)
    return {
        "spoken": speak,
        "at_ts": round(at_ts, 2),
        "duration": round(dur, 2),
        "engine": engine,
        "output": out_path,
        "line_after": swap.get("full_line_after"),
    }
