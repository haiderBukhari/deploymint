# Chat assistant

A natural-language box that routes plain English into the same pipeline
every other interface uses — it doesn't have its own separate logic for
"what a deploy is."

## What works today

- **"deploy my-app"** (or "ship my-app", "launch my-app") — triggers a real
  deploy of the named project, the same as clicking Deploy on its page. If
  the classifier isn't confident which project you mean, it asks you to
  confirm before doing anything.
- **"analyze my-app"** (or "scan my-app", "look at my-app") — reports the
  project's detected language/framework and points you at a full
  re-analysis.
- **"help"** — a reminder of what it can do.

If you don't name a project, it asks which one rather than guessing.

## How it classifies your message

An LLM call classifies the message into an intent (with a deterministic
keyword fallback if the LLM is unavailable — the same resilience pattern
Artifact Smith uses), then the same project-resolution logic every other
route uses looks up which project you meant.

## What's not wired up yet

Status, cost, and rollback queries are recognized as intents but not yet
connected to real data — asking about them tells you plainly that they
aren't available rather than guessing an answer. Check a run's own page or
the Costs page directly for those in the meantime.
