import os
import re
import subprocess
from pathlib import Path
from assemble_natural_session import fish_tts

FISH_VOICES = {
    "أبو صقر": "FISH_VOICE_ABU_SAQR",
    "مروان": "FISH_VOICE_MARWAN",
    "سالم": "FISH_VOICE_SALEM",
    "نبيل": "FISH_VOICE_NABIL",
    "فتحي": "FISH_VOICE_FATHI",
    "الجميع": "FISH_DEFAULT_VOICE_ID",
}

def load_api_key():
    key = os.environ.get("FISH_API_KEY")
    if key:
        return key.strip()
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.lstrip("\ufeff").strip().startswith("FISH_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None

def get_api_key():
    api_key = load_api_key()
    if not api_key:
        raise ValueError("يرجى وضع FISH_API_KEY في ملف .env")
    return api_key

def clean_speech_text(text: str) -> tuple[str, bool]:
    has_laugh = bool(re.search(r'[😂🤣]|هههه+|هاهاها', text))
    cleaned = re.sub(r'[😂🤣]', '', text)
    cleaned = re.sub(r'[\"«»]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned, has_laugh

def parse_dialogue(raw_text: str):
    items = []
    lines = raw_text.strip().split('\n')
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Scene transition marker
        if re.match(r'^(المشهد|نهاية الجلسة|الخلاصة|جلسة يمنية)', s) and not (':' in s or '：' in s):
            items.append({"type": "scene", "text": s})
            continue
        # Skip description notes
        if s.startswith('ملاحظة:') or s.startswith('الشخصيات:') or s.startswith('حوار تخيّلي'):
            continue
        if re.match(r'^(أبو صقر|مروان|سالم|نبيل|فتحي):\s*(راعي|عصبي|يفهم|يتابع|خفيف)', s):
            continue

        m = re.match(r'^([^:：]{1,30})[:：]\s*(.+)$', s)
        if m:
            spk = m.group(1).strip()
            content = m.group(2).strip()
            cleaned, has_laugh = clean_speech_text(content)
            items.append({
                "type": "dialogue",
                "speaker": spk,
                "text": cleaned,
                "original": content,
                "has_laugh": has_laugh
            })
    return items

def generate_silence(duration: float, out_file: str):
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=stereo',
        '-t', str(duration),
        '-q:a', '2', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def generate_scene_chime(out_file: str):
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'sine=frequency=587.33:duration=0.35',
        '-filter_complex', 'afade=t=in:st=0:d=0.03,afade=t=out:st=0.15:d=0.2',
        '-q:a', '2', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def render_with_fish_audio(script_file: str, output_mp3: str = "fish_audio_yemeni_session.mp3"):
    api_key = get_api_key()
    with open(script_file, "r", encoding="utf-8") as f:
        content = f.read()

    items = parse_dialogue(content)
    temp_dir = "temp_fish_render"
    os.makedirs(temp_dir, exist_ok=True)

    silence_normal = os.path.join(temp_dir, "pause_normal.mp3")
    silence_laugh = os.path.join(temp_dir, "pause_laugh.mp3")
    silence_scene = os.path.join(temp_dir, "pause_scene.mp3")
    scene_chime = os.path.join(temp_dir, "scene_chime.mp3")

    generate_silence(0.45, silence_normal)
    generate_silence(0.9, silence_laugh)
    generate_silence(1.4, silence_scene)
    generate_scene_chime(scene_chime)

    playlist = []
    total = len(items)
    print(f"Fish Audio Total Dialogue Items: {total}")

    for i, it in enumerate(items):
        if it["type"] == "scene":
            print(f"[{i+1}/{total}] فاصل: {it['text']}")
            playlist.append(scene_chime)
            playlist.append(silence_scene)
            continue

        spk = it["speaker"]
        voice_id = os.environ.get(FISH_VOICES.get(spk, ""), os.environ.get("FISH_DEFAULT_VOICE_ID", ""))
        if not voice_id:
            raise ValueError(f"أدخل معرف صوت Fish Audio في متغير بيئة مناسب للمتحدث {spk}")
        text = it["text"]
        print(f"[{i+1}/{total}] صوت {spk} (Voice ID: {voice_id[:6]}...): {text[:35]}...")

        seg_file = os.path.join(temp_dir, f"fish_{i:03d}.mp3")
        fish_tts(text, voice_id, api_key, seg_file)
                
        playlist.append(seg_file)
        if it.get("has_laugh"):
            playlist.append(silence_laugh)
        else:
            playlist.append(silence_normal)

    # Concat
    concat_manifest = os.path.join(temp_dir, "fish_manifest.txt")
    with open(concat_manifest, "w", encoding="utf-8") as f:
        for p in playlist:
            f.write(f"file '{Path(p).resolve().as_posix()}'\n")

    print("Concatenating into master studio file...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_manifest, "-c", "copy", output_mp3
    ], check=True)

    print(f"SUCCESS! Master Studio MP3 created at: {output_mp3}")
    return output_mp3

if __name__ == "__main__":
    render_with_fish_audio("session_script.txt")
