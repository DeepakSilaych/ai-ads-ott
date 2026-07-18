"""VoiceCraft span-infill speech editing.

Given a clip of the original line, the original + target transcripts, and the
word span to change (seconds, relative to the clip), regenerates the edited
span in the same voice conditioned on audio on BOTH sides of the cut.

Heavy: ~830M model on CPU. Load once per process.
"""
import os
import sys

os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", "/opt/homebrew/lib/libespeak-ng.dylib")

BASE_DIR = os.path.dirname(__file__)
VC_DIR = os.path.join(BASE_DIR, "vendor", "VoiceCraft")
PRETRAINED = os.path.join(BASE_DIR, "vendor", "pretrained")
sys.path.insert(0, VC_DIR)

_model = None
_bundle = None


def _patch_torch_load():
    """audiocraft/VoiceCraft checkpoints predate torch>=2.6 weights_only default.
    We trust these HF-downloaded files; force weights_only=False globally."""
    import torch
    if getattr(torch.load, "_wo_patched", False):
        return
    orig = torch.load

    def load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return orig(*args, **kwargs)

    load._wo_patched = True
    torch.load = load


def _load():
    global _model, _bundle
    if _bundle is not None:
        return _bundle
    _patch_torch_load()
    import torch
    from models import voicecraft
    from data.tokenizer import AudioTokenizer, TextTokenizer

    device = "cpu"
    ckpt_path = os.path.join(PRETRAINED, "giga830M.pth")
    encodec_path = os.path.join(PRETRAINED, "encodec_4cb2048_giga.th")

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model = voicecraft.VoiceCraft(ckpt["config"])
    model.load_state_dict(ckpt["model"])
    model.to(device)
    model.eval()

    phn2num = ckpt["phn2num"]
    text_tokenizer = TextTokenizer(backend="espeak")
    audio_tokenizer = AudioTokenizer(signature=encodec_path)

    _bundle = (model, ckpt["config"], phn2num, text_tokenizer, audio_tokenizer, device)
    return _bundle


def edit_speech(clip_wav, orig_transcript, target_transcript,
                edit_start_s, edit_end_s, out_wav, margin_s=0.08, seed=1):
    """Regenerate [edit_start_s, edit_end_s] of clip_wav so it speaks
    target_transcript instead of orig_transcript. Returns out_wav path."""
    import torch
    import torchaudio
    from inference_speech_editing_scale import inference_one_sample
    from argparse import Namespace

    model, config, phn2num, text_tokenizer, audio_tokenizer, device = _load()
    torch.manual_seed(seed)

    codec_sr = 50
    info = torchaudio.info(clip_wav)
    clip_dur = info.num_frames / info.sample_rate

    start = max(edit_start_s - margin_s, 0)
    end = min(edit_end_s + margin_s, clip_dur)
    mask_interval = torch.LongTensor(
        [[round(start * codec_sr), round(end * codec_sr)]])

    decode_config = {
        "top_k": -1, "top_p": 0.8, "temperature": 1,
        "stop_repetition": 2, "kvcache": 1,
        "codec_sr": codec_sr, "codec_audio_sr": 16000,
        "silence_tokens": [1388, 1898, 131],
    }

    with torch.no_grad():
        orig_audio, gen_audio = inference_one_sample(
            model, config, phn2num, text_tokenizer, audio_tokenizer,
            clip_wav, target_transcript, mask_interval, device, decode_config)

    gen = gen_audio[0].cpu()
    torchaudio.save(out_wav, gen, 16000)
    return out_wav
