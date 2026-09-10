---
title: "Orchard"
description: "A recruiter's candidate pipeline with templated, multi-inbox outreach and email open-tracking"
thumbnail: "template.svg"
version: v1
format: v2
---

# Orchard

This file is the manifest for the **Orchard** template (slug:
`orchard`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Orchard is a recruiter's outreach workbench: a single-page app for running
cold-outreach campaigns end to end. It solves the "I have a list of people to
reach out to, help me contact them and track what happens" problem. The user
opens one board that shows a candidate table with a three-stage pipeline (not
contacted -> reached out -> replied), a compose panel with a live email
preview, a writing-voice panel, a settings panel, and open notifications.

From that board the recruiter adds candidates, writes reusable email templates
whose `{{first_name}}` / `{{company}}` / `{{role}}` / `{{sender_name}}` tokens
are filled in per candidate, and sends the message through the Gmail API from
one of three sending inboxes (their own connected mailbox plus two teammate
aliases). Several assists are built in: Claude can draft a template in a chosen
sender's writing "voice" from pasted samples, draft a first outreach or
follow-up directly, or generate a one-line "why I'm reaching out" from a
candidate's links; ContactOut can look up a candidate's email from their
LinkedIn URL. Every sent email carries an invisible 1x1 tracking pixel served
over a public Cloudflare tunnel, so the app records when a candidate opens it
and raises a notification; it also detects replies and folds in follow-ups the
recruiter sent from their own mail client, keeping each candidate's send
history complete. On first open, with no candidates yet, the board shows a
plain "your orchard is empty -- add someone to start tracking your outreach"
state rather than an error.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/orchard`
- `system/scripts/env.d/2000-orchard-cloudflared.sh`
- `system/supervisord.conf` (only its `orchard` / `orchard-pixel` / `orchard-tunnel`
  program blocks; any other program blocks the source workspace had accumulated
  from unrelated apps are not part of this recipe)

`system/apps/orchard` is the whole app -- a self-contained uv package named
`orchard` exposing three console entry points:

- `runner.py` -- the Flask app that serves the single-page UI (`assets/app.html`)
  and the JSON API the page calls.
- `store.py` -- a JSON file store under `data/.apps/orchard/`: candidates,
  templates, sending inboxes, per-inbox voice samples, pitch ideas, settings,
  notifications, the design/branding layer, and the ContactOut token. Writes
  are atomic and every read recovers to a seeded default on a missing or
  corrupt file, so a fresh adopter's first load never errors.
- `service.py` -- the orchestration seam the routes call: token rendering,
  message assembly, sending, and open/reply bookkeeping (pure, offline-testable
  functions with the network layered on top).
- `gmail.py` + `latchkey_client.py` -- send and read mail through the Gmail API
  by shelling out to `latchkey curl` (latchkey injects the connected account's
  Google credentials; no token is ever stored in the app itself).
- `contactout.py` -- calls the ContactOut HTTP API directly (not a
  latchkey-supported service) with the user's own token to resolve a LinkedIn
  URL to email addresses.
- `ai.py` -- shells out to headless `claude -p` for the voice-drafting,
  auto-draft, and personalization completions.
- `pixel_runner.py` -- the standalone public tracking-pixel service (serves only
  the 1x1 GIF and a health check; a pixel hit is enqueued, never written to
  candidate data directly, so the public-facing process has no access to the
  pipeline).
- `tunnel_runner.py` -- opens a Cloudflare quick tunnel to the pixel service and
  writes the discovered public URL into the app's `public_base_url` setting.

`system/supervisord.conf` supplies the process wiring -- three programs added
mid-file on top of the clean template base, all shipped `autostart=false`
(this app needs Gmail write access and a real candidate list to be useful; the
adopting agent flips these to `true` once those are set up -- see
"Requirements" below):

- `orchard` -- the main app, bound to `localhost:8082` (`ORCHARD_PORT`), and
  registered as a tab via
  `system/scripts/forward_port.py --url http://localhost:8082 --name orchard --icon-file system/apps/orchard/icon.svg --program orchard`.
- `orchard-pixel` -- the pixel service, bound to `localhost:8083`
  (`ORCHARD_PIXEL_PORT`); deliberately NOT registered as a tab (no
  `forward_port.py` call), the same way this base's `chat-lab-control` program
  has no tab of its own.
- `orchard-tunnel` -- runs `cloudflared tunnel --url http://localhost:8083`,
  parses the `*.trycloudflare.com` URL from cloudflared's output, and saves it
  as `public_base_url`, so mail sent afterward embeds a pixel that resolves
  publicly. Only the pixel port is exposed; the main app and candidate data
  stay private. `cloudflared` is not apt-installable in the pinned snapshot
  mirror, so `system/scripts/env.d/2000-orchard-cloudflared.sh` fetches the
  pinned binary from Cloudflare's own GitHub releases on next boot after this
  template is adopted (same shape as this base's existing Fortress browser
  unit). Without it, or with this program stopped, mail still sends -- just
  without a tracking pixel; `orchard-tunnel` exits cleanly (not a crash-loop)
  if `cloudflared` isn't on `PATH` yet.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

- requires_permission: google-gmail-api / google-gmail-send-messages (user-approved; the adopting agent initiates this via a latchkey permission request during setup -- the send path POSTs to Gmail `users/me/messages/send`, so without it nothing sends)
- requires_permission: google-gmail-api / google-gmail-read-all (user-approved; needed to fill the "you" inbox from the connected mailbox, verify which addresses are allowed "send mail as", detect replies, and fold in follow-ups sent from the user's own client -- prefer the granular `google-gmail-read-messages` + `google-gmail-read-settings` + `google-gmail-read-profile` if you want least privilege)
- requires_secret: ContactOut API token (OPTIONAL -- the app runs fully without it; only the "Find email" LinkedIn-to-email lookup needs it. Set it in the app's Settings panel; it is stored server-side at `data/.apps/orchard/contactout.json` and never returned to the client)
- requires_llm: calls Claude via the KEYLESS subscription path -- headless `claude -p --output-format json`, default model `claude-sonnet-5` -- for voice-drafted templates, auto-drafted outreach, and the "why reach out" personalization. An adopter whose workspace uses the KEYED litellm path (`ANTHROPIC_API_KEY`) must switch these calls per the use-ai-integration skill (the path is hardcoded in `ai.py`; see the adaptation bullets below).

Open-tracking reaches recipients through the `orchard-tunnel` program, which
opens a Cloudflare **quick** tunnel to the pixel service. A quick tunnel needs
no Cloudflare login or account and thus no user grant -- the tunnel URL is
discovered at boot and self-registered into the app's `public_base_url`. The
only requirement is that `cloudflared` (fetched automatically by this
template's env.d unit) can reach the internet and the `orchard-tunnel`
program is running; if it is not, mail still sends but without a tracking
pixel.

Adaptation -- worked through interactively with the user, after activation:

- **The three supervisord programs ship `autostart=false`.** This app needs
  Gmail write access and a real candidate list to be useful; flip
  `orchard`, `orchard-pixel`, and `orchard-tunnel` to `autostart=true` in
  `system/supervisord.conf` (and `supervisorctl reread && supervisorctl
  update`) once activation is done, rather than leaving the user to find a
  dead tab.
- **Sender identities are generic examples.** The three inboxes ship as "you"
  (blank address, filled from Settings by the connected mailbox), "Alex"
  <alex@example.com>, and "Sam" <sam@example.com>. Replace the two example
  teammates with the user's real colleagues -- their display names and their
  verified Gmail "send mail as" addresses. Keep the internal inbox keys
  (`you` / `josh` / `kj`) and their CSS accent variables (`--you` / `--josh` /
  `--kj`) exactly as they are; only names/addresses change. The hardcoded
  verification-address list in `assets/app.html` (~line 1138) must be updated
  to the same two addresses so the Settings "check mail connection" readout is
  accurate.
- **Open-tracking depends on the public pixel tunnel being reachable.** If
  `orchard-tunnel`/`cloudflared` is not running or `public_base_url` is empty,
  mail still sends but carries no pixel, so opens are never recorded. The
  quick tunnel's URL rotates on every restart, so pixels already delivered in
  earlier mail stop recording once the old URL is gone -- there is no stable
  custom domain. An adopter who wants durable tracking would swap the quick
  tunnel for a named tunnel on their own domain.
- **The AI path is hardcoded to keyless `claude -p`.** `ai.py` was built for
  the keyless subscription path. On a workspace that uses the keyed litellm /
  `ANTHROPIC_API_KEY` path, re-point the `ai.complete` / `ai.research` calls
  per the use-ai-integration skill.
- **Seed content is illustrative filler.** The two default templates and the
  two "you" voice samples exist so the app demonstrates itself on first boot;
  the adopter should replace them with their own templates and paste real
  writing samples so voice drafts sound like the actual sender.
- **Reply/open bookkeeping only sees the connected mailbox.** Replies to, and
  follow-ups sent from, a teammate alias's own Google account are invisible to
  reply detection and the off-platform send sync -- only the connected "you"
  mailbox is read.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

`cloudflared`: the `orchard-tunnel` program shells out to it to open the
public quick tunnel the email tracking pixel is served through. It is not in
the pinned apt snapshot mirror, so it ships as a carried env.d unit
(`system/scripts/env.d/2000-orchard-cloudflared.sh`) rather than an `apt`
entry: the unit downloads the pinned `cloudflared` binary from Cloudflare's
own GitHub releases (checksummed, per-architecture) on the adopting mind's
next boot after this template lands, the same way this base's own browser
stack (Fortress) installs itself. Nothing else this app uses needs installing
beyond the stock workspace environment (Flask, Pydantic, and Werkzeug all come
from the app's own `pyproject.toml`, picked up automatically).

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-09-09) -- First published snapshot of Orchard on this fork: the full recruiting-outreach app (pipeline board, templated multi-inbox Gmail send, AI voice-drafting and auto-draft, ContactOut email lookup, and Cloudflare-tunnel email open-tracking), re-cut from mangonomnom/orchard onto this mind's current base with a fixed frontend loading-state label (the placeholder inbox names now match the seeded Alex/Sam identities).

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
