# Orator backend

FastAPI backend for Orator, a web app that translates documents into other languages and turns them into spoken audio with AWS Translate and AWS Polly.

This repo holds the API only. The frontend and the Terraform infrastructure live in their own repos.

Work in progress. Setup and usage docs will grow as the code does.

## Running locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API starts on http://127.0.0.1:8000. Check it is alive at http://127.0.0.1:8000/api/health.
