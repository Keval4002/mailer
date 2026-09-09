# Apollo Usage Notes

## Default Rule

In this repo, prefer direct Apollo HTTP API calls from runtime scripts.

- Read `APOLLO_API_KEY` from `.env`
- Do not hardcode the key
- Do not rely on the Apollo MCP for normal repo flows when direct API access is available

## Best Endpoint Split

Use Apollo in 2 phases:

1. discovery / shortlist
2. enrichment / reveal

### Discovery

Use:

- `POST https://api.apollo.io/api/v1/mixed_people/api_search`

Good filters:

- `q_organization_domains_list`
- `person_titles`
- `include_similar_titles=true`
- `q_keywords` for broader fallback exploration

This endpoint is good for building a shortlist before spending credits.

What it typically returns:

- Apollo person `id`
- `first_name`
- `last_name_obfuscated`
- `title`
- `has_email`
- limited org / location hints

Important limitation:

- it often does **not** return the final revealed email
- it often does **not** return the full last name
- it may not return `linkedin_url`

So treat `mixed_people/api_search` as shortlist-only.

### Enrichment

Use:

- `POST https://api.apollo.io/api/v1/people/match`

Best input when coming from search:

- Apollo person `id`

This is the reliable step for:

- full name
- exact work email
- `linkedin_url`
- normalized title
- `email_status`

## Deprecated Endpoint Note

For API callers, do **not** use:

- `mixed_people/search`

Apollo returns a deprecation error and points API callers to:

- `mixed_people/api_search`

## Credit-Saving Workflow

Use this order:

1. search with `mixed_people/api_search`
2. keep only people with `has_email=true` when possible
3. show the shortlist to the user
4. enrich only the approved people with `people/match`

This keeps credit usage intentional.

## Practical Outreach Priorities

For fresher / early-career SWE outreach, a good default priority is:

1. `Talent Acquisition` / `Talent Partner` / `Recruiter`
2. `Manager, Talent Acquisition` / `Associate - Talent Acquisition`
3. `Engineering Manager`
4. very senior TA or engineering leadership only as a later wave

Reason:

- campus / early-career titles are ideal but often sparse
- practical recruiting titles usually work better than waiting for perfect title matches
- engineering managers are useful fallback hiring-manager targets
- very senior leadership is often lower-yield for first outreach

## After Enrichment

Always:

- dedupe by normalized email
- skip people already mailed if the server contact history shows prior sends

Recommended duplicate check:

- `GET /api/contacts?email=...`
- inspect `sent_count`

If `sent_count > 0`, skip unless the user explicitly wants another thread.

## Scheduler Projection Rule

Apollo rows can be rich locally, but only supported scheduler fields should be posted.

Important supported fields to keep when available:

- `recipient_email`
- `recipient_name`
- `company`
- `title`
- `linkedin_url`

Important learning:

- if `linkedin_url` is omitted from the scheduler payload, the contact can still be created, but the stored server contact row will miss that metadata
- if `linkedin_url` is included, the backend persists it on the contact row and `GET /api/mail-jobs` exposes it as `recipient_linkedin_url`

## Server Contact Behavior

The backend deduplicates contacts by normalized email.

Implications:

- canonical work email matters more than Apollo person id at scheduler time
- enriching multiple Apollo rows that map to the same email should collapse to one send target

## Recommended Batch Pattern

For multi-person outreach sourced from Apollo:

1. shortlist with Apollo search
2. enrich approved people
3. dedupe by email
4. skip already-sent contacts using server contact history
5. preflight resume with `GET /api/files`
6. schedule one bulk same-thread sequence
7. stagger root sends slightly, for example `2` minutes apart
8. verify with `GET /api/mail-jobs?batch_name=...`

## Example Direct API Snippets

### Search

```python
import os
import requests

headers = {
    "Cache-Control": "no-cache",
    "Content-Type": "application/json",
    "accept": "application/json",
    "x-api-key": os.environ["APOLLO_API_KEY"],
}

payload = {
    "q_organization_domains_list": ["razorpay.com"],
    "person_titles": ["talent acquisition", "technical recruiter", "engineering manager"],
    "include_similar_titles": True,
    "per_page": 25,
    "page": 1,
}

resp = requests.post(
    "https://api.apollo.io/api/v1/mixed_people/api_search",
    headers=headers,
    json=payload,
    timeout=45,
)
resp.raise_for_status()
people = resp.json().get("people", [])
```

### Enrich One Person By Apollo ID

```python
resp = requests.post(
    "https://api.apollo.io/api/v1/people/match",
    headers=headers,
    json={"id": apollo_person_id},
    timeout=45,
)
resp.raise_for_status()
person = resp.json().get("person")
```
