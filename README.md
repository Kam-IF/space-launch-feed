# space-launch-feed

A static JSON feed of upcoming rocket launches for the **LaunchClock** app.

Every 15 minutes a GitHub Actions workflow fetches upcoming launches from
[The Space Devs Launch Library 2](https://thespacedevs.com/llapi) (LL2), adds the
countries involved in each launch, validates the result against a JSON Schema, and
publishes it to GitHub Pages as static JSON. The app reads only this feed and never
calls LL2 directly. If a run fails at any step, nothing is deployed and the previous
feed stays online.

## Feed

- URL: `https://<owner>.github.io/space-launch-feed/v1/launches.json` (`<owner>` is the owner of this repository)
- Schema: [`schema/launches.v1.schema.json`](schema/launches.v1.schema.json) (top-level fields: `schemaVersion`, `generatedAt`, `source`, `launches`)
- Times are UTC (ISO 8601). Each launch carries related country codes (ISO 3166-1 alpha-2) per role: `launchSite`, `launchProvider`, `payloadOperator`.
- The site root (`index.html`) only links to the feed.

### Compatibility rules

- Adding optional fields and adding enum values keep `schemaVersion` 1. Consumers must ignore unknown fields and treat unknown enum values as `unknown`.
- Removing a field, changing a type, or changing a meaning requires `schemaVersion` 2 and a new `/v2/` path. `/v1/` is then published in parallel for a transition period.

### Freshness and caching

The workflow runs every 15 minutes (cron `7,22,37,52 * * * *`), but GitHub may delay scheduled runs by several minutes. GitHub Pages serves files with `Cache-Control: max-age=600`. In practice the feed can be up to about 25-30 minutes old. Consumers should check `generatedAt` rather than assume the feed is fresh.

## Data source and attribution

Launch data comes from The Space Devs / Launch Library 2 (<https://thespacedevs.com/llapi>).
Summary of their terms, based on the
[Space Devs FAQ](https://github.com/TheSpaceDevs/Tutorials/blob/main/faqs/faq_TSD.md)
(read the original for the authoritative wording):

- The data may be used freely.
- They ask that you do not just relay the data without adding value.
- Attribution is encouraged.
- Caching is recommended.
- Commercial use is not addressed explicitly.
- Accuracy is not guaranteed.

This feed does not include images. The MIT license of this repository covers the code
only; it does not extend to the data, which remains subject to The Space Devs' terms.

## Local development

Requires **Python 3.12**.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Build the feed without network access, from saved LL2 responses:

```bash
.venv/bin/python -m launch_feed build --out dist --input-dir tests/fixtures/run
```

This writes `dist/v1/launches.json` and `dist/index.html`. `--input-dir` reads
`upcoming_p1.json`, `upcoming_p2.json`, ... from the given directory.

Build from the LL2 development endpoint (uses the network and the LL2 rate limit):

```bash
.venv/bin/python -m launch_feed build --out dist --endpoint dev
```

### Options

| Option | Description |
|--------|-------------|
| `--out DIR` | Output directory (required). |
| `--endpoint prod\|dev` | LL2 endpoint: `prod` (`https://ll.thespacedevs.com/2.3.0`, default) or `dev` (`https://lldev.thespacedevs.com/2.3.0`). |
| `--input-dir DIR` | Read `upcoming_pN.json` files instead of using the network. |
| `--repo-root DIR` | Directory containing `schema/` and `config/` (default: current directory). |

### Environment variables

| Variable | Description |
|----------|-------------|
| `LL2_BASE_URL` | Overrides the base URL chosen by `--endpoint`. |
| `LL2_API_KEY` | Optional LL2 API key, sent as `Authorization: Token <key>`. Never printed. |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. |
| 2 | Configuration or argument error (missing or unreadable schema/config/input directory, invalid overrides file, bad arguments). |
| 3 | Rate limited by LL2 (HTTP 429). |
| 4 | Upstream error (timeout, connection failure, 5xx, non-JSON response, missing input page). |
| 5 | The generated feed failed schema validation (indicates a bug in the transform). |
| 6 | No valid launches to publish. |
| 1 | Unexpected error (stack trace in logs, one-line reason on stderr). |

On any non-zero exit nothing is written to the output directory that would replace a published feed.

## Country overrides

Countries come from LL2 by default. When LL2 has wrong or missing country data, correct it in
[`config/country_overrides.json`](config/country_overrides.json):

```json
{
  "agencies": { "123": ["US", "JP"] },
  "locations": { "45": ["KZ"] }
}
```

- Both `agencies` and `locations` are required (use `{}` when empty).
- Keys are LL2 numeric IDs; values are arrays of ISO 3166-1 alpha-2 codes (case is normalized to upper case; invalid codes make the build fail with exit code 2).
- `agencies` replaces the countries of an agency, for both the launch provider and the payload operators (mission agencies).
- `locations` replaces the launch site country for that LL2 location (the pad's location).
- An override replaces the value from LL2 entirely; it is not merged.

## Operator runbook

1. Create a **public** GitHub repository named `space-launch-feed` and push this repository to it.
2. In Settings -> Pages, set Source to **GitHub Actions**.
3. Optional: add a repository secret `LL2_API_KEY` (Settings -> Secrets and variables -> Actions). Keys are obtained through The Space Devs' Patreon and are sent as `Authorization: Token <key>`. Without a key the free rate limit applies.
4. Run the `publish-feed` workflow manually from the Actions tab (Run workflow, endpoint `prod`), then open `https://<owner>.github.io/space-launch-feed/v1/launches.json` to confirm it is served.
5. Enable failure notifications for GitHub Actions in your own notification settings (Settings -> Notifications -> Actions). Notifications for scheduled runs go to the user who created the workflow, or the user who last changed or re-enabled its cron schedule (see [GitHub's documentation](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)), so check that this is you.
6. **Required:** every 50 days, check in the Actions tab that the schedule is still running, and re-enable the workflow if it has stopped. GitHub disables scheduled workflows in a public repository after 60 days without repository activity. The `keepalive` job runs `gh workflow enable publish-feed.yml` on each scheduled run, but GitHub does not document that this resets the timer, so this manual check is mandatory. If `keepalive` fails, the run summary shows a `keepalive failed` warning annotation (the rest of the workflow is not affected); investigate when you see it.
7. If runs fail with HTTP 429 (exit code 3) frequently, obtain an API key and add it as `LL2_API_KEY` (step 3). Run the workflow manually (workflow_dispatch) at most once per hour: scheduled runs (4 per hour) use up to 3 requests each, i.e. 12, and the free tier allows 15 requests per hour per IP.
8. Before monetizing the app, contact The Space Devs (support@thespacedevs.com) and ask whether commercial use and redistribution of the data is permitted. If redistribution is not allowed, stop publishing by disabling the `publish-feed` workflow (Actions -> publish-feed -> Disable workflow).
9. GitHub Pages has a soft bandwidth limit of 100 GB per month. The feed is about 450 KB uncompressed (roughly 90 KB gzipped) at the 300-launch maximum, so one fetch per day from 10,000 users is about 27 GB per month. If usage grows beyond that, consider a CDN or moving hosting elsewhere.

Dependabot (`.github/dependabot.yml`) proposes weekly updates for GitHub Actions and pip dependencies.

## License

Code: MIT (see [LICENSE](LICENSE)). The data is not covered by this license; see
[Data source and attribution](#data-source-and-attribution).
