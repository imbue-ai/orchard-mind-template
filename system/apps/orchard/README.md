# orchard

A recruiter's candidate pipeline: track candidates (name, LinkedIn, email,
company/role), write reusable email templates with `{{tokens}}`, and send
personalized, open-tracked email in one click from any of three inboxes (the
recruiter's own plus the two founder aliases).

Served at `/service/orchard/`. State lives under
`data/.apps/orchard/` (candidates, templates, inbox config, settings,
notifications). Mail is sent through the Gmail API via latchkey; opens are
detected with an invisible tracking pixel served from the app's public URL.
