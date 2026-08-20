Had leftover Codex credit so wanted to test a personal idea. Can the number of flights at the airport predict wait times? Does this even matter? All written with Codex.

# AirPlanner

AirPlanner collects real Port Authority security wait times for JFK and LGA,
joins them to FlightAware departure demand, and gradually turns the resulting
history into a security-wait forecast and airport-arrival recommendation.

The application deliberately has no demo or synthetic data. Before a source has
been collected, the corresponding UI shows an honest empty state.

## What is included

- Five-minute JFK/LGA Port Authority polling with retry, backoff, raw response
  provenance, missing-value preservation, and idempotent observations.
- Budget-limited FlightAware schedule collection with complete pagination,
  codeshare de-duplication, immutable schedule snapshots, and future-schedule
  caching.
- SQLite in WAL mode, Alembic migrations, collection health records, optional
  manual backups, and raw-provider response retention jobs.
- Demand windows, controlled correlations, historical quantiles, terminal
  inference, time-blocked model ablations, and median/P90/P95 quantile models.
- A FastAPI API and responsive Next.js dashboard and trip planner.
- Development and production Docker Compose stacks.
- A Tailscale-only production route through the existing Review Reviews Nginx
  and Cloudflare DNS-01 edge, with Docker restart policies and an optional
  systemd unit on the Oracle VM.

## Local development

Requirements: Docker with Compose v2. Node and Python are only needed if you
want to run either service outside Docker.

```bash
cp .env.example .env
```

Set `APP_UID` and `APP_GID` in `.env` to the values printed by `id -u` and
`id -g`. Leave `FLIGHTAWARE_API_KEY` empty until the AeroAPI account is ready.
Then run:

```bash
make dev
```

Open:

- Frontend: `http://localhost:3000`
- FastAPI docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/api/health`

The scheduler immediately performs a real Port Authority poll, then repeats it
every five minutes. With no FlightAware key, schedule collection is skipped and
the demand/planner surfaces remain empty.

Useful commands:

```bash
make logs
make test
make down
```

## FlightAware setup

After creating the AeroAPI key, set:

```dotenv
FLIGHTAWARE_API_KEY=your-key
```

The default monthly allocation is:

- `$4.25` for sampled training schedules.
- `$0.75` for on-demand future planner schedules.
- `$5.00` absolute provider-spend ceiling.

Before a paid request, the client checks both its local page ledger and the free
`GET /account/usage` response. The provider value can lag, so the effective
spend is the larger of the provider total and the local estimate. There is no
automatic fallback that can exceed the configured ceiling.

Paid result-set requests are paced at least 6.5 seconds apart by default to stay
within the Personal tier's 10-result-set-per-minute limit. The persisted usage
ledger carries that pacing across the JFK and LGA collectors; an unexpected 429
without `Retry-After` triggers a 61-second cooldown before retrying.

The Personal license restricts raw AeroAPI response retention. Raw FlightAware
JSON is therefore removed after 29 days, while normalized flights and schedule
snapshots remain available for analysis. Review the current FlightAware license
before changing this behavior.

## Data and model behavior

All timestamps are stored as UTC and displayed by the browser in local time.
Airport calendar features use `America/New_York`, including DST transitions.

Training is offline and requires at least 100 usable observations across five
matched schedule days. It compares historical/time-only, flight-count, and
scheduled-seat feature sets. Validation holds out complete dates; nearby
five-minute observations are never randomly split across train and validation.

If no approved model exists, the planner tries a matching historical quantile
baseline. If that is also under-sampled, it returns `insufficient_data` rather
than inventing a result.

## API

- `GET /api/airports`
- `GET /api/dashboard/current`
- `GET /api/dashboard/history`
- `GET /api/dashboard/demand`
- `GET /api/dashboard/demand/history`
- `GET /api/analytics/correlation`
- `GET /api/analytics/historical`
- `GET /api/analytics/model`
- `POST /api/planner/predict`
- `GET /api/system/status`
- `GET /api/health`

## Optional manual backups and recovery

Backups are not scheduled automatically. The main SQLite database contains the
complete working history. Run `make backup` only when you want a recovery point,
such as before a migration or significant deployment change. It uses SQLite's
online backup API and writes the snapshot under `./backups` without interrupting
collection.

To restore, stop the production service, preserve the current `data/airports.db*`
files, copy the chosen backup to `data/airports.db`, make it writable by
`APP_UID:APP_GID`, and start the service again.

## Repository map

```text
backend/          FastAPI, collectors, analytics, ML, jobs, migrations, tests
frontend/         Next.js dashboard and planner
data/             SQLite database (ignored)
artifacts/        Trained model artifacts (ignored)
backups/          Online SQLite backups (ignored)
deploy/scripts/   Oracle host bootstrap
compose.yaml      Development stack
compose.prod.yaml Production stack
```

The Port Authority integration is an independent implementation informed only
by the observable request/response contract demonstrated by
`digitalhen/tsa-times`; no source was copied, and that repository does not
currently declare a license.
