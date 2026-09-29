# WISeREP monthly scrape

Offline operator tooling. It pulls the public supernova spectra WISeREP
ingested in a date window and writes them as a monthly challenge dataset.
Nothing in the request path imports it; the web app never runs it.

## Running it

It needs the app's Python environment, so run it in the container:

```bash
docker compose <compose args> exec app_dev python wiserep_scrape/wiserep_monthly_scrape.py \
  --start 2026-07-01 \
  --end   2026-07-31 \
  --output /mnt/astrodash-data/wiserep_challenge/2026-07
```

The search uses spectrum Creation Date (UT), accepts WISeREP's zip-of-CSV
export as well as bare CSV and HTML, skips residual-0 duplicate uploads, and
is idempotent: a re-run lands on the same filenames and re-downloads nothing
unless `--overwrite` is given.

`--delay` defaults to one second between requests. This runs once a month, so
there is no reason to go faster.

## Where a dataset lives

Not in the repository. `.gitignore` excludes `app/wiserep_scrape/data/` so a
local run cannot commit a dataset by accident.

Locally, point `--output` at the mount, which is where the leaderboard's
scoring step expects to find a month:

```
{ASTRODASH_DATA_DIR}/wiserep_challenge/<YYYY-MM>/
    metadata.csv       iau,filename,type,redshift
    snapshot.json      when this dataset was collected, and its counts
    spectra/           the ASCII spectra metadata.csv names
```

`ASTRODASH_DATA_DIR` is `/mnt/astrodash-data` by default.

### Datasets are reproducible, and say when they were collected

Re-running the same commands months later returns the same dataset. Repeating
all five 2026 challenge months in September that were first collected in August
produced byte-identical `metadata.csv` files and the same spectra.

The search phase is a different matter, and it is easy to misread. A search for
a closed window returns more rows over time as people upload older
observations -- June 2026 went from 322 to 390 candidate rows between those two
runs. Those extra candidates are then filtered out: non-SN classifications,
residual-0 duplicate uploads, and entries with no ASCII spectrum to download.
So a growing candidate count does not mean a changed dataset, and comparing
search counts across runs will suggest drift that the output does not have.

What was missing was provenance. Nothing in a dataset recorded when it was
collected, so two copies could not be told apart and a score file could not say
which collection produced it. Each scrape now writes a `snapshot.json` beside
`metadata.csv`:

```json
{
  "scraped_at": "2026-09-25T16:00:00+00:00",
  "window_start": "2026-07-01",
  "window_end": "2026-07-31",
  "spectra": 395,
  "objects": 230
}
```

The scoring step copies `scraped_at` into the score file and the page shows it
beside the month's status, so standings are attributable to a collection rather
than to an unrecorded moment.

Re-collecting a month is safe because the result is reproducible, but there is
no reason to: prefer the published copy in the bucket, which is also the one
the recorded date refers to.

### Publishing a month

Datasets live in the same Jetstream2 bucket as everything else, under
`challenges/wiserep/`:

```bash
mc cp        data/2026-07/metadata.csv  js-blast/astrodash/challenges/wiserep/2026-07/metadata.csv
mc cp --recursive data/2026-07/spectra/ js-blast/astrodash/challenges/wiserep/2026-07/spectra/
```

Note the prefix. `init/data/` is the *download manifest* root: every file
under it is fetched onto every pod and every developer volume at container
start. Scoring is an offline operator task that no cluster performs, and a
month is roughly 24 MB of spectra with another arriving every month, so
putting challenge data there would grow the startup download of every
environment forever for something none of them use. `challenges/` sits
outside the manifest, so nothing is downloaded automatically and no manifest
regeneration or image rebuild is needed to publish a month.

Pull a month when you actually need to score it:

```bash
mc cp --recursive js-blast/astrodash/challenges/wiserep/2026-07/ \
  /mnt/astrodash-data/wiserep_challenge/2026-07/
```

Reads from this bucket are anonymous; only uploading needs credentials. See
`docs/admin/updating-data-files.md` for the bucket's endpoint and access
details, and note that its manifest steps apply to `init/data/` only -- they
are deliberately not part of publishing a challenge month.

## Identifying the client

Requests carry a plain descriptive `User-Agent`:

```
AstroDASH-WISeREP-Ingest/1.0 (+https://astrodash.scimma.org)
```

An earlier revision sent a TNS-style marker with `"tns_id":0`. TNS issues real
bot ids on registration and WISeREP is operated by the same group, so a marker
carrying a placeholder id claims a registration that does not exist. If
AstroDASH registers a bot id, pass it with `--user-agent` rather than editing
the default back to a marker literal.

Only public, unauthenticated pages are read. No credentials are involved.

## Relationship to the leaderboard

This produces the input the monthly model leaderboard scores against. The two
are decoupled: the leaderboard page reads its own committed score JSON, and
the scoring step takes a dataset directory without caring how it was
produced.

If the leaderboard lands, point its `CHALLENGE_DATA_ROOT` at the mount path
above; it currently resolves to `app/wiserep_scrape/data`, which this change
stops tracking.
