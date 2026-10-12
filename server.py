import os
import re
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from elevenlabs.client import ElevenLabs
from assemble_natural_session import build_natural_session

app = FastAPI(title="ElevenLabs Dialogue Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_api_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key.strip()
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""

def get_client(custom_key: str = None):
    key = custom_key or load_api_key()
    if not key:
        raise ValueError("يرجى توفير مفتاح ELEVENLABS_API_KEY")
    return ElevenLabs(api_key=key)

class GenerateRequest(BaseModel):
    text: str
    apiKey: str = ""
    voiceMapping: dict = {}

@app.get("/api/voices")
def get_voices(apiKey: str = ""):
    try:
        client = get_client(apiKey if apiKey.strip() else None)
        v_list = client.voices.get_all()
        return [
            {
                "id": v.voice_id,
                "name": v.name,
                "category": v.category
            }
            for v in v_list.voices
        ]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def clean_speech_text(text: str) -> tuple[str, bool]:
    has_laugh = bool(re.search(r'[😂🤣]|هههه+|هاهاها', text))
    cleaned = re.sub(r'[😂🤣]|هههه+|هاهاها+', ' [laughs] ', text)
    cleaned = re.sub(r'(?:\s*\[laughs\]){2,}', ' [laughs] ', cleaned)
    cleaned = re.sub(r'[\"«»]', '', cleaned)
    cleaned = re.sub(r'(\[laughs\])\s*[،؛,]\s*', r'\1 ', cleaned)
    cleaned = re.sub(r'\s+([،؛,.!?؟])', r'\1', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned, has_laugh

def parse_dialogue(raw_text: str):
    items = []
    lines = raw_text.strip().split('\n')
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.match(r'^(المشهد|نهاية الجلسة|الخلاصة|جلسة يمنية)', s) and not (':' in s or '：' in s):
            items.append({"type": "scene", "text": s})
            continue
        if s.startswith('ملاحظة:') or s.startswith('الشخصيات:') or s.startswith('حوار تخيّلي'):
            continue
        if re.match(r'^(أبو صقر|مروان|سالم|نبيل|فتحي):\s*(راعي|عصبي|يفهم|يتابع|خفيف)', s):
            continue

        m = re.match(r'^([^:：]{1,30})[:：]\s*(.*)$', s)
        if m:
            spk = m.group(1).strip()
            content = m.group(2).strip()
            cleaned, has_laugh = clean_speech_text(content)
            if not cleaned and not has_laugh:
                continue
            items.append({
                "type": "dialogue",
                "speaker": spk,
                "text": cleaned,
                "has_laugh": has_laugh,
            })
    return items

@app.post("/api/generate")
def generate_dialogue(req: GenerateRequest):
    try:
        client = get_client(req.apiKey if req.apiKey.strip() else None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    items = parse_dialogue(req.text)
    if not items:
        raise HTTPException(status_code=400, detail="لم يتم العثور على حوار صالح في النص")

    job_id = str(uuid.uuid4())[:8]
    work_dir = os.path.join("generated_jobs", job_id)
    os.makedirs(work_dir, exist_ok=True)

    out_mp3_name = f"session_{job_id}.mp3"
    final_output = os.path.join("static", out_mp3_name)
    os.makedirs("static", exist_ok=True)

    manifest_file = os.path.join(work_dir, "manifest.txt")
    try:
        build_natural_session(
            items=items,
            client=client,
            voice_mapping=req.voiceMapping,
            work_dir=work_dir,
            output_file=final_output,
            manifest_path=manifest_file,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"فشل توليد أو تجميع الصوت: {e}") from e

    return {
        "success": True,
        "audioUrl": f"/static/{out_mp3_name}",
        "filename": out_mp3_name,
        "segmentsCount": len(items)
    }

# Mount static files
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    return FileResponse("index.html")

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
