# Rezzi--The-Resume-Helper

Rezzi is a resume-analysis assistant that accepts a resume (only as a PDF), identifies or accepts an inputted target role, gathers publicly available job-requirement information, compares the resume with those requirements using Google Gemini, and provides a follow-up career chatbot.

The interface is styled after Windows 95, Wii, and Frutiger Aero design aesthetics.

## Features

- PDF resume upload with drag-and-drop support.
- Optional target-job input.
- Automatic target-role detection from the resume when no role is supplied.
- DuckDuckGo-powered job-requirement searches.
- HTML parsing and requirement extraction.
- Gemini-API-powered resume-to-requirements comparison and score.
- Results page with printable analysis.
- Resume-aware chatbot for career questions and resume feedback.
- Local output files for scraped requirements and the generated analysis.

## Project structure

```text
rezzi/
├── app.py                 # Flask application and HTTP routes
├── main.py                # Search, scraping, and analysis orchestration
├── web_scraping.py        # Search and HTML requirement extraction
├── API_comparison.py      # Gemini resume comparison
├── requirements.txt       # Python dependencies
├── README.md
├── uploads/               # Uploaded PDF resumes
├── outputs/               # CSV files, visit logs, and analysis.txt
└── static/
    ├── index.html         # Upload page
    ├── loading.html       # Progress page and Pong animation
    ├── interact.html      # Analysis results
    └── chatbot.html       # Resume chatbot
```

## Requirements

- Python 3.10 or newer is recommended.
- A Google Gemini API key.
- Internet access for web searches and Gemini requests.
- A PDF-only resume.

## Installation

From the project directory:

```bash
python -m venv .venv
```

Activate the environment:

### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
```

### macOS/Linux

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Create a `.env` file beside `app.py`:

```env
GEMINI_API_KEY=your_google_gemini_api_key
GEMINI_MODEL=gemini-3.8-flash
```

`GEMINI_MODEL` is optional. If omitted, Rezzi uses the default configured in `API_comparison.py` and `app.py`.

Do not commit `.env`, API keys, passwords, or private resume files to version control.

## Running Rezzi

Start the Flask server:

```bash
python app.py
```

Open the application at:

```text
http://127.0.0.1:5000
```

## How the workflow operates

1. The user uploads a PDF resume and may enter a target job title.
2. Rezzi saves the PDF in `uploads/`.
3. If no job title is supplied, Gemini is asked to infer a likely role from the resume.
4. Rezzi searches DuckDuckGo using several role-related queries.
5. Returned pages are filtered, downloaded, and parsed with Beautiful Soup.
6. Requirements are written to `outputs/<role>_requirements.csv`.
7. If public pages are unavailable or block requests, Rezzi uses search-result text or conservative role-based fallback requirements.
8. Gemini receives the resume PDF and the cleaned requirements summary.
9. Gemini returns a match score, supported strengths, gaps, and recommended next steps.
10. The result is saved to `outputs/analysis.txt` and displayed at `/interact`.
11. The chatbot at `/chatbot` sends the resume and conversation context to Gemini for follow-up questions.


## Attribution and technology disclosure

### Google Gemini API

Rezzi uses the **Google Gemini API** through the official `google-genai` Python SDK. Gemini is used for:

- Inferring a target job role from an uploaded resume when the user does not provide one.
- Comparing the resume with the scraped requirements.
- Producing the match score, strengths, gaps, and recommendations.
- Answering chatbot questions using the uploaded resume as context.

*The developers of Rezzi do not own Gemini, so all copyright hereby belongs to its creators*

### DuckDuckGo search

Rezzi uses the `duckduckgo-search` Python package to query DuckDuckGo search results. Rezzi is not affiliated with or endorsed by DuckDuckGo. Search results are provided by a third party and may be incomplete, unstable, or subject to rate limits.

Rezzi does not claim ownership of third-party pages returned by search. The scraper should respect website terms of service, robots policies, copyright, rate limits, and applicable law. Scraped text is used as input for analysis rather than republished as a substitute for the original pages.

### Flask

The backend is built with **Flask**, a Python web framework. Flask provides the HTTP server, route handling, JSON responses, and static-page delivery.

### Flask-CORS

**Flask-CORS** is used to configure Cross-Origin Resource Sharing. In a production deployment, its permissive development configuration should be restricted to trusted origins.

### Beautiful Soup 4

**Beautiful Soup 4** is used to parse downloaded HTML and locate headings, paragraphs, and list items that may contain job requirements.

### Requests

**Requests** is used to download web pages for the scraper.

### python-dotenv

**python-dotenv** loads local configuration values, including `GEMINI_API_KEY` and `GEMINI_MODEL`, from `.env`.

### Werkzeug

**Werkzeug** supplies Flask-related utilities, including `secure_filename`, which is used to sanitize uploaded filenames.

### HTML, CSS, and JavaScript

The frontend uses standard HTML, CSS, and browser JavaScript. It does not require a frontend framework or an OpenAI/ChatGPT JavaScript library. The Pong loading animation is custom browser JavaScript and is not a third-party game library.

## ChatGPT and AI-use disclosure

This project was developed with assistance from **OpenAI ChatGPT** for code generation, debugging suggestions, refactoring, interface design, and documentation drafting.

ChatGPT aided in the development of this project. **Note: Rezzi does not call the OpenAI API at runtime.** Runtime generative-AI requests are handled by Google Gemini through the `google-genai` SDK. 

## Privacy and security notes

- Resume PDFs are sent to Google Gemini when role detection, comparison, or chatbot functionality is used.
- Resumes and generated files are stored locally in `uploads/` and `outputs/` unless the deployment environment or a third-party API processes them otherwise.
- Do not use this application with passwords, Social Security numbers, protected health information, restricted student records, or other data that the deployment environment is not authorized to process.
- The development server is intended for local use. It is not a production security configuration.
- Add authentication, HTTPS, request limits, CSRF protection, strict CORS origins, file scanning, encrypted storage, and retention controls before public deployment.
- Do NOT expose API keys in frontend code, Git repositories, screenshots, logs, or error messages (bad stuff will happen).

## Limitations

- Search results and job postings can be unavailable, blocked, outdated, duplicated, or unrelated.
- HTML extraction will not work reliably on every website.
- Fallback requirements are general role-based guidance, not requirements from a specific employer.
- Gemini may make mistakes. Take its suggestions with a grain of salt. 
- The match score is an estimate, not a guarentee or a validated employment assessment.
- The current pipeline keeps status in process memory and is intended for one local server process.

## License and third-party licenses

No project license is specified by this repository. Unless a separate license file is added, the original Rezzi source should be treated as “all rights reserved.” Add an explicit license before distributing the project.

The dependencies listed in `requirements.txt` are separate third-party projects and retain their own licenses. Before redistribution, review the current license and attribution requirements for Flask, Flask-CORS, google-genai, python-dotenv, Requests, Beautiful Soup 4, duckduckgo-search, and Werkzeug. Dependency licenses should not be assumed from this README.

**Thank you for your interest in Rezzi!**
