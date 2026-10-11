"""
Rezzi web scraper — searches DuckDuckGo for job postings and extracts
requirement bullet points from HTML pages.
"""

import csv
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

BASE_DIR   = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"

# ── Blocked job-board domains (require login / have no useful HTML) ────────────
BLOCKED_SITES: set[str] = {
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "careerbuilder.com",
    "simplyhired.com",
    "dice.com",
}

# ── Four complementary search queries per job title ────────────────────────────
SEARCH_QUERIES: list[str] = [
    "{} job requirements qualifications skills",
    "{} required skills experience education job posting",
    "{} preferred skills job posting",
    "{} hiring requirements responsibilities",
    "{} job description qualifications license experience" ,
]

# ── Regex patterns ─────────────────────────────────────────────────────────────
HEADING_RE = re.compile(
    r"requirement|qualification|skill|experience|education|"
    r"what you need|what we.re looking for|preferred|"
    r"desired|minimum qualification|candidate profile",
    re.IGNORECASE,
)

REMOVE_RE = re.compile(
    r"salary|benefits?|apply now|equal opportunity|"
    r"about us|company overview|contact us|privacy policy|"
    r"copyright|newsletter|read more|learn more|https?://|www\.|"
    r"what do employers|below are|common patterns|"
    r"this article|career path|internal links|"
    r"^how to become|^for example",
    re.IGNORECASE,
)

FILLER_RE = re.compile(
    r"^(the ideal candidate will|the successful candidate will|"
    r"candidates must|candidates should|applicants must|"
    r"must be able to|ability to|able to|"
    r"you will be responsible for|responsible for)\s+",
    re.IGNORECASE,
)

DESIRED_RE = re.compile(
    r"preferred|desired|nice to have|bonus|plus|helpful|advantageous",
    re.IGNORECASE,
)

SKILL_CUE_RE = re.compile(
    r"(?:experience|proficiency|proficient|knowledge|familiarity|skilled|"
    r"expertise|ability|background|competency|competence)\s+"
    r"(?:with|in|using|to use|for)\s+(.+)",
    re.IGNORECASE,
)

DEGREE_RE = re.compile(
    r"\b((?:associate|bachelor.?s|master.?s|doctoral|ph\.?d\.?)"
    r"(?: degree)?|high school diploma|ged|"
    r"professional certification|industry certification|"
    r"driver.?s license|commercial driver.?s license)\b",
    re.IGNORECASE,
)

YEARS_RE = re.compile(
    r"\b\d+(?:\s*[-–]\s*\d+)?\+?\s+years?"
    r"(?:\s+of)?\s+(?:professional\s+)?experience\b",
    re.IGNORECASE,
)

COMMON_SKILLS: set[str] = {
    "communication", "written communication", "verbal communication",
    "customer service", "problem solving", "critical thinking",
    "analytical thinking", "time management", "project management",
    "teamwork", "collaboration", "leadership", "organization",
    "attention to detail", "decision making", "data analysis",
    "research", "public speaking", "presentation", "negotiation",
    "sales", "marketing", "budgeting", "scheduling", "training",
    "teaching", "classroom management", "curriculum development",
    "content writing", "technical writing", "social media management",
    "inventory management", "quality assurance", "record keeping",
    "documentation", "reporting", "event planning", "conflict resolution",
    "project coordination",
}


# ── Text helpers ───────────────────────────────────────────────────────────────

def fix_encoding(text: str) -> str:
    try:
        if any(c in text for c in ("â", "Â", "ð")):
            text = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        pass
    for old, new in {
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-",
        "\u2026": "...", "Â": "",
    }.items():
        text = text.replace(old, new)
    return text


