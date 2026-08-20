# AirPlanner frontend

Next.js dashboard and trip-planner interface. The normal development workflow
is documented in the repository root `README.md` and uses Docker Compose.

Standalone frontend commands:

```bash
npm install
npm run dev
npm run lint
npm run build
```

Set `NEXT_PUBLIC_API_URL=http://localhost:8000` when the API is hosted on a
different origin. In production, Caddy serves both applications from one origin
and this value is intentionally empty.
