# 0007. Dual-runtime serverless architecture

Date: 2026-08-05
Status: Accepted
Partially supersedes: [0005](0005-sqlite-and-local-media-storage.md), [0006](0006-in-process-background-jobs.md)

## Context

Orator is getting deployed, and the deployment target is serverless: Lambda behind an HTTP API Gateway, provisioned by a Terraform module that composes the existing tf-module family. The reasons are cost and honesty. Lambda, API Gateway, DynamoDB, and S3 bill per use and cost nothing at rest, which fits an app one person uses occasionally. A container service would tick a monthly bill for the same result. The synthesis pipeline also gets to showcase Step Functions, which is exactly the kind of orchestration it is shaped like.

Lambda breaks three assumptions the backend was built on:

1. Background work dies. The execution environment freezes the moment a handler returns, so the in-process thread from ADR 0006 that polls Polly after the 202 response would simply never run.
2. SQLite is a local file. Every Lambda instance has its own ephemeral disk, so the database would vanish and diverge between concurrent instances.
3. `media/` is a local folder. Same disk, same problem, for uploaded originals and finished MP3s.

At the same time, running locally with zero cloud setup is a hard requirement. The local development experience from ADRs 0005 and 0006 works and stays.

## Decision

One backend, two runtimes, selected by a single setting, `ORATOR_RUNTIME=local|aws`. Three seams get an interface with two implementations each:

| Seam | local | aws |
|---|---|---|
| Persistence | SQLite file | DynamoDB |
| Media storage | `media/` folder, API serves the bytes | S3, API redirects to presigned URLs |
| Job execution | background thread polling Polly | Step Functions state machine |

The state machine is start, wait, check, choice: a start Lambda chunks the text and fires the Polly tasks, a check Lambda polls them on each loop and writes per-chunk progress to the job row, and a finalize Lambda downloads the pieces, joins them with ffmpeg, uploads the final MP3 to the media bucket, and completes the job. The failure path updates the job row through the Step Functions native DynamoDB integration, no Lambda needed. The API itself runs as FastAPI wrapped in Mangum.

What does not change is the API contract. Endpoints, request and response shapes, and the polling model stay identical, the frontend cannot tell which runtime answered. The business core, chunking, Polly calls, the ffmpeg join, stays one shared module that both the local thread and the worker Lambdas call.

Resource names and locations are Terraform's business. The backend receives the table name, bucket names, and state machine ARN through environment variables and hardcodes nothing.

## Consequences

Local development keeps working offline with a file database and a media folder, and the deployed app pays only for what it does. The price is two implementations per seam, which the thin repository interface has to keep honest, and DynamoDB modelling by access pattern instead of SQL. Audio serving becomes a redirect in the aws runtime, which browsers follow transparently. Uploads through API Gateway are base64-encoded, which turns the 10 MB limit into roughly 7.5 MB effective, acceptable for text documents.

ADRs 0005 and 0006 remain accurate for the local runtime. Their cloud-side halves, SQLite as the only store and in-process jobs as the only executor, are superseded by this record.
