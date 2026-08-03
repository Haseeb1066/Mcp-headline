# Tableau dashboard extension — Narrative Insights

## How it works

1. Tableau loads the UI in an iframe from the URL in `NarrativeInsights.trex`.
2. The extension initializes the Tableau Extensions API, lists **worksheets**, and discovers **datasources** on those worksheets.
3. It resolves the **workbook** (contentUrl / name → LUID) via `GET /api/workbooks/resolve` when Tableau PAT credentials are configured.
4. Choose a source in the UI:
   - **Workbook worksheet** — reads summary data (`getSummaryDataAsync`), filters apply.
   - **Published datasource** — queries via `POST /api/narrative/from-datasource` (Metadata fields + MCP `query-datasource`).

## Setup

### 1. Configure Tableau credentials

In project `.env`:

```
TABLEAU_SERVER=https://your-server
TABLEAU_SITE_NAME=
TABLEAU_PAT_NAME=...
TABLEAU_PAT_VALUE=...
```

Required for workbook resolve and datasource queries. Worksheet-only mode still works without PAT (summary data from the extension).

### 2. Build and run the server

```bash
npm install
pip install -r requirements.txt   # or use .venv
npm run build
npm start
```

UI + API: http://localhost:8787/

### 3. Edit the manifest URL

In `NarrativeInsights.trex`, set `<source-location><url>`:

- **Local Tableau Desktop:** `http://localhost:8787/`
- **Tableau Server / Cloud:** `https://your-server.example.com/` (HTTPS required)

### 4. Add to a dashboard

1. Open a workbook → edit dashboard → drag **Extension**.
2. Choose **NarrativeInsights.trex**.
3. Pick **Workbook worksheet** or **Published datasource**, then Refresh.

## Browser test without Tableau

```
http://localhost:5173/?mock=1
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Extensions API not available | Open via dashboard extension, or use `?mock=1` |
| Workbook not resolved | Set TABLEAU_* in `.env`; check contentUrl / workbook name |
| Datasource mode fails | Enable API Access on the published datasource; PAT user needs View |
| No MoM/YoY | Need a date field and a numeric measure |
| Blank on Server | Use HTTPS; add URL to Server safe list |
