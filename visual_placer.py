"""Stage 2: visual ad placement via Runway Aleph 2.0 (in-context video editing).

Pipeline: pick a stage-1 visual slot -> link same-surface detections across
analyzed frames to get the visibility window -> cut that segment -> Aleph
replaces the surface with a brand ad (prompt + generated brand-card keyframe)
-> splice the edited segment back over the video track, audio untouched.
"""
import base64
import os
import subprocess

import requests
from PIL import Image, ImageDraw, ImageFont

from brands_catalog import load_catalog

BASE_DIR = os.path.dirname(__file__)
EDITED_DIR = os.path.join(BASE_DIR, "static", "uploads", "edited")
ADS_DIR = os.path.join(BASE_DIR, "static", "uploads", "ads")
WORK_DIR = os.path.join(BASE_DIR, "static", "uploads", "visual")

BRAND_STYLE = {
    "Eggo": ("#F7C815", "#C8102E"),
    "McDonald's": ("#DA291C", "#FFC72C"),
    "Lay's": ("#E32934", "#FFD100"),
    "Domino's": ("#0B648F", "#E31837"),
    "Coca-Cola": ("#F40009", "#FFFFFF"),
    "Red Bull": ("#002654", "#FFC906"),
    "Starbucks": ("#00704A", "#FFFFFF"),
    "Apple": ("#1D1D1F", "#F5F5F7"),
    "Nike": ("#111111", "#FFFFFF"),
    "Uber": ("#000000", "#FFFFFF"),
    "LG": ("#A50034", "#FFFFFF"),
}


def _runway_key():
    with open(os.path.join(BASE_DIR, ".env")) as f:
        for line in f:
            if line.startswith("RUNWAY_API="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("RUNWAY_API not in .env")


def _brand_entry(name):
    for b in load_catalog():
        if b["name"].lower() == name.lower():
            return b
    raise ValueError(f"brand {name!r} not in catalog")


def generate_ad_image(brand_name, w=800, h=450):
    """Brand ad card used as a keyframe reference for Aleph."""
    os.makedirs(ADS_DIR, exist_ok=True)
    entry = _brand_entry(brand_name)
    bg, fg = BRAND_STYLE.get(brand_name, ("#222222", "#FFFFFF"))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    try:
        f_big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                                   int(min(w * 0.16, h * 0.3)))
        f_small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf",
                                     int(min(w * 0.06, h * 0.11)))
    except OSError:
        f_big = f_small = ImageFont.load_default()

    bbox = d.textbbox((0, 0), brand_name, font=f_big)
    d.text(((w - bbox[2]) / 2, h * 0.28 - bbox[3] / 2), brand_name, fill=fg, font=f_big)
    tagline = entry.get("tagline") or ""
    if tagline:
        bbox = d.textbbox((0, 0), tagline, font=f_small)
        d.text(((w - bbox[2]) / 2, h * 0.68), tagline, fill=fg, font=f_small)
    d.rectangle([8, 8, w - 9, h - 9], outline=fg, width=4)

    path = os.path.join(ADS_DIR, f"{brand_name.lower().replace(chr(39), '').replace(' ', '_')}.png")
    img.save(path)
    return path


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua else 0


def visibility_window(visual_slots, anchor, max_gap_s=2.5):
    """Return the (start_ts, end_ts) window in which the anchor surface stays
    visible. Linked by surface LABEL across temporally adjacent sampled
    frames — bbox IoU is useless under camera pans (boxes shift entirely
    between the 2s samples)."""
    def same(a, b):
        a, b = a.lower(), b.lower()
        return a in b or b in a

    times = sorted({s["timestamp"] for s in visual_slots
                    if same(s["surface"], anchor["surface"])})
    lo = hi = anchor["timestamp"]
    for t in reversed([t for t in times if t < anchor["timestamp"]]):
        if lo - t > max_gap_s:
            break
        lo = t
    for t in [t for t in times if t > anchor["timestamp"]]:
        if t - hi > max_gap_s:
            break
        hi = t
    return lo, hi


