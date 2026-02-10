# OrbitRisk (MVP) - Conjunction Triage That Ops Can Trust

## Who it's for

Small LEO satellite operators who need a fast, explainable way to triage conjunctions and keep a defensible record of what changed and why.

## The problem we solve

Conjunction operations create "alert fatigue": many events, frequent message updates, and inconsistent internal recordkeeping.
Teams still need an internal place to:

- Deduplicate alerts into a single event timeline
- Compare updates across time (and across sources)
- Log decisions and export reports
- Keep sensitive ephemerides and operational notes inside the operator environment

## What OrbitRisk does (today)

- Self-hosted web app for conjunction triage and decision support
- "One event, many updates" history (miss distance, tier, confidence, drivers)
- Screening index + qualitative tier (Low/Watch/High) and explicit confidence (A-D)
- CCSDS CDM (KVN) attach workflow and a CDM inbox that can auto-create/dedupe events
- Frame-consistent internal computations (canonical inertial frame)
- Audit trail + PDF export
- Webhooks for integrations (alerts into ops tools)

## What it explicitly does NOT do

- No probability of collision (Pc)
- No autonomous maneuver recommendations
- No orbit determination / OD

## Why it's different

- Operator-first: readable, explainable, and built for tired humans at 2 AM
- Self-hosted: keep operational data internal
- Source-agnostic, versioned states: future-ready for ephemerides/covariance without refactoring

## Typical sales motion (solo founder friendly)

1. 15-minute demo: seed data, paste a CDM, show history + PDF.
2. Pilot: run in a staging environment and connect webhooks to Slack/Teams/Jira.
3. Production: deploy with customer-owned SQLite/Postgres and backup procedures.

## Packaging suggestions

- Single-tenant annual license + support
- Optional "integration pack": webhook transforms, custom CDM variants, and data retention policies

