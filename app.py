"""
Rezzi — main Flask server.

Routes
------
GET  /               → static/index.html   (upload form)
GET  /loading        → static/loading.html (Pong + poll)
GET  /interact       → static/interact.html (results)
GET  /chatbot        → static/chatbot.html  (AI chat)

POST /api/upload     → save PDF, detect role, start pipeline thread
POST /api/job        → pre-save job title (optional)
GET  /api/status     → pipeline status: idle | running | done | error
GET  /api/analysis   → analysis text (plain text, when done)
POST /chat           → chatbot (resume as context)
GET  /health         → server health check
"""

import os
import re
import shutil
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from google import genai
from google.genai import types
from werkzeug.utils import secure_filename

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"

for _d in (UPLOAD_DIR, OUTPUT_DIR, STATIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Gemini client ──────────────────────────────────────────────────────────────
GEMINI_KEY   = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

if not GEMINI_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing.\n"
        "Create a .env file next to app.py with:\n"
        "  GEMINI_API_KEY=your_key_here"
    )

_gemini = genai.Client(api_key=GEMINI_KEY)

# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
CORS(app)

# ── Pipeline state (shared between threads) ────────────────────────────────────
_state: dict = {
    "status":      "idle",   # idle | running | done | error
    "job_title":   "",
    "output_file": None,
    "error":       "",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Page routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return send_from_directory(STATIC_DIR, "index.html")

@app.get("/loading")
def loading_page():
    return send_from_directory(STATIC_DIR, "loading.html")

@app.get("/interact")
def interact_page():
    return send_from_directory(STATIC_DIR, "interact.html")

@app.get("/chatbot")
def chatbot_page():
    return send_from_directory(STATIC_DIR, "chatbot.html")


# ══════════════════════════════════════════════════════════════════════════════
#  API routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    pdfs = list(UPLOAD_DIR.rglob("*.pdf"))
    best = max(pdfs, key=lambda f: f.stat().st_mtime) if pdfs else None
    return jsonify({
        "server":        "online",
        "resume_found":  best is not None,
        "resume_name":   best.name if best else None,
        "resume_folder": str(UPLOAD_DIR),
    })


@app.post("/api/job")
def save_job():
    """Optional: pre-save a job title before the upload step."""
    data  = request.get_json(silent=True) or {}
    title = data.get("job_title", "").strip()
    if not title:
        return jsonify({"error": "Please enter a job title."}), 400
    _state["job_title"] = title
    return jsonify({"message": f"Job title saved: {title}"})


@app.post("/api/upload")
def upload():
    """
    Accept a PDF resume and an optional job_title form field.
    Saves the file, auto-detects the role if needed, wipes old outputs,
    and kicks off the analysis pipeline in a background thread.
    """
    if "myFile" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    file = request.files["myFile"]
    if not file.filename:
        return jsonify({"error": "No file was selected."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF resumes are accepted."}), 400

    # Save the PDF (overwrite previous with same name)
    safe_name = secure_filename(file.filename)
    dest = UPLOAD_DIR / safe_name
    file.save(str(dest))

    # Job title: multipart form field → pre-saved → Gemini auto-detect
    form_title = (request.form.get("job_title") or "").strip()
    job_title  = form_title or _state.get("job_title", "").strip()
    if not job_title:
        job_title = _detect_role(dest)

    _state["job_title"] = job_title

    # Start fresh pipeline
    _wipe_outputs()
    _state.update(status="running", output_file=None, error="")
    threading.Thread(target=_pipeline_worker, args=(job_title,), daemon=True).start()

    return jsonify({
        "message":   f"'{safe_name}' uploaded. Analysis started.",
        "job_title": job_title,
    })


@app.get("/api/status")
def api_status():
    return jsonify({
        "status":    _state["status"],
        "job_title": _state["job_title"],
        "error":     _state.get("error", ""),
    })


@app.get("/api/analysis")
def api_analysis():
    if _state["status"] != "done":
        return jsonify({"error": "Analysis is not ready yet."}), 404

    path = _state.get("output_file")
    if not path or not Path(path).exists():
        return jsonify({"error": "Analysis file not found on disk."}), 404

    return Response(Path(path).read_text(encoding="utf-8"), mimetype="text/plain")


@app.post("/chat")
def chat():
    """Answer resume/career questions with the uploaded resume as context."""
    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])

    if not message:
        return jsonify({"error": "No message provided."}), 400

    pdfs = list(UPLOAD_DIR.rglob("*.pdf"))
    if not pdfs:
        return jsonify({
            "error": "No resume found in uploads/. Please upload a resume first."
        }), 400

    resume_path  = max(pdfs, key=lambda f: f.stat().st_mtime)
    resume_bytes = resume_path.read_bytes()
    if len(resume_bytes) > 50 * 1024 * 1024:
        return jsonify({"error": "Resume must be under 50 MB."}), 400

    resume_part = types.Part.from_bytes(
        data=resume_bytes, mime_type="application/pdf"
    )

    conv = "\n".join(
        f"{h.get('role', 'user').capitalize()}: {h.get('text', '').strip()}"
        for h in history
        if h.get("text", "").strip()
    )

    prompt = (
        "You are Rezzi, a friendly and knowledgeable career and resume assistant.\n\n"
        "Use the attached resume when answering. "
        "Only claim something is in the resume if the document actually supports it. "
        "Do not invent experience, education, certifications, skills, employers, or accomplishments.\n\n"
        "You can help with: resume improvement and wording, career planning, "
        "job requirements and skills-gap analysis, interview preparation, "
        "education and certifications, and career transitions.\n\n"
    )
    if conv:
        prompt += f"Previous conversation:\n{conv}\n\n"
    prompt += f"User: {message}"

    # Gemini can temporarily return 503 when the selected model is busy.
    # Retry a few times before reporting the error to the browser.
    retry_delays = [3, 8, 15]
    response = None

    for attempt, delay in enumerate(retry_delays, start=1):
        try:
            print(f"[Chat] Contacting Gemini (attempt {attempt}/{len(retry_delays)})")
            response = _gemini.models.generate_content(
                model=GEMINI_MODEL,
                contents=[resume_part, prompt],
            )
            break

        except Exception as exc:
            error_text = str(exc)
            temporary = any(
                marker in error_text.lower()
                for marker in (
                    "503",
                    "unavailable",
                    "high demand",
                    "temporarily",
                    "429",
                    "resource_exhausted",
                )
            )

            if not temporary or attempt == len(retry_delays):
                print(f"[Chat] Gemini error: {type(exc).__name__}: {exc}")
                return jsonify({
                    "error": (
                        "Gemini is temporarily unavailable. Please try again "
                        "in a moment."
                    )
                }), 503

            print(f"[Chat] Gemini is busy; retrying in {delay} seconds.")
            time.sleep(delay)

    if response is None or not response.text:
        return jsonify({"error": "Gemini returned an empty response."}), 502

    return jsonify({
        "response": response.text
    })


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _detect_role(pdf_path: Path) -> str:
    """Ask Gemini to infer the target job role from the resume."""
    try:
        part = types.Part.from_bytes(
            data=pdf_path.read_bytes(), mime_type="application/pdf"
        )
        resp = _gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                part,
                (
                    "Read this resume. Reply with ONLY the job title or role "
                    "it is targeting — nothing else, no punctuation, no explanation. "
                    "Examples: Software Engineer  |  Registered Nurse  |  Data Analyst"
                ),
            ],
        )
        title = (resp.text or "").strip().strip('"').strip("'").rstrip(".")
        return title if title else "General Professional"
    except Exception:
        return "General Professional"


def _wipe_outputs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _pipeline_worker(job_title: str) -> None:
    """Background thread: web-scrape requirements → Gemini comparison → save."""
    try:
        # Deferred import keeps startup fast and avoids circular imports
        from main import run_scraper, run_resume_analysis

        print(f"\n[Pipeline] ▶ Starting for: '{job_title}'")
        csv_path = run_scraper(job_title)

        if csv_path is None:
            _state.update(
                status="error",
                error=(
                    f"The web scraper found no job requirements for '{job_title}'. "
                    "Try a more specific job title and re-upload your resume."
                ),
            )
            return

        output_path = run_resume_analysis()

        if output_path is None:
            _state.update(
                status="error",
                error="Gemini could not complete the analysis. Check your API key and quota.",
            )
            return

        _state.update(status="done", output_file=str(output_path))
        print(f"[Pipeline] ✓ Done — {output_path}")

    except Exception as exc:
        print(f"[Pipeline] ✗ Error: {exc}")
        _state.update(status="error", error=str(exc))


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 58)
    print("  Rezzi — Resume Analysis Assistant")
    print("  Open your browser → http://127.0.0.1:5000")
    print("=" * 58 + "\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