def build_prompt(slot, brand):
    b = _brand_entry(brand)
    tagline = f' and the tagline "{b["tagline"]}"' if b.get("tagline") else ""
    return (
        f"Replace the {slot['surface']} with a large professional {b['name']} "
        f"advertisement showing the {b['name']} logo text{tagline}, promoting "
        f"{b['products'][0]}. Match the scene's lighting, perspective, film "
        f"grain and era. Keep every other part of the scene exactly the same."
    )


V2V_MODEL = os.environ.get("V2V_MODEL", "aleph2")  # aleph2 | gemini_omni_flash | seedance2_mini


def edit_segment(segment_path, prompt, keyframe_png=None, model=None):
    """Run a video-to-video edit on the segment. Model selectable: aleph2 is
    the premium editor; gemini_omni_flash / seedance2_mini are faster+cheaper
    and fine for small static-surface replacements."""
    from runwayml import RunwayML

    model = model or V2V_MODEL
    client = RunwayML(api_key=_runway_key())
    with open(segment_path, "rb") as f:
        video_uri = "data:video/mp4;base64," + base64.b64encode(f.read()).decode()

    kwargs = {"model": model, "prompt_text": prompt}
    if model.startswith("seedance"):
        kwargs.update(prompt_video=video_uri)  # seedance branch naming
    elif model == "aleph2":
        kwargs.update(video_uri=video_uri, ratio="16:9")
    else:
        kwargs.update(video_uri=video_uri)

    task = client.video_to_video.create(
        **kwargs,
    ).wait_for_task_output(timeout=15 * 60)

    out_url = task.output[0]
    out_path = segment_path.replace(".mp4", ".edited.mp4")
    r = requests.get(out_url, timeout=600)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path


def splice_video(video_path, out_path, segment_path, seg_start, seg_end):
    """Replace [seg_start, seg_end] of the VIDEO track with segment_path
    (scaled to source resolution/fps); audio passes through untouched."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0",
         video_path],
        capture_output=True, text=True, check=True)
    w, h, fr = probe.stdout.strip().split(",")

    fc = (
        f"[0:v]trim=0:{seg_start:.3f},setpts=PTS-STARTPTS[pre];"
        f"[1:v]scale={w}:{h},fps={fr},setpts=PTS-STARTPTS,"
        f"trim=0:{seg_end - seg_start:.3f},setpts=PTS-STARTPTS[mid];"
        f"[0:v]trim=start={seg_end:.3f},setpts=PTS-STARTPTS[post];"
        f"[pre][mid][post]concat=n=3:v=1:a=0[outv]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-i", segment_path,
         "-filter_complex", fc,
         "-map", "[outv]", "-map", "0:a?",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "copy", out_path],
        capture_output=True, check=True)


def track_windows(track, duration=None, pad_s=1.0, sample_s=2.0, merge_gap_s=3.0):
    """A track's keyframes -> merged contiguous [start, end] edit windows."""
    windows = []
    for kf in sorted(track["keyframes"], key=lambda k: k["ts"]):
        s, e = max(kf["ts"] - pad_s, 0), kf["ts"] + sample_s + pad_s
        if windows and s - windows[-1][1] <= merge_gap_s:
            windows[-1][1] = e
        else:
            windows.append([s, e])
    if duration:
        windows = [[s, min(e, duration)] for s, e in windows]
    return windows


