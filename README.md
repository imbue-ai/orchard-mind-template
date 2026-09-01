# Orchard

A recruiter's candidate pipeline with templated, multi-inbox outreach and email open-tracking.

Orchard is a recruiter's outreach workbench -- a single-page app for running
cold-outreach campaigns end to end. It tracks candidates through a three-stage
pipeline (not contacted -> reached out -> replied) and sends personalized,
templated emails through the Gmail API from any of several sending inboxes, with
per-candidate `{{first_name}}` / `{{company}}` / `{{role}}` tokens filled in
automatically. Claude can draft a message in a chosen sender's writing "voice"
or generate a one-line "why I'm reaching out," ContactOut can look up a
candidate's email from their LinkedIn URL, and every sent email carries an
invisible tracking pixel (served over a public Cloudflare tunnel) so the app
shows opens, detects replies, and keeps each candidate's send history complete.

This repository is a published **minds inspiration**: a clean, bootable
snapshot of the apps and features a mind built, ready to adapt into your own.
It is NOT the generic workspace template -- it is this specific project.

## Use it

- **Create a new mind from it:** point a new minds workspace at this repo's
  URL. On first boot the mind reads the inspiration and helps you connect your
  own accounts and adapt it.
- **Bring it into an existing mind:** run `/use-inspiration <this repo's URL>`.

## What it needs to run

The mind that adopts this wires these up for you during setup, but if you are a
developer standing the repo up directly, this is what Orchard connects to (the
full, machine-readable list is under "Prerequisites" in the manifest):

- **Gmail (required)** -- sending and reading mail goes through latchkey's
  Google Gmail API scope (`google-gmail-api`): `google-gmail-send-messages` to
  send, plus read access (`google-gmail-read-all`, or the granular
  messages/settings/profile permissions) for reply detection, "send mail as"
  verification, and filling the "you" inbox from the connected mailbox.
- **ContactOut (optional)** -- an API token, set in the app's Settings, powers
  only the "Find email" LinkedIn-to-email lookup. The app runs fully without it.
- **Claude (required for the AI assists)** -- voice-drafting and personalization
  call Claude via the keyless `claude -p` subscription path (default model
  `claude-sonnet-5`). On a keyed `ANTHROPIC_API_KEY` workspace, switch these
  calls per the use-ai-integration guidance.
- **cloudflared (for open-tracking)** -- the `orchard-tunnel` service opens a
  Cloudflare quick tunnel (no login/account required) so the tracking pixel is
  reachable from recipients' inboxes.

## What's inside

- **Orchard** -- [`inspiration-orchard.md`](inspiration-orchard.md) (published now)

Each `inspiration-<slug>.md` is the full manifest for that inspiration: what
it is, how it works, the prerequisites it needs, and how to adapt it.
