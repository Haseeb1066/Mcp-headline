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

function tableToPayload(
  table: TableauDataTable,
  context: ExtensionContext,
  datasource: DatasourceInfo
): TabularPayload {
  const columns = (table.columns || []).map((c, i) => ({
    index: c?.index ?? i,
    fieldName: c?.fieldName || `Column ${i + 1}`,
    dataType: c?.dataType || "string",
    name: c?.fieldName || `Column ${i + 1}`,
  }));
  const rows = (table.data || []).map((row) => (row || []).map(serializeCell));
  const dsName = datasource?.name || "Datasource";
  return {
    columns,
    rows,
    worksheetName: dsName,
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
        const name = ds.name || `Datasource ${index + 1}`;
        const key = `${ds.id || name}`.toLowerCase();
        if (!key || byKey.has(key)) return;
        byKey.set(key, {
          id: ds.id || "",
          name,
          isPublished: ds.isPublished,
          isExtract: ds.isExtract,
          source: "extension",
          worksheetName: ws.name || "Worksheet",
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
  const worksheetNames = worksheets.map((w) => w?.name || "").filter(Boolean);
  const datasources = await collectSessionDatasources();

  return {
    workbookName: dashboard.workbook?.name || "Workbook",
    dashboardName: dashboard.name || "Dashboard",
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

/**
 * Load underlying data for a datasource from the Tableau session.
 * Uses logical table data when available; falls back to summary data
 * from the worksheet that exposes that datasource.
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
  let matchedSource: TableauDataSource | null = null;
  let matchedWorksheet: TableauWorksheet | null = null;

  for (const ws of worksheets) {
    if (!ws) continue;
    try {
      const sources = await ws.getDataSourcesAsync();
      const found = (sources || []).find(
        (ds) =>
          ds &&
          ((datasource.id && ds.id === datasource.id) ||
            ds.name === datasource.name)
      );
      if (found) {
        matchedSource = found;
        matchedWorksheet = ws;
        break;
      }
    } catch {
      /* continue */
    }
  }

  if (!matchedSource || !matchedWorksheet) {
    throw new Error(
      `Datasource “${datasource.name}” was not found in this dashboard session.`
    );
  }

  // Preferred: logical table data from the session datasource (no PAT)
  try {
    const logicalTables = await matchedSource.getLogicalTablesAsync();
    if (logicalTables.length > 0) {
      const table = await matchedSource.getLogicalTableDataAsync(logicalTables[0].id, {
        maxRows,
      });
      return tableToPayload(table, context, {
        ...datasource,
        name: matchedSource.name || datasource.name || "Datasource",
        id: matchedSource.id || datasource.id,
      });
    }
  } catch {
    /* fall through to older APIs / summary data */
  }

  // Older Tableau: getUnderlyingDataAsync on the datasource
  if (typeof matchedSource.getUnderlyingDataAsync === "function") {
    try {
      const table = await matchedSource.getUnderlyingDataAsync({
        maxRows,
        includeAllColumns: true,
        ignoreSelection: true,
      });
      return tableToPayload(table, context, {
        ...datasource,
        name: matchedSource.name || datasource.name || "Datasource",
        id: matchedSource.id || datasource.id,
      });
    } catch {
      /* fall through */
    }
  }

  // Last resort: worksheet summary data still comes from this session datasource
  const summary = await matchedWorksheet.getSummaryDataAsync({
    maxRows,
    ignoreSelection: true,
    includeAllColumns: true,
  });
  return tableToPayload(summary, context, {
    ...datasource,
    name: matchedSource.name || datasource.name || "Datasource",
    id: matchedSource.id || datasource.id,
  });
}
