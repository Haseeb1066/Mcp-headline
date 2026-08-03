export type ColumnMeta = {
  index: number;
  fieldName: string;
  dataType: string;
  name?: string;
};

export type DataSourceMode = "workbook" | "datasource";

export type WorkbookSummary = {
  id: string;
  name: string;
  contentUrl?: string;
  projectName?: string;
};

export type DatasourceInfo = {
  id: string;
  name: string;
  projectName?: string;
  isPublished?: boolean;
  isExtract?: boolean;
  source?: "extension" | "rest" | "mcp" | string;
};

export type TabularPayload = {
  columns: ColumnMeta[];
  rows: Array<Array<string | number | boolean | null>>;
  worksheetName: string;
  dashboardName: string;
  workbookName: string;
  datasourceName?: string;
  datasourceLuid?: string;
  dataSource?: DataSourceMode;
};

export type Highlight = {
  severity: "high" | "medium" | "low" | string;
  category: string;
  text: string;
  details?: unknown;
};

export type ComparisonBlock = {
  label: string;
  measure: string;
  currentPeriod: string;
  previousPeriod: string;
  current: number;
  previous: number;
  delta: number;
  pctChange: number | null;
  direction?: "up" | "down" | "flat" | string;
  pointer?: string;
};

export type PeriodChange = {
  period: string;
  previousPeriod: string;
  value: number;
  previousValue: number;
  delta: number;
  pctChange: number | null;
  direction: "up" | "down" | "flat" | string;
  pointer: string;
};

export type QuantitativePointer = {
  periodType: "monthly" | "quarterly" | "yearly" | string;
  severity: string;
  text: string;
  current?: number;
  previous?: number;
  delta?: number;
  pctChange?: number | null;
  direction?: "up" | "down" | "flat" | string;
};

export type QuantitativeAnalysis = {
  measure: string | null;
  dateField: string | null;
  asOf?: string | null;
  headline: ComparisonBlock[];
  pointers: QuantitativePointer[];
  monthlyChanges: PeriodChange[];
  quarterlyChanges: PeriodChange[];
  monthlySeries: Array<{ period: string; value: number }>;
  quarterlySeries: Array<{ period: string; value: number }>;
};

export type NarrativeResult = {
  summary: string;
  summarySource: "template" | "llm" | string;
  kpis: Array<{
    name: string;
    value: number | null;
    formatted: string;
    mean?: number;
    min?: number;
    max?: number;
    momPct?: number | null;
    momDelta?: number | null;
    qoqPct?: number | null;
    qoqDelta?: number | null;
    yoyPct?: number | null;
    yoyDelta?: number | null;
  }>;
  highlights: Highlight[];
  comparisons: {
    monthOverMonth: ComparisonBlock | null;
    quarterOverQuarter?: ComparisonBlock | null;
    yearOverYear: ComparisonBlock | null;
    yearToDate: ComparisonBlock | null;
    sameMonthPriorYear?: ComparisonBlock | null;
    monthlySeries: Array<{ period: string; value: number }>;
    quarterlySeries?: Array<{ period: string; value: number }>;
    monthlyChanges?: PeriodChange[];
    quarterlyChanges?: PeriodChange[];
    pointers?: QuantitativePointer[];
    measure?: string;
    dateField?: string;
    asOf?: string;
  };
  quantitative?: QuantitativeAnalysis;
  profiles: Array<Record<string, unknown>>;
  schema: {
    measures: string[];
    dimensions: string[];
    dates: string[];
  };
  context: {
    worksheetName: string;
    dashboardName: string;
    workbookName: string;
    rowCount: number;
    columnCount: number;
    dateRange: { min: string; max: string } | null;
    dateField?: string | null;
    dataSource?: DataSourceMode | string;
    datasourceName?: string;
    datasourceLuid?: string;
  };
  notes: string[];
  topDrivers: Array<{
    dimension: string;
    value: string;
    measure: string;
    total: number;
    share: number;
  }>;
  truncated?: boolean;
  queryFields?: unknown;
  insightSections?: Array<{
    title: string;
    insights: Array<{
      text: string;
      severity: "critical" | "warning" | "opportunity" | "info" | string;
      direction?: "up" | "down" | "flat" | string;
    }>;
  }>;
};

export type ExtensionContext = {
  workbookName: string;
  dashboardName: string;
  worksheetNames: string[];
  datasources: DatasourceInfo[];
  workbook?: WorkbookSummary | null;
  contentUrl?: string | null;
  source: "tableau" | "mock";
};
