# OEPL M4 Tag RF Acceptance Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record source-backed radio/tag compatibility, define passive dashboard observability, and prepare a gated first-tag RF acceptance report without touching hardware state.

**Architecture:** Keep M4 as a documentation and offline-validation layer in the PAC repository. Source evidence is cited by path and symbol; CI tests validate the report contract without depending on the developer's sibling OEPL checkout.

**Tech Stack:** Markdown, Python/pytest contract tests, existing PAC Ruff/mypy/pytest gates, GitHub Draft PR workflow.

## Global Constraints

- No serial access, GPIO changes, reset, flashing, RF self-test, HTTP POST, tag pairing, image upload, or network/firewall changes.
- Do not claim `REAL_RF_PASS` without a fresh real-tag event and display evidence.
- Treat exact Hanshow/Hanshuo 2.66-inch model and firmware as unknown until identified.
- Preserve existing untracked diagnostic captures.

## Tasks

- [ ] Audit C6 IEEE 802.15.4, optional CC1101, tag types, flasher requirements, and WebSocket event sources.
- [ ] Add `m4-acceptance-report.md` with a compatibility matrix, passive observability contract, and gated repeatable test procedure.
- [ ] Add an offline report-contract test that prevents accidental RF PASS claims and external checkout dependencies.
- [ ] Run Ruff, mypy, pytest, and the existing offline safety checks; review for secrets and unrelated changes.
- [ ] Commit and push only the M4 software/report changes to the existing branch; verify Draft PR CI.

## Review Focus

- Separate source-supported tag type definitions from proven compatibility of an unidentified second-hand tag.
- Distinguish broadcast-only WebSocket observation from commands that mutate AP/tag state.
- Keep all unresolved hardware and exact-firmware questions explicit.
