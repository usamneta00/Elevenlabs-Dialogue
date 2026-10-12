import os
import re
import subprocess
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

def generate_scene_transition(client, out_file: str):
    """Generate a quiet natural outdoor café ambience cue with ElevenLabs SFX."""
    stream = client.text_to_sound_effects.convert(
        text="Brief subtle outdoor Yemeni street café ambience, distant soft traffic and quiet tea-house murmur, no intelligible speech, no music, natural recording",
        duration_seconds=1.8,
        prompt_influence=0.35,
        model_id="eleven_text_to_sound_v2",
        output_format="mp3_44100_128",
    )
    with open(out_file, "wb") as audio_file:
        for chunk in stream:
            audio_file.write(chunk)


def dialogue_pause(text: str, previous_speaker: str | None, speaker: str) -> float:
    """Vary turn timing by punctuation and speaker changes."""
    if previous_speaker and previous_speaker != speaker:
        return 0.38 if re.search(r'[؟?!.…]$', text) else 0.28
    if re.search(r'[؟?!]$', text):
        return 0.34
    if re.search(r'[.…]$', text):
        return 0.48
    if re.search(r'[،؛,]$', text):
        return 0.20
    return 0.26


def build_natural_session(items, client, voice_mapping, work_dir, output_file, manifest_path):
    """Render page-provided dialogue, natural pauses, and scene cues as one file."""
    os.makedirs(work_dir, exist_ok=True)
    silence_dialogue = os.path.join(work_dir, "pause_dialogue.mp3")
    silence_laugh = os.path.join(work_dir, "pause_laugh.mp3")
    silence_scene = os.path.join(work_dir, "pause_scene.mp3")
    scene_transition = os.path.join(work_dir, "scene_transition.mp3")
    generate_silence(0.28, silence_dialogue)
    generate_silence(0.72, silence_laugh)
    generate_silence(1.15, silence_scene)
    has_scenes = any(item["type"] == "scene" for item in items)
    if has_scenes:
        generate_scene_transition(client, scene_transition)

    default_voice = "pNGUEZc2WLum9bjP611g"
    playlist = []
    previous_speaker = None

    dialogue_items = [item for item in items if item["type"] == "dialogue"]
    dialogue_position = 0
    for index, item in enumerate(items):
        if item["type"] == "scene":
            if playlist:
                playlist.append(silence_scene)
                if has_scenes:
                    playlist.append(scene_transition)
                playlist.append(silence_scene)
            continue

        speaker = item["speaker"]
        if item["text"]:
            segment_file = os.path.join(work_dir, f"seg_{index:03d}.mp3")
            current_position = dialogue_position
            previous_text = dialogue_items[current_position - 1]["text"] if current_position else None
            next_text = dialogue_items[current_position + 1]["text"] if current_position + 1 < len(dialogue_items) else None
            stream = client.text_to_speech.convert(
                voice_id=voice_mapping.get(speaker, default_voice),
                text=item["text"],
                # v3 understands inline [laughs] audio tags, so each laugh is
                # produced in the selected character's voice in the same take.
                model_id="eleven_v3",
                output_format="mp3_44100_128",
                previous_text=previous_text,
                next_text=next_text,
                voice_settings={
                    "stability": 0.42,
                    "similarity_boost": 0.78,
                    "style": 0.28,
                    "use_speaker_boost": True,
                },
            )
            with open(segment_file, "wb") as audio_file:
                for chunk in stream:
                    audio_file.write(chunk)
            playlist.append(segment_file)
            dialogue_position += 1

        if item.get("has_laugh"):
            playlist.append(silence_laugh)
        elif item["text"]:
            pause_duration = dialogue_pause(item["text"], previous_speaker, speaker)
            pause_file = os.path.join(work_dir, f"pause_{int(pause_duration * 1000)}.mp3")
            if not os.path.exists(pause_file):
                generate_silence(pause_duration, pause_file)
            playlist.append(pause_file)
        previous_speaker = speaker

    return assemble_playlist(playlist, output_file, manifest_path)

if __name__ == "__main__":
    raise SystemExit("هذا الملف يُستدعى من server.py بعد تحويل النص إلى مقاطع حوار.")
