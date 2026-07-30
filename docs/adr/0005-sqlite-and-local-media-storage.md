# 0005. SQLite and local media storage

Date: 2026-07-30
Status: Accepted

## Context

Documents, translations, and synthesis jobs need to survive a restart. The realistic options were Postgres, which means running and maintaining a second service for an app with a single operator, or SQLite, which is a file. Separately, the uploaded originals and the generated MP3s need a home: database blobs, local disk, or S3.

## Decision

SQLite through SQLModel, with the database URL configurable so the SQLAlchemy layer underneath could point at Postgres later without touching the models. Extracted text lives in the database. Original uploads and generated audio live on local disk under `media/`, with only their paths recorded in the database. S3 stays what ADR 0003 made it, a staging area for Polly output, not the final home of anything.

The schema is created with `create_all` at startup. Proper migrations (Alembic) are deliberately deferred until the schema stops moving.

## Consequences

Zero operational overhead locally, one file to back up or delete. SQLite's single-writer nature is a real limit, but not for one operator's workload. Media files are gitignored and the API never exposes filesystem paths, responses carry document ids and the text itself. If the app ever needs multi-user scale, the escape hatches are the database URL for Postgres and a storage service swap for S3-backed media.
