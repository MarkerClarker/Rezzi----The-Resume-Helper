"""
Rezzi pipeline helpers — called by app.py's background thread.

run_scraper(job_title)   → scrape web, save CSV, return path or None
run_resume_analysis()    → read CSV + PDF, call Gemini, save analysis.txt
"""

import re
from pathlib import Path

import requests

from API_comparison import (
    analyze_resume,
    create_output_filename,
    find_csv_file,
    find_pdf_file,
    read_csv_file,
    remove_duplicate_records,
    save_analysis,
)
from web_scraping import (
    get_links,
    save_csv,
    scrape_page,
    search_web,
)
from web_scraping import (
    remove_duplicates as ws_dedup,
)

BASE_DIR   = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"


def _safe_name(text: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return name or "job_search"



def _fallback_requirements(job_title: str) -> list[dict]:
    """Return conservative baseline requirements when public pages are unavailable.

    This keeps the analysis usable when search engines, robots rules, or
    JavaScript-heavy pages prevent HTML extraction. Gemini is told these are
    baseline requirements, not claims about a particular employer.
    """
    role = job_title.lower()
    common = [
        "Relevant education, training, or professional preparation for the role.",
        "Clear written and verbal communication with strong documentation skills.",
        "Ability to work collaboratively, organize priorities, and manage time effectively.",
        "Relevant hands-on experience demonstrating the responsibilities of the target role.",
    ]
    if any(x in role for x in ("nurse", "nursing", "rn", "registered nurse", "lpn", "lvn")):
        common = [
            "Current nursing license or registration appropriate to the position and jurisdiction.",
            "Clinical experience providing safe, compassionate, patient-centered care.",
            "Knowledge of assessment, medication administration, documentation, and care planning.",
            "Strong communication and collaboration with patients, families, and interdisciplinary teams.",
            "Ability to prioritize care, respond to changing patient conditions, and follow safety procedures.",
            "BLS or CPR certification is commonly expected for nursing positions.",
        ]
    elif any(x in role for x in ("software", "developer", "programmer", "engineer")):
        common = [
            "Proficiency with relevant programming languages, frameworks, or development tools.",
            "Experience designing, testing, debugging, and maintaining software applications.",
            "Ability to collaborate with technical and non-technical stakeholders.",
            "Strong problem-solving, communication, and software documentation skills.",
        ]
    elif any(x in role for x in ("data", "analyst", "scientist")):
        common = [
            "Experience collecting, cleaning, analyzing, and interpreting data.",
            "Proficiency with spreadsheets, SQL, statistics, visualization, or related tools.",
            "Ability to communicate findings clearly to technical and non-technical audiences.",
            "Strong attention to detail, problem-solving, and documentation skills.",
        ]

    return [{
        "category": "Baseline requirement",
        "section": "Role-based fallback requirements",
        "specific_skill": "General qualification",
        "qualification": text,
        "source_url": "Generated baseline because public HTML was unavailable",
    } for text in common]

def run_scraper(job_title: str) -> str | None:
    """
    Run DuckDuckGo searches for *job_title*, scrape matching pages,
    and write the extracted requirements to a CSV.

    Returns the CSV file path on success, or None if nothing was found.
    """
    print(f"[Scraper] Searching: {job_title}")

    results = search_web(job_title)
    links = get_links(results)

    if not links:
        print(
            "[Scraper] No web pages found; "
            "using role-based requirements fallback."
        )
        links = []
    else:
        print(f"[Scraper] {len(links)} pages to scrape.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / f"{_safe_name(job_title)}_visited_links.txt"
    records: list[dict] = []

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; RezziBot/1.0; +https://rezzi.local)"
    )

    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"Visited links for: {job_title}\n{'=' * 70}\n\n")
        for n, link in enumerate(links, 1):
            print(f"[Scraper] {n}/{len(links)} {link}")
            page_records, ok, msg = scrape_page(link, session)
            records.extend(page_records)
            log.write(f"[{'OK' if ok else '  '}] {link} — {msg}\n")
            log.flush()

    records = ws_dedup(records)

    # Fallback: job boards and some employer sites block automated HTML
    # requests. Use the search-result title/body as evidence rather than
    # failing the entire pipeline.
    if not records:
        print("[Scraper] HTML extraction returned no records; using search snippets.")
        snippet_records = []
        for result in results:
            title = (result.get("title") or "").strip()
            body = (result.get("body") or result.get("snippet") or "").strip()
            text = " ".join(part for part in (title, body) if part)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 25 or "http" in text.lower():
                continue
            if any(word in text.lower() for word in (
                "require", "qualification", "license", "licensed",
                "experience", "clinical", "patient", "nursing",
                "registered nurse", "rn", "bsn", "certification",
            )):
                snippet_records.append({
                    "category": "Requirement",
                    "section": "Search result summary",
                    "specific_skill": "General qualification",
                    "qualification": text[:360],
                    "source_url": result.get("href", ""),
                })
        records = ws_dedup(snippet_records)

    if not records:
        print("[Scraper] No public requirements could be extracted; using role-based baseline requirements.")
        records = _fallback_requirements(job_title)

    csv_path = save_csv(records, job_title)
    print(f"[Scraper] Saved {len(records)} records → {csv_path}")
    return csv_path


def run_resume_analysis() -> Path | None:
    """
    Find the most recent requirements CSV and PDF resume, send them to
    Gemini for comparison, and save the resulting analysis.

    Returns the Path to outputs/analysis.txt, or None on failure.
    """
    csv_file    = find_csv_file()
    resume_file = find_pdf_file()

    if not csv_file:
        print("[Analysis] No requirements CSV found in outputs/.")
        return None
    if not resume_file:
        print("[Analysis] No PDF resume found in uploads/.")
        return None

    print(f"[Analysis] CSV:    {csv_file.name}")
    print(f"[Analysis] Resume: {resume_file.name}")

    job_title, records = read_csv_file(csv_file)
    records = remove_duplicate_records(records)

    if not records:
        print("[Analysis] CSV had no usable records after deduplication.")
        return None

    print(
        f"[Analysis] Sending {len(records)} records for '{job_title}' to Gemini…"
    )

    try:
        analysis_text = analyze_resume(
            resume_file=resume_file,
            csv_file=csv_file,
            job_title=job_title,
            records=records,
        )
    except Exception as exc:
        print(f"[Analysis] Gemini error: {type(exc).__name__}: {exc}")
        raise

    out_path = create_output_filename(job_title)
    save_analysis(
        analysis=analysis_text,
        output_file=out_path,
        resume_file=resume_file,
        csv_file=csv_file,
        job_title=job_title,
    )
    return out_path
