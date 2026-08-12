/** Minimal Tableau Extensions API types used by Narrative Insights. */

interface TableauDataValue {
  readonly value: string | number | boolean | Date | null;
  readonly formattedValue: string;
}

interface TableauColumn {
  readonly fieldName: string;
  readonly dataType: string;
  readonly index: number;
}

interface TableauDataTable {
  readonly columns: ReadonlyArray<TableauColumn>;
  readonly data: ReadonlyArray<ReadonlyArray<TableauDataValue>>;
  readonly totalRowCount: number;
}

interface TableauLogicalTable {
  readonly id: string;
  readonly caption: string;
}

interface TableauDataSourceField {
  readonly id: string;
  readonly name: string;
  readonly description?: string;
  readonly dataType?: string;
  readonly role?: string;
  readonly isHidden?: boolean;
  readonly isCalculatedField?: boolean;
}

interface TableauDataSource {
  readonly id: string;
  readonly name: string;
  readonly isExtract: boolean;
  readonly isPublished: boolean;
  readonly fields: ReadonlyArray<TableauDataSourceField>;
  getLogicalTablesAsync(): Promise<ReadonlyArray<TableauLogicalTable>>;
  getLogicalTableDataAsync(
    logicalTableId: string,
    options?: { maxRows?: number; ignoreAliases?: boolean; ignoreSelection?: boolean }
  ): Promise<TableauDataTable>;
  /** Older Tableau versions */
  getUnderlyingDataAsync?(options?: {
    maxRows?: number;
    ignoreAliases?: boolean;
    ignoreSelection?: boolean;
    includeAllColumns?: boolean;
  }): Promise<TableauDataTable>;
}

interface TableauWorksheet {
  readonly name: string;
  getSummaryDataAsync(options?: {
    maxRows?: number;
    ignoreSelection?: boolean;
    includeAllColumns?: boolean;
  }): Promise<TableauDataTable>;
  getDataSourcesAsync(): Promise<ReadonlyArray<TableauDataSource>>;
}

interface TableauDashboard {
  readonly name: string;
  /** Not always present on all Tableau Desktop / Server builds. */
  readonly workbook?: { readonly name?: string } | null;
  readonly worksheets: ReadonlyArray<TableauWorksheet>;
}

interface TableauSettings {
  get(key: string): string | undefined;
  set(key: string, value: string): void;
  saveAsync(): Promise<void>;
}

interface TableauDashboardContent {
  readonly dashboard: TableauDashboard;
}

interface TableauExtensionsApi {
  initializeAsync(): Promise<void>;
  readonly dashboardContent: TableauDashboardContent;
  readonly settings: TableauSettings;
}

interface TableauGlobal {
  readonly extensions: TableauExtensionsApi;
}

interface Window {
  tableau?: TableauGlobal;
}
