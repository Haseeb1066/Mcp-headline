import type { ExtensionContext, TabularPayload } from "./types";

/** Synthetic sales data for browser testing without Tableau (`?mock=1`). */
export function getMockContext(): ExtensionContext {
  return {
    workbookName: "Sample Sales",
    dashboardName: "Executive Overview",
    worksheetNames: ["Sales Trend", "Category Mix"],
    datasources: [
      {
        id: "a70a7ff7-c515-41da-8121-6b09468d92d7",
        name: "AP Dataset",
        projectName: "Data Sources",
        isPublished: true,
        source: "extension",
      },
    ],
    workbook: {
      id: "00000000-0000-4000-8000-000000000001",
      name: "Sample Sales",
      contentUrl: "SampleSales",
      projectName: "Samples",
    },
    contentUrl: "SampleSales",
    source: "mock",
  };
}

function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}-15`;
}

export function getMockTable(worksheetName: string): TabularPayload {
  const categories = ["Furniture", "Technology", "Office Supplies"];
  const regions = ["West", "East", "Central", "South"];
  const rows: Array<Array<string | number | null>> = [];

  const today = new Date();
  const end = new Date(today.getFullYear(), today.getMonth() - 1, 1);

  for (let i = 25; i >= 0; i--) {
    const d = new Date(end.getFullYear(), end.getMonth() - i, 1);
    const y = d.getFullYear();
    const m = d.getMonth() + 1;
    const season = 1 + 0.15 * Math.sin((m / 12) * Math.PI * 2);
    const yoyGrowth = 1 + (y - (today.getFullYear() - 1)) * 0.08;

    for (const category of categories) {
      for (const region of regions) {
        const base =
          category === "Technology" ? 42000 : category === "Furniture" ? 28000 : 16000;
        const regionFactor =
          region === "West" ? 1.2 : region === "East" ? 1.1 : region === "Central" ? 0.9 : 0.85;
        const noise = 0.85 + ((y * 17 + m * 3 + category.length + region.length) % 30) / 100;
        const sales = Math.round(base * regionFactor * season * yoyGrowth * noise);
        const profit = Math.round(sales * (0.12 + (category === "Technology" ? 0.08 : 0.03)));
        rows.push([monthKey(y, m), category, region, sales, profit]);
      }
    }
  }

  if (rows.length > 10) {
    rows[rows.length - 5][3] = 250000;
  }

  return {
    columns: [
      { index: 0, fieldName: "Order Date", dataType: "date", name: "Order Date" },
      { index: 1, fieldName: "Category", dataType: "string", name: "Category" },
      { index: 2, fieldName: "Region", dataType: "string", name: "Region" },
      { index: 3, fieldName: "Sales", dataType: "float", name: "Sales" },
      { index: 4, fieldName: "Profit", dataType: "float", name: "Profit" },
    ],
    rows,
    worksheetName: worksheetName || "Sales Trend",
    dashboardName: "Executive Overview",
    workbookName: "Sample Sales",
    dataSource: "workbook",
  };
}

export function getMockDatasourceTable(datasourceName: string): TabularPayload {
  const base = getMockTable("Datasource");
  return {
    ...base,
    worksheetName: datasourceName,
    datasourceName,
    datasourceLuid: "mock-ds-sales",
    dataSource: "datasource",
  };
}
