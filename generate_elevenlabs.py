import os
import re
import time
import subprocess
from pathlib import Path
from elevenlabs.client import ElevenLabs

# Actual ElevenLabs Voice IDs with high Arabic realism:
# Professional native Arabic voices in your library:
# - Ziyad (Natural Saudi Arabic)
# - Fahad (Saudi, Warm & Confident)
# - Mamdooh Fg
# - ABDULLAH ALMUDAWI
# - Abdulrahman
# + Adam / George for deep characters

ELEVEN_VOICES = {
    "أبو صقر": "pNGUEZc2WLum9bjP611g",  # Ziyad - Natural Arabic (وقور، رزين)
    "مروان":   "KfYUTAF49N2CPU3iKuMi",  # Abdulrahman (حماسي، نبرة حادة وصريحة)
    "سالم":    "rpGHcNQJvO8dFNNFNj1v",  # Fahad (هادئ، واثق، واقتصادي)
    "نبيل":    "FyTdvEJCoWuIYfS3P1x0",  # ABDULLAH ALMUDAWI (جدي وعسكري)
    "فتحي":    "Rc0jb4oaYXOM4EVr0Jij",  # Mamdooh Fg (خفيف دم ومرح)
    "الجميع":  "pNGUEZc2WLum9bjP611g"
}

FALLBACK_VOICE_ID = "pNGUEZc2WLum9bjP611g"

def load_api_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key.strip()
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None

def get_client():
    api_key = load_api_key()
    if not api_key:
        raise ValueError("يرجى وضع ELEVENLABS_API_KEY في ملف .env")
    return ElevenLabs(api_key=api_key)

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

def render_with_elevenlabs(script_file: str, output_mp3: str = "elevenlabs_yemeni_session.mp3"):
    client = get_client()
    with open(script_file, "r", encoding="utf-8") as f:
        content = f.read()

    items = parse_dialogue(content)
    temp_dir = "temp_eleven_render"
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
    print(f"ElevenLabs Total Dialogue Items: {total}")

    for i, it in enumerate(items):
        if it["type"] == "scene":
            print(f"[{i+1}/{total}] فاصل: {it['text']}")
            playlist.append(scene_chime)
            playlist.append(silence_scene)
            continue

        spk = it["speaker"]
        voice_id = ELEVEN_VOICES.get(spk, FALLBACK_VOICE_ID)
        text = it["text"]
        print(f"[{i+1}/{total}] صوت {spk} (Voice ID: {voice_id[:6]}...): {text[:35]}...")

        seg_file = os.path.join(temp_dir, f"eleven_{i:03d}.mp3")
        
        # Call ElevenLabs Multilingual V2 (Human realistic)
        audio_stream = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128"
        )
        
        with open(seg_file, "wb") as f_out:
            for chunk in audio_stream:
                f_out.write(chunk)
                
        playlist.append(seg_file)
        if it.get("has_laugh"):
            playlist.append(silence_laugh)
        else:
            playlist.append(silence_normal)

    # Concat
    concat_manifest = os.path.join(temp_dir, "eleven_manifest.txt")
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
    render_with_elevenlabs("session_script.txt")
