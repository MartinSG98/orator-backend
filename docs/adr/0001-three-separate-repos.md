# 0001. Three separate repos

Date: 2026-07-29
Status: Accepted

## Context

Orator replaces a single-folder CLI tool with a proper application: an HTTP API, a web frontend, and AWS infrastructure. That could live in one monorepo or in separate repos per concern.

## Decision

Three repos. `orator-backend` holds the FastAPI API, `orator-frontend` holds the React app, and a third repo will hold the Terraform module for the AWS resources.

## Consequences

Each piece has its own history, its own releases, and can be understood on its own, which suits a portfolio where each repo should stand alone. The cost is coordination: the frontend and backend evolve against each other's APIs without a shared commit, and changes that span both need matching pull requests. Because the two run on different origins, the backend has to care about CORS from day one.
