import { apiUrl } from "./config";
import type {
  DatasourceInfo,
  NarrativeResult,
  TabularPayload,
  WorkbookSummary,
} from "./types";

export async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(text || `HTTP ${res.status}`);
  }
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : text || `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data as T;
}

export async function fetchHealth(): Promise<{
  ok: boolean;
  hasOpenAi: boolean;
  hasTableau?: boolean;
  tableauSignInOk?: boolean;
}> {
  const res = await fetch(apiUrl("/api/health"));
  return readJson(res);
}

export async function resolveWorkbook(options: {
  workbookId?: string;
  name?: string;
  contentUrl?: string;
  projectName?: string;
}): Promise<WorkbookSummary> {
  const qs = new URLSearchParams();
  if (options.workbookId) qs.set("workbookId", options.workbookId);
  if (options.name) qs.set("name", options.name);
  if (options.contentUrl) qs.set("contentUrl", options.contentUrl);
  if (options.projectName) qs.set("projectName", options.projectName);
  const res = await fetch(apiUrl(`/api/workbooks/resolve?${qs}`));
  const data = await readJson<{ workbook: WorkbookSummary }>(res);
  if (!data.workbook?.id) throw new Error("Could not resolve workbook");
  return data.workbook;
}

export async function fetchDatasources(workbookId?: string): Promise<{
  datasources: DatasourceInfo[];
  workbookConnections: Array<Record<string, string>>;
  scopedToWorkbook: boolean;
}> {
  const qs = workbookId ? `?workbookId=${encodeURIComponent(workbookId)}` : "";
  const res = await fetch(apiUrl(`/api/datasources${qs}`));
  return readJson(res);
}

export async function fetchWorkbookViews(workbookId: string): Promise<{
  workbook: WorkbookSummary;
  views: Array<{ id: string; name: string; contentUrl?: string; viewUrlName?: string }>;
}> {
  const res = await fetch(apiUrl(`/api/workbooks/${encodeURIComponent(workbookId)}/views`));
  return readJson(res);
}

export async function fetchNarrative(
  payload: TabularPayload,
  polish = true
): Promise<NarrativeResult> {
  const res = await fetch(apiUrl("/api/narrative"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, polish }),
  });
  return readJson(res);
}

export async function fetchNarrativeFromDatasource(options: {
  datasourceLuid?: string;
  datasourceName?: string;
  workbookName?: string;
  dashboardName?: string;
  polish?: boolean;
  fieldCaptions?: string[];
}): Promise<NarrativeResult> {
  const res = await fetch(apiUrl("/api/narrative/from-datasource"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      datasourceLuid: options.datasourceLuid,
      datasourceName: options.datasourceName,
      workbookName: options.workbookName ?? "",
      dashboardName: options.dashboardName ?? "",
      polish: options.polish ?? true,
      fieldCaptions: options.fieldCaptions,
    }),
  });
  return readJson(res);
}

export async function fetchNarrativeFromWorkbook(options: {
  workbookId?: string;
  contentUrl?: string;
  workbookName?: string;
  viewId?: string;
  viewName?: string;
  dashboardName?: string;
  polish?: boolean;
}): Promise<NarrativeResult> {
  const res = await fetch(apiUrl("/api/narrative/from-workbook"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workbookId: options.workbookId,
      contentUrl: options.contentUrl,
      workbookName: options.workbookName,
      viewId: options.viewId,
      viewName: options.viewName,
      dashboardName: options.dashboardName ?? "",
      polish: options.polish ?? true,
    }),
  });
  return readJson(res);
}
