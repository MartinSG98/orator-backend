# 0003. Async Polly with S3 staging

Date: 2026-07-29
Status: Accepted

## Context

Polly offers two synthesis APIs. The synchronous `synthesize_speech` call streams audio straight back but caps input at 3000 characters, so long documents need chunking and in-process buffering. The asynchronous `start_speech_synthesis_task` call writes its result to an S3 bucket and is built for exactly this kind of longer job, at the cost of requiring that bucket to exist and polling for task completion.

Going synchronous would have removed the S3 requirement entirely and made the project trivial to set up. This was considered seriously.

## Decision

The async task API with an S3 staging bucket, configured through `ORATOR_S3_BUCKET`.

## Consequences

Audio lands in S3 instead of being buffered inside the API process, task status is queryable on the AWS side, and the backend's job tracking maps naturally onto Polly's own task lifecycle. Setup is heavier: the bucket must exist, IAM needs the Polly task actions plus S3 access scoped to that bucket, and the backend polls tasks to completion before concatenating chunks and cleaning up the staged objects.

Synthesis is not implemented yet. This record exists so the choice and its rejected alternative are written down before that code lands.
