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

function tableToPayload(
  table: TableauDataTable,
  context: ExtensionContext,
  datasource: DatasourceInfo
): TabularPayload {
  const columns = table.columns.map((c) => ({
    index: c.index,
    fieldName: c.fieldName,
    dataType: c.dataType,
    name: c.fieldName,
  }));
  const rows = table.data.map((row) => row.map(serializeCell));
  return {
    columns,
    rows,
    worksheetName: datasource.name,
    dashboardName: context.dashboardName,
    workbookName: context.workbookName,
    datasourceName: datasource.name,
    datasourceLuid: datasource.id || undefined,
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
  const worksheets = ext.dashboardContent.dashboard.worksheets;
  const byKey = new Map<string, SessionDatasource>();

  for (const ws of worksheets) {
    try {
      const sources = await ws.getDataSourcesAsync();
      sources.forEach((ds, index) => {
        const key = `${ds.id || ds.name}`.toLowerCase();
        if (!key || byKey.has(key)) return;
        byKey.set(key, {
          id: ds.id || "",
          name: ds.name,
          isPublished: ds.isPublished,
          isExtract: ds.isExtract,
          source: "extension",
          // First datasource on a worksheet is the primary one by Tableau convention
          worksheetName: ws.name,
          ...(index === 0 ? {} : {}),
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
  const dashboard = ext.dashboardContent.dashboard;
  const worksheetNames = dashboard.worksheets.map((w) => w.name);
  const datasources = await collectSessionDatasources();

  return {
    workbookName: dashboard.workbook.name,
    dashboardName: dashboard.name,
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

  const worksheets = ext.dashboardContent.dashboard.worksheets;
  let matchedSource: TableauDataSource | null = null;
  let matchedWorksheet: TableauWorksheet | null = null;

  for (const ws of worksheets) {
    try {
      const sources = await ws.getDataSourcesAsync();
      const found = sources.find(
        (ds) =>
          (datasource.id && ds.id === datasource.id) ||
          ds.name === datasource.name
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
        name: matchedSource.name,
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
        name: matchedSource.name,
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
    name: matchedSource.name,
    id: matchedSource.id || datasource.id,
  });
}
