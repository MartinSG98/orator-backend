# 0004. Dynamic language and voice discovery

Date: 2026-07-29
Status: Accepted

## Context

The predecessor CLI had a hand-written table of six languages, each with one female and one male voice. Reality is bigger and messier. Polly offers around forty language variants in eu-west-1, availability differs per region and per engine, gender pairs are not guaranteed (Arabic has a single female voice, some languages have four voices), and AWS keeps adding voices. A static table is wrong the day it is written and drifts further after that.

The alternative considered was expanding the table by hand to the full current intersection of Polly voices and Translate targets, which trades one stale table for a much bigger stale table.

## Decision

Discover at runtime. The backend calls Polly `describe_voices` for the configured region, intersects the result with the languages from Translate `list_languages`, and serves that as the catalog. Voices are modelled as what they are, a list per language with id, display name, gender, and engine, preferring neural and falling back to standard. A small overrides map handles the language codes the two services spell differently.

Successful discovery is cached in memory for 24 hours. Failure is retried at most every 5 minutes, serving the last good catalog in between, or a built-in six-language fallback if there has never been one.

## Consequences

Coverage is region-accurate with zero table maintenance, and new AWS voices appear on their own. The frontend gets real voice names to show instead of two gender buttons. The live path needs credentials with `polly:DescribeVoices` and `translate:ListLanguages`, and without any credentials the app still runs on the fallback catalog, so local development needs no AWS setup. AWS traffic is two read-only calls per day per process.
