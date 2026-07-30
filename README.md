# Orator backend

FastAPI backend for Orator, a web app that translates documents into other languages and turns them into spoken audio with AWS Translate and AWS Polly.

This repo holds the API only. The frontend and the Terraform infrastructure live in their own repos.

Work in progress. Setup and usage docs will grow as the code does.

Architecture decisions are recorded in [docs/adr](docs/adr/README.md), one numbered file per decision.

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

## Configuration

Copy `.env.example` to `.env` and fill in the values. Everything has a sensible default except the S3 bucket, which is only needed once audio generation is involved.

| Variable | Default | Purpose |
|---|---|---|
| `ORATOR_S3_BUCKET` | empty | S3 bucket where Polly stages synthesised audio |
| `ORATOR_AWS_REGION` | `eu-west-1` | AWS region, chosen because it has neural voices for every supported language |
| `ORATOR_AWS_PROFILE` | empty | Local dev only, pins a named AWS profile. Leave empty when deployed, the IAM role on the compute provides credentials |
| `ORATOR_CORS_ORIGIN` | `http://localhost:5173` | Origin the frontend dev server runs on |
| `ORATOR_DATABASE_URL` | `sqlite:///orator.db` | SQLAlchemy database URL, a local SQLite file by default |
| `ORATOR_MEDIA_DIR` | `media` | Directory for uploaded documents and generated audio |

## Supported languages and voices

The backend does not keep a hand-written language table. On the first request it asks AWS what is actually available: Polly's `describe_voices` lists every voice in the configured region, and that list is intersected with the languages AWS Translate can translate into. The result is served at `GET /api/languages`, which is what the frontend builds its pickers from.

Each language carries its real voices with name, gender, and engine. Neural is preferred, standard is the fallback for voices without neural support, and languages Polly can voice but Translate cannot target (Cantonese, for example) come through with a null `translate_code`.

Caching keeps AWS traffic minimal without ever going stale for long. A successful discovery is cached in memory and refreshed after 24 hours. A failed discovery is retried at most every 5 minutes, and between attempts the API serves the last good catalog, or a built-in six-language fallback if there has never been one. So local development works with no AWS setup at all, and adding credentials later gets picked up without a restart.

## Documents

`POST /api/documents` accepts a file upload in `.docx`, `.pdf`, `.txt`, or `.md` format, up to 10 MB. The text is extracted immediately, stored in the database along with a word count, and the original file is kept under `media/documents/`. Uploads with no extractable text are rejected.

`GET /api/documents` lists what has been uploaded, newest first, without the text. `GET /api/documents/{id}` returns a single document including its full extracted text.

The interactive API reference at http://127.0.0.1:8000/docs covers all endpoints.

## AWS permissions

The IAM user or role behind the credentials only needs what the implemented features use. So far that is discovery:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["polly:DescribeVoices", "translate:ListLanguages"],
      "Resource": "*"
    }
  ]
}
```

This policy grows with the app. Translation will add `translate:TranslateText`, and audio generation will add the Polly synthesis task actions plus S3 access scoped to the staging bucket.
