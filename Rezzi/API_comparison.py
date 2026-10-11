"""
Rezzi — Gemini resume comparison module.
Reads requirements CSV + PDF resume, sends both to Gemini, returns analysis.
"""

import csv
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

print(
    "Gemini key loaded:",
    bool(os.getenv("GEMINI_API_KEY", "").strip())
)

print(
    "Gemini model:",
    os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
)


BASE_DIR   = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR = BASE_DIR / "uploads"

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_CSV_CHARS = 100_000


# ── File finders ───────────────────────────────────────────────────────────────

def find_csv_file() -> Path | None:
    """Return the most recently modified *_requirements.csv in outputs/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = list(OUTPUT_DIR.glob("*_requirements.csv"))
    if not files:
        print("[API] No requirements CSV in outputs/.")
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def find_pdf_file() -> Path | None:
    """Return the most recently modified PDF in uploads/."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        f for f in UPLOAD_DIR.rglob("*")
        if f.is_file() and f.suffix.lower() == ".pdf"
    ]
    if not files:
        print("[API] No PDF in uploads/.")
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


# ── CSV helpers ────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    try:
        if any(c in text for c in ("â", "Â", "ð")):
            text = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        pass
    for old, new in {
        "â€™": "'",
        "â€œ": '"',
        "â€\x9d": '"',
        "â€“": "-",
        "â€”": "-",
        "â€¦": "...",
        "Â": "",
    }.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def read_csv_file(csv_file: Path) -> tuple[str, list[dict]]:
    """Return (job_title, list_of_record_dicts) from a requirements CSV."""
    records: list[dict] = []
    with csv_file.open("r", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            record = {k: _clean(v or "") for k, v in row.items() if k}
            if any(record.values()):
                records.append(record)

    job_title = next(
        (r["job"] for r in records if r.get("job")),
        csv_file.stem.replace("_requirements", "").replace("_", " "),
    )
    return job_title, records


def remove_duplicate_records(records: list[dict]) -> list[dict]:
    seen:   set[tuple] = set()
    unique: list[dict] = []
    for r in records:
        key = (
            r.get("category", "").lower(),
            r.get("specific_skill", "").lower(),
            r.get("qualification", "").lower(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _build_csv_summary(records: list[dict]) -> str:
    lines = ["Scraped job requirements and skills:", ""]
    for r in records:
        parts: list[str] = []
        if r.get("category"):
            parts.append(f"Category: {r['category']}")
        if r.get("section"):
            parts.append(f"Section: {r['section']}")
        skill = r.get("specific_skill", "")
        if skill and skill.lower() != "general qualification":
            parts.append(f"Skill: {skill}")
        if r.get("qualification"):
            parts.append(f"Qualification: {r['qualification']}")
        if parts:
            lines.append("- " + " | ".join(parts))

    summary = "\n".join(lines)
    if len(summary) > MAX_CSV_CHARS:
        summary = summary[:MAX_CSV_CHARS] + "\n\n[Truncated — too many records]"
    return summary


# ── Gemini call ────────────────────────────────────────────────────────────────

def analyze_resume(
    resume_file: Path,
    csv_file: Path,
    job_title: str,
    records: list[dict],
) -> str:
    """
    Send the resume PDF and requirements CSV to Gemini.
    Returns the analysis text string.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    client = genai.Client(api_key=api_key)

    resume_bytes = resume_file.read_bytes()
    if len(resume_bytes) > 50 * 1024 * 1024:
        raise ValueError("Resume PDF is larger than 50 MB.")

    resume_part = types.Part.from_bytes(
        data=resume_bytes, mime_type="application/pdf"
    )
    summary = _build_csv_summary(records)

    prompt = f"""You are Rezzi, a friendly career and resume assistant.

Compare the attached PDF resume against the scraped job requirements listed below.

The CSV data is noisy web-scraped text — it may contain duplicates, partial entries,
or off-topic lines. Weight repeated, specific, and clearly job-related requirements
more heavily than generic or vague ones.

Speak directly to the user ("your resume", "you should"). Do not describe how this
tool works internally. Do not infer or evaluate protected characteristics such as
age, race, gender, disability, or national origin.

Target job title from the CSV:
{job_title}

Respond with exactly four bullet points, each separated by a blank line.
Keep the full response under 1,400 characters.

Bullet 1 — Match score (0–100) with a one-sentence explanation.
Bullet 2 — Strongest matches: specific skills and experiences well supported by the resume.
Bullet 3 — Gaps: important requirements that are missing, only weakly documented, or absent.
Bullet 4 — Action steps: one thing to do immediately, one for the next application cycle, one long-term.

When describing each item, clearly distinguish between:
  • "Not found in your resume"
  • "Present but weakly documented"
  • "Strongly supported"

Mention education, certifications, licenses, and years of experience when relevant.
Do not invent anything that is not in the resume.

CSV requirements data:
{summary}
"""

    retries = [10, 30, 60]
    response = None

    for attempt, delay in enumerate(retries, 1):
        try:
            print(f"[Gemini] Attempt {attempt}/{len(retries)}…")
            response = client.models.generate_content(
                model=DEFAULT_MODEL,
                contents=[resume_part, prompt],
            )
            break

        except Exception as exc:
            err = str(exc)
            is_temporary = any(
                x in err for x in ("503", "UNAVAILABLE", "high demand", "429", "quota")
            )
            if not is_temporary:
                raise
            if attempt == len(retries):
                raise RuntimeError(
                    "Gemini remained unavailable after all retry attempts."
                ) from exc
            print(f"[Gemini] Temporary error. Retrying in {delay}s…")
            time.sleep(delay)

    if response is None or not response.text:
        raise RuntimeError("Gemini returned an empty response.")

    return response.text.strip()


# ── Output helpers ─────────────────────────────────────────────────────────────

def create_output_filename(job_title: str) -> Path:  # noqa: ARG001
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / "analysis.txt"


def save_analysis(
    analysis: str,
    output_file: Path,
    resume_file: Path,   # noqa: ARG001
    csv_file: Path,      # noqa: ARG001
    job_title: str,      # noqa: ARG001
) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(analysis + "\n", encoding="utf-8")
    print(f"[API] Analysis saved → {output_file}")
