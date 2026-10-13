import os
import re
import json
import time
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def assemble_playlist(playlist, output_file, manifest_path, static_copies=()):
    """Join rendered dialogue and ambience for both the web app and CLI flow."""
    if not playlist:
        raise ValueError("لا توجد مقاطع صوتية لتجميعها")

    manifest = Path(manifest_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", encoding="utf-8") as f:
        for item in playlist:
            # Escape apostrophes for ffmpeg concat's single-quoted file syntax.
            path = Path(item).resolve().as_posix().replace("'", "'\\''")
            f.write(f"file '{path}'\n")

    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(manifest.resolve()), "-vn", "-c:a", "libmp3lame",
        "-b:a", "192k", "-ar", "44100", "-ac", "2", str(output)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    for copy_to in static_copies:
        target = Path(copy_to)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(output), "-vn", "-c:a", "copy", str(target)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    return str(output)

def generate_silence(duration: float, out_file: str):
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=stereo',
        '-t', str(duration),
        '-q:a', '2', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def generate_scene_transition(out_file: str):
    """Create a short local transition chime without an external SFX provider."""
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=587.33:duration=0.35",
        "-filter_complex", "afade=t=in:st=0:d=0.03,afade=t=out:st=0.15:d=0.2",
        "-q:a", "2", "-acodec", "libmp3lame", out_file,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fish_tts(text: str, voice_id: str, api_key: str, out_file: str):
    payload = json.dumps({
        "text": text,
        "reference_id": voice_id,
        "format": "mp3",
        "sample_rate": 44100,
        "mp3_bitrate": 128,
        # Stable, natural variation for pre-recorded dialogue (not real-time).
        "temperature": float(os.environ.get("FISH_TTS_TEMPERATURE", "0.7")),
        "top_p": float(os.environ.get("FISH_TTS_TOP_P", "0.7")),
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": os.environ.get("FISH_TTS_MODEL", "s2.1-pro-free"),
    }
    for attempt in range(3):
        request = urllib.request.Request(
            "https://api.fish.audio/v1/tts", data=payload,
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                audio_data = response.read()
            if not audio_data:
                raise ConnectionError("Fish Audio returned an empty audio response")
            with open(out_file, "wb") as audio_file:
                audio_file.write(audio_data)
            return
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except (ValueError, AttributeError):
                pass
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Fish Audio API ({error.code}): {detail}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            if attempt == 2:
                raise RuntimeError(
                    f"انقطع الاتصال بـ Fish Audio أثناء تنزيل الصوت بعد 3 محاولات: {error}"
                ) from error
        time.sleep(1 + attempt)


def dialogue_pause(text: str, previous_speaker: str | None, speaker: str) -> float:
    """Use conversational turn-taking pauses instead of uniform gaps."""
    length = len(text.strip())
    changed_speaker = previous_speaker is not None and previous_speaker != speaker
    if re.search(r'[؟?]$', text):
        return 0.62 if changed_speaker else 0.42
    if re.search(r'[.!…]$', text):
        return 0.54 if changed_speaker else 0.38
    if re.search(r'[،؛,]$', text):
        return 0.30 if length > 45 else 0.22
    if changed_speaker:
        return 0.48 if length > 55 else 0.36
    return 0.24 if length > 55 else 0.18


def fish_tts_with_fallback(text: str, voice_id: str, api_key: str, out_file: str, fallback_voice_ids=()):
    """Retry stale references with other already-assigned cast voices."""
    candidates = [voice_id]
    candidates.extend(candidate for candidate in fallback_voice_ids if candidate and candidate not in candidates)
    last_error = None
    for candidate in candidates:
        try:
            fish_tts(text, candidate, api_key, out_file)
            return candidate
        except RuntimeError as error:
            last_error = error
            if "reference not found" not in str(error).lower():
                raise
    attempted = ", ".join(candidates)
    raise RuntimeError(
        f"تعذرت الأصوات المحددة والبديلة ({attempted}). آخر رد من Fish Audio: {last_error}"
    ) from last_error


def build_natural_session(items, api_key, voice_mapping, work_dir, output_file, manifest_path, on_progress=None):
    """Render page-provided dialogue, natural pauses, and scene cues as one file."""
    os.makedirs(work_dir, exist_ok=True)
    silence_dialogue = os.path.join(work_dir, "pause_dialogue.mp3")
    silence_laugh = os.path.join(work_dir, "pause_laugh.mp3")
    silence_scene = os.path.join(work_dir, "pause_scene.mp3")
    scene_transition = os.path.join(work_dir, "scene_transition.mp3")
    generate_silence(0.18, silence_dialogue)
    generate_silence(0.82, silence_laugh)
    generate_silence(1.25, silence_scene)
    has_scenes = any(item["type"] == "scene" for item in items)
    if has_scenes:
        generate_scene_transition(scene_transition)

    default_voice = os.environ.get("FISH_DEFAULT_VOICE_ID", "")
    playlist = []
    previous_speaker = None

    for index, item in enumerate(items):
        if item["type"] == "scene":
            if playlist:
                playlist.append(silence_scene)
                if has_scenes:
                    playlist.append(scene_transition)
                playlist.append(silence_scene)
            if on_progress:
                on_progress(index + 1, len(items))
            continue

        speaker = item["speaker"]
        if item["text"]:
            segment_file = os.path.join(work_dir, f"seg_{index:03d}.mp3")
            voice_id = voice_mapping.get(speaker) or default_voice
            if not voice_id:
                raise ValueError("أدخل معرف صوت Fish Audio لكل متحدث أو اضبط FISH_DEFAULT_VOICE_ID")
            try:
                alternate_voices = [
                    mapped_voice for mapped_speaker, mapped_voice in voice_mapping.items()
                    if mapped_speaker != speaker and mapped_voice
                ]
                voice_id = fish_tts_with_fallback(
                    item["text"], voice_id, api_key, segment_file, alternate_voices
                )
            except RuntimeError as error:
                # Keep the provider's actionable response and identify the exact
                # speaker/segment so stale Fish Audio references can be replaced.
                raise RuntimeError(
                    f"فشل توليد المقطع {index + 1} للمتحدث «{speaker}» "
                    f"باستخدام مرجع الصوت {voice_id}: {error}"
                ) from error
            playlist.append(segment_file)

        if item.get("has_laugh"):
            # Keep a breathing beat after laughter; the provider still handles
            # the actual laugh marker in the spoken text.
            playlist.append(silence_laugh)
        elif item["text"]:
            pause_duration = dialogue_pause(item["text"], previous_speaker, speaker)
            pause_file = os.path.join(work_dir, f"pause_{int(pause_duration * 1000)}.mp3")
            if not os.path.exists(pause_file):
                generate_silence(pause_duration, pause_file)
            playlist.append(pause_file)
        previous_speaker = speaker
        if on_progress:
            on_progress(index + 1, len(items))

    return assemble_playlist(playlist, output_file, manifest_path)

if __name__ == "__main__":
    raise SystemExit("هذا الملف يُستدعى من server.py بعد تحويل النص إلى مقاطع حوار.")
