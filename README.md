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
npm start
```

Serves the built UI and API on port `8787` (or `$PORT`).

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
