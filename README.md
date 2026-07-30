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
| `ORATOR_AWS_REGION` | `eu-west-2` | AWS region. London has full Polly neural and Translate coverage, verified against eu-west-1 |
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

## Translations

`POST /api/documents/{id}/translations` with `{"language_code": "fr-FR"}` translates the document's text via AWS Translate and stores the result. The source language is auto-detected, and if it already matches the target the text passes through untranslated at no cost, so "translating" an English document to English is a valid no-op. Long documents are split into chunks of up to 4500 characters, breaking on paragraph boundaries where possible and sentence boundaries otherwise, then reassembled.

The stored translation is meant to be reviewed before any audio is generated. `PATCH /api/translations/{id}` with `{"text": "..."}` saves an edited version and marks it as edited. One translation per document and language, a repeat POST returns 409, delete the old one first if you want a fresh machine translation.

`GET /api/documents/{id}/translations` lists a document's translations, `GET /api/translations/{id}` returns one with its text, `DELETE /api/translations/{id}` removes it.

## Speech synthesis

`POST /api/translations/{id}/synthesis` with `{"voice_id": "Lea"}` starts an audio job and returns 202 immediately. The voice must belong to the translation's language, the catalog endpoint tells you which ones do. The job runs in the background: text is chunked at 2800 characters, each chunk becomes one Polly task writing to the S3 staging bucket, the job polls the tasks, downloads the pieces, joins them into one MP3 stored under `media/audio/`, and deletes the staged objects.

`GET /api/jobs/{id}` reports status and per-chunk progress. Once completed, `GET /api/jobs/{id}/audio` serves the MP3. `GET /api/jobs` lists every job newest first, with an optional `translation_id` filter.

## History

`GET /api/documents/{id}/overview` returns the whole story of one document in a single response: the document, its translations, and each translation's synthesis jobs, all newest first. This is what a history view should render from.

Deletes cascade. Removing a translation also removes its jobs and their audio files from disk, removing a document takes its translations, their jobs, the audio, and the stored original with it.

To voice an untranslated document, create a passthrough translation first (for an English document, "translate" it to `en-GB`, which is free) and synthesise that. This keeps one rule true everywhere: audio always comes from a reviewable text.

Joining multi-chunk audio calls ffmpeg's concat demuxer in stream copy mode, which splices the pieces without re-encoding. Nothing needs to be installed at OS level, the `imageio-ffmpeg` package carries a static ffmpeg binary inside the virtualenv, and a system ffmpeg is preferred automatically when one exists. Single-chunk results skip joining entirely and are copied byte for byte.

## AWS permissions

The IAM user or role behind the credentials only needs what the implemented features use:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "polly:DescribeVoices",
        "polly:StartSpeechSynthesisTask",
        "polly:GetSpeechSynthesisTask",
        "translate:ListLanguages",
        "translate:TranslateText"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<your-staging-bucket>/polly-staging/*"
    }
  ]
}
```

The S3 statement is scoped to the staging prefix of the one bucket the app uses. Polly writes there with the caller's permissions, so `PutObject` is required even though the app itself only downloads and deletes.
