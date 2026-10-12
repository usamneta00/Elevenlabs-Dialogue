import os
import re
import asyncio
import subprocess
import edge_tts
from pathlib import Path

# Character voice definitions:
# All voices are high-quality neural voices
# أبو صقر: راعي سياسة ورزين -> ar-YE-SalehNeural (يماني وقور)
# مروان: عصبي وصريح -> ar-SA-HamedNeural (حماسي وحاد)
# سالم: خبير اقتصاد وأسعار -> ar-OM-AbdullahNeural (هادئ ومرتب)
# نبيل: خبير حرب وأسلحة -> ar-IQ-BasselNeural (جدي وواثق)
# فتحي: خفيف دم ونكت -> ar-JO-TaimNeural (مرح ونشيط)
# الجميع: مزيج / ضحكة جماعية

VOICE_CONFIG = {
    "أبو صقر": {
        "voice": "ar-YE-SalehNeural",
        "pitch": "-3Hz",
        "rate": "-2%"
    },
    "مروان": {
        "voice": "ar-SA-HamedNeural",
        "pitch": "+3Hz",
        "rate": "+6%"
    },
    "سالم": {
        "voice": "ar-OM-AbdullahNeural",
        "pitch": "-2Hz",
        "rate": "+1%"
    },
    "نبيل": {
        "voice": "ar-IQ-BasselNeural",
        "pitch": "+1Hz",
        "rate": "+3%"
    },
    "فتحي": {
        "voice": "ar-JO-TaimNeural",
        "pitch": "+5Hz",
        "rate": "+8%"
    },
    "الجميع": {
        "voice": "ar-YE-SalehNeural",
        "pitch": "+3Hz",
        "rate": "+5%"
    }
}

DEFAULT_VOICE = {
    "voice": "ar-YE-SalehNeural",
    "pitch": "+0Hz",
    "rate": "+0%"
}

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
        # Scene marker
        if re.match(r'^(المشهد|نهاية الجلسة|الخلاصة|جلسة يمنية)', s) and not (':' in s or '：' in s):
            items.append({"type": "scene", "text": s})
            continue
        
        # Skip description notes
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

def generate_silence(duration: float, out_file: str):
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'anullsrc=r=24000:cl=mono',
        '-t', str(duration),
        '-q:a', '9', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def generate_scene_chime(out_file: str):
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=0.35',
        '-filter_complex', 'afade=t=in:st=0:d=0.05,afade=t=out:st=0.2:d=0.15',
        '-q:a', '9', '-acodec', 'libmp3lame',
        out_file
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

async def synthesize_all(raw_text: str, output_mp3: str, temp_dir: str = "temp_render"):
    os.makedirs(temp_dir, exist_ok=True)
    items = parse_dialogue(raw_text)
    
    silence_short = os.path.join(temp_dir, "silence_0_4.mp3")
    silence_laugh = os.path.join(temp_dir, "silence_0_8.mp3")
    silence_scene = os.path.join(temp_dir, "silence_1_2.mp3")
    chime_file = os.path.join(temp_dir, "scene_chime.mp3")
    
    generate_silence(0.4, silence_short)
    generate_silence(0.8, silence_laugh)
    generate_silence(1.2, silence_scene)
    generate_scene_chime(chime_file)
    
    file_list = []
    
    print(f"Total elements to render: {len(items)}")
    
    for idx, item in enumerate(items):
        if item["type"] == "scene":
            file_list.append(chime_file)
            file_list.append(silence_scene)
            continue
            
        seg_file = os.path.join(temp_dir, f"seg_{idx:03d}.mp3")
        spk = item["speaker"]
        cfg = VOICE_CONFIG.get(spk, DEFAULT_VOICE)
        voice = cfg["voice"]
        pitch = cfg["pitch"]
        rate = cfg["rate"]
        text = item["text"]
        
        # Render speech
        com = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)
        await com.save(seg_file)
        file_list.append(seg_file)
        
        # Natural pause
        if item.get("has_laugh"):
            file_list.append(silence_laugh)
        else:
            file_list.append(silence_short)
            
    # Concatenate using ffmpeg
    concat_txt = os.path.join(temp_dir, "concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in file_list:
            clean_path = Path(p).resolve().as_posix()
            f.write(f"file '{clean_path}'\n")
            
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_txt,
        '-c', 'copy',
        output_mp3
    ], check=True)
    
    print(f"Full natural dialogue generated: {output_mp3} ({os.path.getsize(output_mp3)} bytes)")
    return output_mp3

if __name__ == "__main__":
    import sys
    with open("session_script.txt", "r", encoding="utf-8") as f:
        text = f.read()
    asyncio.run(synthesize_all(text, "yemeni_dialogue_session.mp3"))
