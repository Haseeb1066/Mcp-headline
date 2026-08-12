import type { DatasourceInfo, ExtensionContext, TabularPayload } from "./types";

function serializeCell(value: TableauDataValue): string | number | boolean | null {
  const raw = value?.value;
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "boolean" || typeof raw === "number") return raw;
  if (raw instanceof Date) return raw.toISOString();
  if (typeof raw === "object") return value.formattedValue || null;
  const text = String(raw);
  if (text === "%null%" || text.toLowerCase() === "null") return null;
  return text;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Safe string from Tableau objects that may omit `.name` entirely. */
function safeName(value: unknown, fallback: string): string {
  if (value == null) return fallback;
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || fallback;
  }
  if (typeof value === "object" && "name" in value) {
    const name = (value as { name?: unknown }).name;
    if (typeof name === "string" && name.trim()) return name.trim();
  }
  return fallback;
}

/** True when this page is likely hosted inside Tableau (iframe). */
export function isLikelyTableauHost(): boolean {
  if (typeof window === "undefined") return false;
  if (window.tableau?.extensions) return true;
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

/**
 * Wait for the Tableau Extensions API.
 * Prefer same-origin /tableau.extensions.min.js (avoids Tableau CSP blocking the CDN).
 */
export async function waitForTableauExtensions(timeoutMs = 20000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  const ready = () => Boolean(window.tableau?.extensions);
  if (ready()) return true;

  const loadScript = (src: string) =>
    new Promise<void>((resolve) => {
      const found = document.querySelector(`script[src="${src}"]`) as HTMLScriptElement | null;
      if (found) {
        found.addEventListener("load", () => resolve(), { once: true });
        found.addEventListener("error", () => resolve(), { once: true });
        setTimeout(() => resolve(), 30);
        return;
      }
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.dataset.narrativeTableauExt = "1";
      script.onload = () => resolve();
      script.onerror = () => resolve();
      document.head.appendChild(script);
    });

  // 1) Same-origin copy (works when extensions.tableau.com is blocked by CSP)
  await loadScript("/tableau.extensions.min.js");
  if (ready()) return true;

  // 2) Official CDN fallback
  await loadScript("https://extensions.tableau.com/tableau.extensions.1.latest.min.js");

  while (Date.now() < deadline) {
    if (ready()) return true;
    await sleep(150);
  }
  return ready();
}

function isNoiseFieldName(name: string): boolean {
  const low = name.toLowerCase().replace(/\s+/g, "");
  return (
    !low ||
    low.includes("placeholder") ||
    low.includes("pplaceholder") ||
    low.includes("measurevalues") ||
    low.includes("measurenames") ||
    low === "lat" ||
    low === "lon" ||
    low.includes("latitude") ||
    low.includes("longitude")
  );
}

function scoreTable(table: TableauDataTable): number {
  const rows = table.data?.length || 0;
  const cols = table.columns || [];
  let usefulMeasures = 0;
  let dates = 0;
  let dims = 0;
  for (const col of cols) {
    const name = col?.fieldName || "";
    if (isNoiseFieldName(name)) continue;
    const dt = (col?.dataType || "").toLowerCase();
    if (dt.includes("date")) dates += 1;
    else if (["int", "integer", "float", "real", "number", "numeric"].some((t) => dt.includes(t))) {
      usefulMeasures += 1;
    } else {
      dims += 1;
    }
  }
  // Prefer richer mark-level tables over a single AGG number
  return rows * 20 + usefulMeasures * 8 + dates * 10 + dims * 2;
}

function tableToPayload(
  table: TableauDataTable,
  context: ExtensionContext,
  datasource: DatasourceInfo,
  worksheetName?: string
): TabularPayload {
  const columns = (table.columns || [])
    .map((c, i) => ({
      index: c?.index ?? i,
      fieldName: c?.fieldName || `Column ${i + 1}`,
      dataType: c?.dataType || "string",
      name: c?.fieldName || `Column ${i + 1}`,
    }))
    .filter((c) => !isNoiseFieldName(c.fieldName));

  // Remap indexes after filtering noise columns
  const keep = new Set(columns.map((c) => c.index));
  const indexMap = new Map<number, number>();
  columns.forEach((c, i) => {
    indexMap.set(c.index, i);
    c.index = i;
  });

  const rows = (table.data || []).map((row) => {
    const raw = row || [];
    const next: Array<string | number | boolean | null> = [];
    raw.forEach((cell, idx) => {
      if (!keep.has(idx)) return;
      next[indexMap.get(idx)!] = serializeCell(cell);
    });
    return next;
  });

  const dsName = datasource?.name || "Datasource";
  return {
    columns,
    rows,
    worksheetName: worksheetName || dsName,
    dashboardName: context.dashboardName,
    workbookName: context.workbookName,
    datasourceName: dsName,
    datasourceLuid: datasource?.id || undefined,
    dataSource: "datasource",
  };
}

export type SessionDatasource = DatasourceInfo & {
  worksheetName: string;
};

/**
 * Discover datasources from the open Tableau dashboard session only.
 * No PAT / server call required.
 */
export async function collectSessionDatasources(): Promise<SessionDatasource[]> {
  const ext = window.tableau?.extensions;
  if (!ext) {
    throw new Error("Tableau Extensions API is not available.");
  }
  const worksheets = ext.dashboardContent?.dashboard?.worksheets || [];
  const byKey = new Map<string, SessionDatasource>();

  for (const ws of worksheets) {
    if (!ws) continue;
    try {
      const sources = await ws.getDataSourcesAsync();
      (sources || []).forEach((ds, index) => {
        if (!ds) return;
        const name = safeName(ds, `Datasource ${index + 1}`);
        const key = `${ds.id || name}`.toLowerCase();
        if (!key || byKey.has(key)) return;
        byKey.set(key, {
          id: ds.id || "",
          name,
          isPublished: ds.isPublished,
          isExtract: ds.isExtract,
          source: "extension",
          worksheetName: safeName(ws, "Worksheet"),
        });
      });
    } catch {
      /* worksheet may not expose datasources */
    }
  }

  return [...byKey.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export async function initTableauExtension(): Promise<ExtensionContext> {
  const ext = window.tableau?.extensions;
  if (!ext) {
    throw new Error(
      "Tableau Extensions API is not available. Open this app as a dashboard extension."
    );
  }
  await ext.initializeAsync();
  const dashboard = ext.dashboardContent?.dashboard;
  if (!dashboard) {
    throw new Error(
      "Tableau extension initialized, but no dashboard context was found. Place this on a dashboard (not a worksheet)."
    );
  }
  const worksheets = dashboard.worksheets || [];
  const worksheetNames = worksheets
    .map((w) => safeName(w, ""))
    .filter(Boolean);
  const datasources = await collectSessionDatasources();

  // `dashboard.workbook` is undefined on many Desktop builds — never read `.name` bare.
  return {
    workbookName: safeName(dashboard.workbook, "Workbook"),
    dashboardName: safeName(dashboard, "Dashboard"),
    worksheetNames,
    datasources,
    workbook: null,
    contentUrl: null,
    source: "tableau",
  };
}

/**
 * Pick the primary datasource for the *current* dashboard only.
 * Each dashboard extension instance reads its own session datasources.
 */
export function pickSessionDatasource(
  datasources: DatasourceInfo[]
): DatasourceInfo | null {
  if (!datasources.length) return null;
  const published = datasources.filter((d) => d.isPublished !== false);
  const pool = published.length ? published : datasources;
  return pool[0] ?? null;
}

type Candidate = {
  table: TableauDataTable;
  worksheetName: string;
  source: TableauDataSource;
  score: number;
};

/**
 * Load underlying data for a datasource from the Tableau session.
 * Tries logical tables and worksheet summaries across the dashboard,
 * then picks the richest table (avoids single AGG / placeholder mark tables).
 */
export async function loadSessionDatasourceTable(
  datasource: DatasourceInfo,
  context: ExtensionContext,
  maxRows = 10000
): Promise<TabularPayload> {
  const ext = window.tableau?.extensions;
  if (!ext) {
    throw new Error("Tableau Extensions API is not available.");
  }

  const worksheets = ext.dashboardContent?.dashboard?.worksheets || [];
  const candidates: Candidate[] = [];
  const seenSources = new Set<string>();

  for (const ws of worksheets) {
    if (!ws) continue;
    const wsName = safeName(ws, "Worksheet");
    let sources: TableauDataSource[] = [];
    try {
      sources = [...(await ws.getDataSourcesAsync())];
    } catch {
      continue;
    }

    for (const ds of sources) {
      if (!ds) continue;
      const matches =
        (datasource.id && ds.id === datasource.id) ||
        safeName(ds, "") === datasource.name;
      if (!matches) continue;

      const sourceKey = `${ds.id || safeName(ds, "ds")}`;
      if (!seenSources.has(sourceKey)) {
        seenSources.add(sourceKey);
        try {
          const logicalTables = await ds.getLogicalTablesAsync();
          for (const logical of logicalTables || []) {
            try {
              const table = await ds.getLogicalTableDataAsync(logical.id, { maxRows });
              candidates.push({
                table,
                worksheetName: logical.caption || wsName,
                source: ds,
                score: scoreTable(table) + 40, // prefer underlying logical data
              });
            } catch {
              /* try next logical table */
            }
          }
        } catch {
          /* no logical tables */
        }

        if (typeof ds.getUnderlyingDataAsync === "function") {
          try {
            const table = await ds.getUnderlyingDataAsync({
              maxRows,
              includeAllColumns: true,
              ignoreSelection: true,
            });
            candidates.push({
              table,
              worksheetName: wsName,
              source: ds,
              score: scoreTable(table) + 30,
            });
          } catch {
            /* continue */
          }
        }
      }

      // Always consider worksheet summary — often has usable dims/dates for AP views
      try {
        const summary = await ws.getSummaryDataAsync({
          maxRows,
          ignoreSelection: true,
          includeAllColumns: true,
        });
        candidates.push({
          table: summary,
          worksheetName: wsName,
          source: ds,
          score: scoreTable(summary),
        });
      } catch {
        /* continue */
      }
    }
  }

  if (!candidates.length) {
    throw new Error(
      `Datasource “${datasource.name}” was not found in this dashboard session, ` +
        `or Full Data permission is required to read marks.`
    );
  }

  candidates.sort((a, b) => b.score - a.score);
  const best = candidates[0];
  return tableToPayload(
    best.table,
    context,
    {
      ...datasource,
      name: safeName(best.source, datasource.name || "Datasource"),
      id: best.source.id || datasource.id,
    },
    best.worksheetName
  );
}