def run_track(filename, track, brand_name, chain=False, duration=None, windows=None, model=None):
    """Edit EVERY occurrence of a tracked surface in ONE Aleph call:
    cut all the track's windows, concat into a single video, edit it,
    split at the known boundaries, splice each piece back."""
    os.makedirs(EDITED_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    video_path = os.path.join(BASE_DIR, "static", "uploads", "original", filename)
    out_path = os.path.join(EDITED_DIR, filename)
    if chain and os.path.exists(out_path):
        prev = out_path + ".prev.mp4"
        os.replace(out_path, prev)
        video_path = prev

    if windows is None:
        windows = track_windows(track, duration)
    else:
        # merge indexed windows that nearly touch; drop blips under 1s
        merged = []
        for s, e in windows:
            if merged and s - merged[-1][1] <= 1.5:
                merged[-1][1] = e
            else:
                merged.append([s, e])
        windows = [[s, e] for s, e in merged if e - s >= 1.0]
        if duration:
            windows = [[s, min(e, duration)] for s, e in windows]

    # cut each window at a fixed fps so concat boundaries stay frame-exact
    pieces = []
    for i, (s, e) in enumerate(windows):
        p = os.path.join(WORK_DIR, f"w{i}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", video_path,
             "-c:v", "libx264", "-crf", "18", "-r", "24", "-an", p],
            capture_output=True, check=True)
        pieces.append(p)

    if len(pieces) == 1:
        combined = pieces[0]
    else:
        inputs = []
        for p in pieces:
            inputs += ["-i", p]
        n = len(pieces)
        fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[out]"
        combined = os.path.join(WORK_DIR, "combined.mp4")
        subprocess.run(
            ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[out]",
             "-c:v", "libx264", "-crf", "18", combined],
            capture_output=True, check=True)

    prompt = build_prompt(track, brand_name) + (
        " The video contains several cuts of the same location; apply the SAME"
        " replacement consistently in every shot." if len(pieces) > 1 else "")
    edited = edit_segment(combined, prompt, model=model)

    # split the edited video back at the window boundaries and splice each in
    src = video_path
    offset = 0.0
    for i, (s, e) in enumerate(windows):
        span = e - s
        part = os.path.join(WORK_DIR, f"edited_w{i}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{offset:.3f}", "-to", f"{offset + span:.3f}",
             "-i", edited, "-c:v", "libx264", "-crf", "18", part],
            capture_output=True, check=True)
        step_out = out_path if i == len(windows) - 1 else os.path.join(WORK_DIR, f"step{i}.mp4")
        splice_video(src, step_out, part, s, e)
        src = step_out
        offset += span

    return {
        "type": "visual",
        "brand": brand_name,
        "surface": track.get("surface"),
        "windows": [[round(s, 2), round(e, 2)] for s, e in windows],
        "prompt": prompt,
        "engine": f"runway-{model or V2V_MODEL}",
        "output": out_path,
    }


def run(filename, slot, visual_slots, brand_name, chain=False, pad_s=1.0):
    """Full stage-2 pass for one placement."""
    os.makedirs(EDITED_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    video_path = os.path.join(BASE_DIR, "static", "uploads", "original", filename)
    out_path = os.path.join(EDITED_DIR, filename)
    if chain and os.path.exists(out_path):
        prev = out_path + ".prev.mp4"
        os.replace(out_path, prev)
        video_path = prev

    lo, hi = visibility_window(visual_slots, slot)
    seg_start = max(lo - pad_s, 0)
    seg_end = hi + 2.0 + pad_s  # slot timestamps are frame samples every 2s

    seg_path = os.path.join(WORK_DIR, f"seg_{int(seg_start * 10)}.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{seg_start:.3f}", "-to", f"{seg_end:.3f}",
         "-i", video_path, "-c:v", "libx264", "-crf", "18", "-an", seg_path],
        capture_output=True, check=True)

    prompt = build_prompt(slot, brand_name)
    ad_png = generate_ad_image(brand_name)
    edited_seg = edit_segment(seg_path, prompt, keyframe_png=ad_png)
    splice_video(video_path, out_path, edited_seg, seg_start, seg_end)

    return {
        "type": "visual",
        "brand": brand_name,
        "surface": slot.get("surface"),
        "seg_start": round(seg_start, 2),
        "seg_end": round(seg_end, 2),
        "prompt": prompt,
        "engine": "runway-aleph2",
        "ad_image": "/" + os.path.relpath(ad_png, BASE_DIR),
        "output": out_path,
    }
