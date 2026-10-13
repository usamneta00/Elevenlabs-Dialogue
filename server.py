import os
import re
import uuid
import logging
import threading
from pathlib import Path
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from assemble_natural_session import build_natural_session

app = FastAPI(title="Fish Audio Dialogue Studio")
logger = logging.getLogger(__name__)
generation_jobs = {}
generation_jobs_lock = threading.Lock()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_api_key():
    key = os.environ.get("FISH_API_KEY")
    if key:
        return key.strip()
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.lstrip("\ufeff").strip().startswith("FISH_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""

def get_api_key():
    key = load_api_key()
    if not key:
        raise ValueError("يرجى ضبط FISH_API_KEY في إعدادات الخدمة")
    return key

class GenerateRequest(BaseModel):
    text: str
    voiceMapping: dict = {}

@app.get("/api/voices")
def get_voices():
    # Keep the UI limited to curated Arabic voices; Fish account libraries may
    # contain models in unrelated languages without reliable language metadata.
    return [
        {"id": "17f2143d748a4d368cb8a7e624cb1506", "name": "يمني محكي — حيوي ومعبر", "region": "yemen"},
        {"id": "06645dd394b3405fa33c07a818f40e57", "name": "خليجي — هادئ ورزين", "region": "gulf"},
        {"id": "e3b13d8bc61a45c19ee4ba2e08fb5208", "name": "سعودي — شبابي وحواري", "region": "saudi"},
        {"id": "962fe3fab7904c18ba0a250f9b34f53a", "name": "مصري — حيوي ومرح", "region": "egypt"},
        {"id": "ed023b716c7d411e8696bf6e3a4dada0", "name": "عراقي — حواري وودود", "region": "iraq"},
        {"id": "bc65cad6731c4c13a2dd21f02cfe6ac6", "name": "شامي — شبابي وحيوي", "region": "levant"},
        {"id": "8ad316c0e3aa4810a471e1bca511cd32", "name": "مغربي — ودود وحيوي", "region": "morocco"},
        {"id": "49a4c4dddbda43ca97227502160f0f25", "name": "مصري — عميق وحازم", "region": "egypt"},
        {"id": "9cd8bf9f5933408db8ca0f3df941a6cb", "name": "شاب يمني — واضح ونشيط", "region": "yemen"},
        {"id": "0459ad579a554aad9391a84d8da05ead", "name": "شاب يمني — حواري وودود", "region": "yemen"},
        {"id": "b8c0ca1c883f4b0a847c7b7746601e6d", "name": "يمني — جاد ورزين", "region": "yemen"},
        {"id": "10c5c2a37a284a81bb0cf3c53955d795", "name": "سعودي — واضح وهادئ", "region": "saudi"},
        {"id": "fb6c06c8cbf94dd3afb11720d3d87007", "name": "عراقي — نشيط ومعبر", "region": "iraq"},
    ]

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
def generate_dialogue(req: GenerateRequest, background_tasks: BackgroundTasks):
    try:
        api_key = get_api_key()
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
    with generation_jobs_lock:
        generation_jobs[job_id] = {
            "jobId": job_id,
            "status": "queued",
            "completedSegments": 0,
            "totalSegments": len(items),
        }
    background_tasks.add_task(
        run_generation_job,
        job_id,
        req,
        items,
        api_key,
        work_dir,
        final_output,
        manifest_file,
        out_mp3_name,
    )

    return {
        "success": True,
        "jobId": job_id,
        "statusUrl": f"/api/generate/{job_id}",
        "segmentsCount": len(items),
    }


def run_generation_job(job_id, req, items, api_key, work_dir, final_output, manifest_file, out_mp3_name):
    def update_progress(completed, total):
        with generation_jobs_lock:
            job = generation_jobs.get(job_id)
            if job:
                job["completedSegments"] = completed
                job["totalSegments"] = total

    with generation_jobs_lock:
        generation_jobs[job_id]["status"] = "processing"

    try:
        build_natural_session(
            items=items,
            api_key=api_key,
            voice_mapping=req.voiceMapping,
            work_dir=work_dir,
            output_file=final_output,
            manifest_path=manifest_file,
            on_progress=update_progress,
        )
        with generation_jobs_lock:
            generation_jobs[job_id].update({
                "status": "complete",
                "audioUrl": f"/static/{out_mp3_name}",
                "filename": out_mp3_name,
                "segmentsCount": len(items),
            })
    except Exception as e:
        error_body = getattr(e, "body", None)
        error_detail = error_body.get("detail") if isinstance(error_body, dict) else None
        message = error_detail.get("message") if isinstance(error_detail, dict) else None
        message = message or (error_detail if isinstance(error_detail, str) else None)
        message = message or (str(e) if isinstance(e, (RuntimeError, ValueError)) else None)
        message = message or "تعذر توليد الصوت. تحقق من إعدادات Fish Audio ثم أعد المحاولة."
        logger.exception("Audio generation job %s failed", job_id)
        with generation_jobs_lock:
            generation_jobs[job_id].update({"status": "failed", "detail": message})


@app.get("/api/generate/{job_id}")
def get_generation_status(job_id: str):
    with generation_jobs_lock:
        job = generation_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="مهمة التوليد غير موجودة أو انتهت بعد إعادة تشغيل الخدمة")
        return dict(job)


@app.get("/api/sample-script")
def get_sample_script():
    script_path = Path(__file__).with_name("session_script.txt")
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="نص العينة غير موجود")
    return FileResponse(script_path, media_type="text/plain; charset=utf-8")

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
