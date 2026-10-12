import os
import re
import time
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path

# Acoustic profiles per character giving each person a very distinct realistic voice:
VOICE_PROFILES = {
    "أبو صقر": {"pitch": 0.85, "speed": 0.94},   # صوت وقور رزين، أعمق
    "مروان":   {"pitch": 1.15, "speed": 1.10},   # عصبي وحاد وسريع
    "سالم":    {"pitch": 0.95, "speed": 0.98},   # هادئ واقتصادي ومتزن
    "نبيل":    {"pitch": 1.05, "speed": 1.03},   # واثق وعسكري وواضح
    "فتحي":    {"pitch": 1.25, "speed": 1.16},   # مرح ونكت، نبرة عالية وسريعة
    "الجميع":  {"pitch": 1.10, "speed": 1.05},   # تفاعل جماعي
    "الراوي":  {"pitch": 0.92, "speed": 0.95}
}

DEFAULT_PROFILE = {"pitch": 1.0, "speed": 1.0}

def clean_speech_text(text: str) -> tuple[str, bool]:
    has_laugh = bool(re.search(r'[😂🤣]|هههه+|هاهاها', text))
    cleaned = re.sub(r'[😂🤣]', '', text)
    cleaned = re.sub(r'هههههههه+|هههههه+|ههههه|هههه', 'هه هه هه', cleaned)
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
        # Scene title / transition
        if re.match(r'^(المشهد|نهاية الجلسة|الخلاصة|جلسة يمنية)', s) and not (':' in s or '：' in s):
            items.append({"type": "scene", "text": s})
            continue
        
        # Skip notes & descriptions
        if s.startswith('ملاحظة:') or s.startswith('الشخصيات:') or s.startswith('حوار تخيّلي'):
            continue
        if re.match(r'^(أبو صقر|مروان|سالم|نبيل|فتحي):\s*(راعي|عصبي|يفهم|يتابع|خفيف)', s):
            continue

        # Speaker line
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

def fetch_raw_chunk(text: str, out_file: str):
    url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(text)}&tl=ar&client=tw-ob"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read()
    with open(out_file, "wb") as f:
        f.write(content)

def synthesize_text(text: str, out_file: str, temp_dir: str):
    # Split text into small chunks (~120 chars) to prevent Google TTS cutting off
    parts = re.split(r'([،.؟!؛\n])', text)
    chunks = []
    curr = ""
    for p in parts:
        if len(curr) + len(p) < 120:
            curr += p
        else:
            if curr.strip():
                chunks.append(curr.strip())
            curr = p
    if curr.strip():
        chunks.append(curr.strip())
    if not chunks:
        chunks = [text]

    piece_files = []
    for idx, c in enumerate(chunks):
        if not c.strip():
            continue
        part_name = os.path.join(temp_dir, f"raw_part_{abs(hash(text))%100000}_{idx}.mp3")
        for attempt in range(3):
            try:
                fetch_raw_chunk(c, part_name)
                piece_files.append(part_name)
                time.sleep(0.08)
                break
            except Exception:
                time.sleep(0.4)
                
    if len(piece_files) == 1:
        os.replace(piece_files[0], out_file)
    elif len(piece_files) > 1:
        concat_txt = os.path.join(temp_dir, f"concat_{abs(hash(text))%100000}.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            for pf in piece_files:
                posix_p = Path(pf).resolve().as_posix()
                f.write(f"file '{posix_p}'\n")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_txt, "-c", "copy", out_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(concat_txt):
            os.remove(concat_txt)
        for pf in piece_files:
            if os.path.exists(pf):
                os.remove(pf)

def apply_voice_filter(raw_file: str, profile: dict, out_file: str):
    pitch = profile.get("pitch", 1.0)
    speed = profile.get("speed", 1.0)
    rate = int(24000 * pitch)
    tempo = speed / pitch
    subprocess.run([
        "ffmpeg", "-y", "-i", raw_file,
        "-filter_complex", f"asetrate={rate},atempo={tempo}",
        "-acodec", "libmp3lame", "-q:a", "4", out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def generate_silence(duration: float, out_file: str):
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'anullsrc=r=24000:cl=mono',
        '-t', str(duration),
        '-q:a', '9', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def generate_scene_chime(out_file: str):
    # Warm bell tone for scene break
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'sine=frequency=520:duration=0.4',
        '-filter_complex', 'afade=t=in:st=0:d=0.03,afade=t=out:st=0.2:d=0.2',
        '-q:a', '9', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def build_full_audio(raw_script_path: str, output_mp3: str):
    with open(raw_script_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    items = parse_dialogue(content)
    temp_dir = "temp_render_session"
    os.makedirs(temp_dir, exist_ok=True)
    
    silence_normal = os.path.join(temp_dir, "silence_normal.mp3")
    silence_laugh = os.path.join(temp_dir, "silence_laugh.mp3")
    silence_scene = os.path.join(temp_dir, "silence_scene.mp3")
    scene_chime = os.path.join(temp_dir, "scene_chime.mp3")
    
    generate_silence(0.42, silence_normal)
    generate_silence(0.85, silence_laugh)
    generate_silence(1.3, silence_scene)
    generate_scene_chime(scene_chime)
    
    playlist = []
    total = len(items)
    print(f"Total elements to process: {total}")
    
    for i, it in enumerate(items):
        if it["type"] == "scene":
            print(f"[{i+1}/{total}] فاصل مشهد: {it['text']}")
            playlist.append(scene_chime)
            playlist.append(silence_scene)
            continue
            
        spk = it["speaker"]
        prof = VOICE_PROFILES.get(spk, DEFAULT_PROFILE)
        print(f"[{i+1}/{total}] صوت {spk}: {it['text'][:40]}...")
        
        raw_seg = os.path.join(temp_dir, f"seg_{i:03d}_raw.mp3")
        final_seg = os.path.join(temp_dir, f"seg_{i:03d}_final.mp3")
        
        synthesize_text(it["text"], raw_seg, temp_dir)
        apply_voice_filter(raw_seg, prof, final_seg)
        playlist.append(final_seg)
        
        # Add pause after line
        if it.get("has_laugh"):
            playlist.append(silence_laugh)
        else:
            playlist.append(silence_normal)
            
    # Concatenate all
    concat_manifest = os.path.join(temp_dir, "final_manifest.txt")
    with open(concat_manifest, "w", encoding="utf-8") as f:
        for p in playlist:
            f.write(f"file '{Path(p).resolve().as_posix()}'\n")
            
    print("Concatenating all segments into full master session...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_manifest, "-c", "copy", output_mp3
    ], check=True)
    
    size_mb = os.path.getsize(output_mp3) / (1024 * 1024)
    print(f"Master dialogue session generated: {output_mp3} ({size_mb:.2f} MB)")
    return output_mp3

if __name__ == "__main__":
    build_full_audio("session_script.txt", "yemeni_dialogue_session.mp3")