def clean_text(text: str) -> str:
    text = fix_encoding(text)
    text = re.sub(r"\[[0-9]+\]", "", text)
    text = re.sub(r"\([^)]{0,100}\)", "", text)
    text = re.sub(r"^[•●▪◦*–—-]+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip(" ,;:-")


def shorten_text(text: str) -> str:
    text = clean_text(text)
    text = FILLER_RE.sub("", text)
    text = re.sub(
        r"^(typically requires|usually requires|often requires|"
        r"requires|must have|should have|must possess|minimum of)\s+",
        "", text, flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip(" ,;:-")


def is_valid_requirement(text: str, allow_generic: bool = False) -> bool:
    text  = shorten_text(text)
    words = text.split()
    if not 3 <= len(words) <= 60:
        return False
    if not 18 <= len(text) <= 360:
        return False
    if "?" in text or REMOVE_RE.search(text):
        return False

    has_signal = bool(
        DEGREE_RE.search(text)
        or YEARS_RE.search(text)
        or SKILL_CUE_RE.search(text)
        or any(s.lower() in text.lower() for s in COMMON_SKILLS)
        or re.search(r"\b(license|licensed|certification|certified|registration|clinical|patient|medical|hospital|care)\b", text, re.I)
    )
    return has_signal or allow_generic


def normalize_skill(skill: str) -> str:
    skill = clean_text(skill)
    skill = re.sub(
        r"^(including|such as|like|for example)\s+",
        "", skill, flags=re.IGNORECASE,
    )
    return skill.strip(" ,;:-").lower()


def display_skill(skill: str) -> str:
    ACRONYMS = {"ai", "api", "crm", "erp", "html", "css", "sql",
                "excel", "seo", "hr", "pos", "bi", "aws", "gcp"}
    return " ".join(
        w.upper() if w.lower() in ACRONYMS else w.capitalize()
        for w in skill.split()
    )


def extract_specific_skills(text: str) -> list[str]:
    found: list[str] = []

    def _add(raw: str) -> None:
        n = normalize_skill(raw)
        if not n or len(n.split()) > 8:
            return
        if n not in {normalize_skill(x) for x in found}:
            found.append(display_skill(n))

    cue = SKILL_CUE_RE.search(text)
    if cue:
        chunk = re.split(
            r"\b(?:while|and must|as well as|in addition)\b",
            cue.group(1), maxsplit=1, flags=re.IGNORECASE,
        )[0]
        for part in re.split(r",|;|\s+\band\b|\s*/\s*|\s+\bor\b",
                             chunk, flags=re.IGNORECASE):
            _add(part)

    for s in COMMON_SKILLS:
        if re.search(rf"\b{re.escape(s)}\b", text, re.IGNORECASE):
            _add(s)

    for m in DEGREE_RE.finditer(text):
        _add(m.group(1))
    for m in YEARS_RE.finditer(text):
        _add(m.group(0))

    return found


# ── Public API ─────────────────────────────────────────────────────────────────

def search_web(job_title: str, results_per_query: int = 8) -> list[dict]:
    """Run DuckDuckGo searches and return unique result dicts."""
    results:    list[dict] = []
    seen_links: set[str]   = set()

    try:
        with DDGS() as ddgs:
            for fmt in SEARCH_QUERIES:
                query = fmt.format(job_title)
                print(f"  [DDG] {query}")
                try:
                    for item in ddgs.text(query, max_results=results_per_query):
                        link = item.get("href", "").strip()
                        if link and link not in seen_links:
                            seen_links.add(link)
                            results.append(item)
                except Exception:
                    pass   # one query failing shouldn't stop the rest
    except Exception as exc:
        print(f"[Search] DDGS error: {exc}")

    print(f"[Search] {len(results)} unique results.")
    return results


def get_links(results: list[dict]) -> list[str]:
    """Return HTTP links that are not from blocked domains."""
    links: list[str] = []
    seen:  set[str]  = set()

    for item in results:
        link   = item.get("href", "").strip()
        domain = urlparse(link).netloc.lower().removeprefix("www.")

        if not link.startswith(("http://", "https://")):
            continue
        if any(domain == s or domain.endswith(f".{s}") for s in BLOCKED_SITES):
            continue
        if link not in seen:
            seen.add(link)
            links.append(link)

    return links


def remove_duplicates(records: list[dict]) -> list[dict]:
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


def extract_qualifications(html: str, source_url: str) -> list[dict]:
    """Parse headings first, then fall back to ordinary job-posting bullets."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer",
                     "header", "aside", "form", "button"]):
        tag.decompose()

    records: list[dict] = []
    seen_text: set[str] = set()

    def add_record(qualification: str, section: str, desired: bool) -> None:
        qualification = shorten_text(qualification)
        key = qualification.lower()
        if key in seen_text or not is_valid_requirement(qualification):
            return
        seen_text.add(key)
        skills = extract_specific_skills(qualification) or ["General qualification"]
        category = "Desired Skill" if desired or DESIRED_RE.search(qualification) else "Requirement"
        for skill in skills:
            records.append({
                "category": category,
                "section": section or "Job requirements",
                "specific_skill": skill,
                "qualification": qualification,
                "source_url": source_url,
            })

    # Preferred path: content beneath requirement-related headings.
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        section = clean_text(heading.get_text(" ", strip=True))
        if not HEADING_RE.search(section):
            continue
        desired = bool(DESIRED_RE.search(section))
        for el in heading.find_all_next(["li", "p"]):
            if el.name and re.match(r"^h[1-6]$", el.name):
                break
            add_record(el.get_text(" ", strip=True), section, desired)

    # Fallback: many job pages have no headings, or use divs instead of h-tags.
    # Inspect concise list items and paragraphs across the visible page.
    if not records:
        for el in soup.find_all(["li", "p"]):
            text = el.get_text(" ", strip=True)
            add_record(text, "Job requirements", False)
            if len(records) >= 80:
                break

    return remove_duplicates(records)

def scrape_page(
    url: str,
    session: requests.Session,
) -> tuple[list[dict], bool, str]:
    """Download a single URL and extract requirement records."""
    try:
        resp = session.get(url, timeout=10)
        ct   = resp.headers.get("Content-Type", "").lower()

        if resp.status_code != 200:
            return [], False, f"HTTP {resp.status_code}"
        if "text/html" not in ct:
            return [], False, "Not HTML"

        records = extract_qualifications(resp.text, url)
        return records, True, f"{len(records)} records"

    except requests.exceptions.Timeout:
        return [], False, "Timeout"
    except requests.exceptions.ConnectionError:
        return [], False, "Connection error"
    except requests.RequestException as exc:
        return [], False, str(exc)
    except Exception as exc:
        return [], False, f"Error: {exc}"


def save_csv(records: list[dict], job_title: str) -> str:
    """Write records to outputs/<stem>_requirements.csv and return the path."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", job_title).strip("_")
    path = OUTPUT_DIR / f"{stem}_requirements.csv"

    fields = ["job", "category", "section",
              "specific_skill", "qualification", "source_url"]

    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "job":            job_title,
                "category":       r.get("category", ""),
                "section":        r.get("section", ""),
                "specific_skill": r.get("specific_skill", ""),
                "qualification":  r.get("qualification", ""),
                "source_url":     r.get("source_url", ""),
            })

    return str(path)
