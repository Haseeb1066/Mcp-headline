# Tableau Narrative Insights

Dashboard extension that connects to **both the workbook and published datasources** on the dashboard where it is placed, then shows:

- Executive **summary**
- **KPIs** with month-over-month and year-over-year deltas
- Ranked **highlights** (movers, top drivers, outliers, data-quality notes)
- **Last month**, **YoY (12 months)**, and **YTD** comparisons

## Data connections

| Source | How it connects | What you get |
|--------|-----------------|--------------|
| **Workbook worksheet** | Tableau Extensions API `getSummaryDataAsync` (respects dashboard filters) | Narrative from the selected sheet |
| **Published datasource** | Resolve workbook via REST + list datasources; query via Tableau MCP `query-datasource` | Narrative from underlying published data |

The extension also discovers datasources attached to dashboard worksheets via `getDataSourcesAsync`, and resolves the workbook LUID from the dashboard URL / workbook name when Tableau PAT credentials are set.

## Prerequisites

- Python 3.9+
- Node.js 18+
- Tableau Desktop / Server / Cloud (for the real extension; mock mode works in a browser)
- For datasource mode: Tableau PAT with access to the published datasource (API Access enabled)

## Setup

```bash
cd /path/to/narrative
cp .env.example .env
# Set TABLEAU_SERVER, TABLEAU_SITE_NAME, TABLEAU_PAT_NAME, TABLEAU_PAT_VALUE
# optional: OPENAI_API_KEY for polished summaries

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
npm install
```

`npm run dev` / `npm start` use `.venv/bin/python` by default.

## Development

```bash
npm run dev
```

| Service | URL |
|---------|-----|
| Web (Vite) | http://localhost:5173 |
| API | http://localhost:8787 |

Mock data (no Tableau):

```
http://localhost:5173/?mock=1
```

## Production

```bash
npm run build
npm run start:local   # uses .venv
# or: npm start       # system python (Render / production)
```

Serves the built UI and API on port `8787` (or `$PORT`).

## Deploy to Render (dashboard extension — no PAT, no Docker)

Native **Python** web service. The UI is built during deploy; at runtime only FastAPI serves the app. When the extension runs **on a Tableau dashboard**, it reads session datasource data and POSTs to `/api/narrative`. **No Tableau PAT and no Docker.**

### 1. Push the repo to GitHub

Include `render.yaml`, `scripts/render-build.sh`, and the app source (do not commit `.env`).

### 2. Create the service

**Option A — Blueprint (recommended)**  
1. [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**  
2. Connect the repo → apply `render.yaml`  

**Option B — Manual**  
1. **New** → **Web Service** → connect repo  
2. Runtime: **Python 3**  
3. Build: `bash scripts/render-build.sh`  
4. Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`  
5. Health check path: `/api/health`  

### 3. Environment variables

| Variable | Required? | Notes |
|----------|-----------|--------|
| `OPENAI_API_KEY` | Optional | Enables AI insight sections; without it you still get quantitative analysis + template summary |
| `OPENAI_MODEL` | No | Default `gpt-4o-mini` |
| `PYTHON_VERSION` | No | Blueprint sets `3.12.0` |

Do **not** set `TABLEAU_PAT_*` for dashboard use.

### 4. Point the Tableau extension at Render

After deploy you get a URL like `https://narrative-insights.onrender.com`.

1. Edit `extension/NarrativeInsights.trex`:

```xml
<source-location>
  <url>https://YOUR-SERVICE.onrender.com/</url>
</source-location>
```

2. On Tableau Cloud / Server → **Settings → Extensions** → allowlist that HTTPS URL.  
3. Add `NarrativeInsights.trex` to the dashboard (session datasource — no PAT).

### 5. Verify

- Health: `https://YOUR-SERVICE.onrender.com/api/health` (`hasTableau` can be `false` — expected)  
- Mock UI: `https://YOUR-SERVICE.onrender.com/?mock=1`  
- Real use: open the extension on a Tableau dashboard  

**Note:** Free Render instances sleep when idle; first load can take ~30–60s.

## Tableau install

See [extension/README.md](extension/README.md). Use [extension/NarrativeInsights.trex](extension/NarrativeInsights.trex) and point its URL at your hosted app.

## API

### `GET /api/health`

Health, OpenAI, and Tableau PAT sign-in status.

### `GET /api/workbooks/resolve`

Resolve workbook by `workbookId`, `name`, or `contentUrl`.

### `GET /api/datasources?workbookId=...`

List published datasources (scoped to workbook connections when possible).

### `GET /api/datasource-fields?luid=...` or `?name=...`

Metadata GraphQL field list.

### `POST /api/narrative`

Analyze a tabular payload from the extension (worksheet summary data).

### `POST /api/narrative/from-datasource`

Query a published datasource and return the narrative.
