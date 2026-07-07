# Security

## Reporting

Found a vulnerability? Email **luisassardo@gmail.com**. Please do not open a
public issue for sensitive reports.

## Handling of secrets

- The Anthropic API key is read from the `ANTHROPIC_API_KEY` environment
  variable and is **never** committed. On Railway/Render it must be set as a
  secret in the host dashboard.
- The app works without a key (local keyword fallback), so deployments that
  cannot hold a secret degrade gracefully instead of failing.

## Cost / abuse controls

The AI endpoint is public, so spend is bounded on several layers (see README
"Costs"): a per-IP rate limit (`/api/suggest`, 3/min · 10/hour → HTTP 429), an
in-process daily budget (`AI_DAILY_LIMIT`) that degrades to the free local engine
when spent, and an input-size cap. The **hard backstop** is the monthly spend
limit you set in the Anthropic console, which no request path can exceed.

## Data handling

- The tagger is **stateless** in v0.1: campaign descriptions are sent to the
  `/api/suggest` endpoint, used for the request, and not persisted server-side.
- When the AI path is enabled, the campaign description is sent to the Anthropic
  API for processing. Reporters handling sensitive material should be aware of
  this; with no key set, no third party receives the text.
- No accounts, no logging of submitted descriptions to disk.
