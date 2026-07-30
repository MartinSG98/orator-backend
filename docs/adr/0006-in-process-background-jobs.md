# 0006. In-process background jobs

Date: 2026-07-30
Status: Accepted

## Context

Synthesis is slow. Polly tasks are asynchronous on the AWS side and get polled every few seconds, so a job for a long document can run for minutes. That cannot happen inside a request. The standard answer is a task queue, Celery or RQ with a Redis broker, which brings a second process and a third service to run, monitor, and deploy.

## Decision

FastAPI's own BackgroundTasks. The POST returns 202 immediately with a job id, the work runs in the server's threadpool, and progress lands in the job's database row, chunk by chunk, where the status endpoint reads it.

## Consequences

Nothing new to deploy or operate, which fits a single-operator app. The honest limits: jobs die silently if the process dies mid-run, leaving a row stuck on "running", and a flood of concurrent jobs would compete with request handling for threads. Both are acceptable for one user pressing one button, and both point at the same escape hatch if the app ever outgrows this, a real queue behind the same job table and endpoints, with no API change needed.
