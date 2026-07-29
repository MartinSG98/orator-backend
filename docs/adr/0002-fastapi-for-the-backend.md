# 0002. FastAPI for the backend

Date: 2026-07-29
Status: Accepted

## Context

The core of this app is AWS orchestration: Translate, Polly, and S3. Proven Python boto3 logic for all three already existed in the predecessor CLI. The realistic options were a Python API that reuses that logic, or a TypeScript backend (Express or Next.js API routes) that reimplements it against the JS SDK.

## Decision

FastAPI on uvicorn, with pydantic-settings for configuration.

## Consequences

The chunking, polling, and concatenation logic ports over nearly unchanged instead of being rewritten and re-debugged in a second SDK. FastAPI generates an OpenAPI schema and interactive docs for free, which the frontend work can lean on. The project ends up with two languages, Python for the API and TypeScript for the frontend, which is a fair trade for not rewriting working AWS code.
