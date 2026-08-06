import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  fetchDatasources,
  fetchNarrative,
  fetchNarrativeFromDatasource,
  fetchNarrativeFromWorkbook,
  fetchWorkbookViews,
  resolveWorkbook,
} from "./api";
import { queryParam, useMockMode, useServerContentMode } from "./config";
import { getMockContext, getMockDatasourceTable } from "./mockData";
import {
  initTableauExtension,
  loadSessionDatasourceTable,
  pickSessionDatasource,
} from "./tableauData";
import type {
  DatasourceInfo,
  ExtensionContext,
  NarrativeResult,
} from "./types";
import "./App.css";

type InsightSection = NonNullable<NarrativeResult["insightSections"]>[number];
type InsightItem = InsightSection["insights"][number];

function inferDirection(text: string, explicit?: string | null): "up" | "down" | "flat" | null {
  if (explicit === "up" || explicit === "down" || explicit === "flat") return explicit;
  if (/↑|\bincreased\b|\bgrew\b|\bup\b|\bahead of\b|\brise\b|\brising\b/i.test(text)) return "up";
  if (/↓|\bdecreased\b|\bdeclined\b|\bdown\b|\bbehind\b|\bfall\b|\bfalling\b|\bdrop/i.test(text)) {
    return "down";
  }
  if (/→|\bflat\b|\bunchanged\b/i.test(text)) return "flat";
  return null;
}

function highlightInsightText(text: string, explicitDirection?: string | null): ReactNode {
  const fallback = inferDirection(text, explicitDirection);
  const pattern =
    /(↑\s*[\d.,]+%?|↓\s*[\d.,]+%?|→\s*[\d.,]+%?|↑|↓|→|\$?\d[\d,]*(?:\.\d+)?(?:\s*%|[KMB])?|\d{4}-Q[1-4]|\d{4}-\d{2}(?:-\d{2})?)/g;

  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    let className = "num-hl";

    if (/^\d{4}-(?:Q[1-4]|\d{2}(?:-\d{2})?)$/.test(token)) {
      className = "num-period";
    } else if (token.startsWith("↑")) {
      className = "num-up";
    } else if (token.startsWith("↓")) {
      className = "num-down";
    } else if (token.startsWith("→")) {
      className = "num-flat";
    } else if (fallback === "up") {
      className = "num-up";
    } else if (fallback === "down") {
      className = "num-down";
    } else if (fallback === "flat") {
      className = "num-flat";
    }

    nodes.push(
      <span className={className} key={`n-${key++}`}>
        {token}
      </span>
    );
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes.length ? nodes : text;
}

function quantitativeSections(result: NarrativeResult): InsightSection[] {
  const quantitative = result.quantitative;
  if (!quantitative) return [];

  const grouped = new Map<string, InsightItem[]>([
    ["Monthly Quantitative Analysis", []],
    ["Quarterly Quantitative Analysis", []],
    ["Yearly Quantitative Analysis", []],
  ]);

  for (const pointer of quantitative.pointers) {
    const title =
      pointer.periodType === "monthly"
        ? "Monthly Quantitative Analysis"
        : pointer.periodType === "quarterly"
          ? "Quarterly Quantitative Analysis"
          : "Yearly Quantitative Analysis";
    grouped.get(title)?.push({
      text: pointer.text,
      severity:
        pointer.pctChange != null && Math.abs(pointer.pctChange) >= 10
          ? "warning"
          : "info",
      direction: pointer.direction,
    });
  }

  const context = [
    quantitative.measure,
    quantitative.dateField ? `by ${quantitative.dateField}` : null,
    quantitative.asOf ? `as of ${quantitative.asOf}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return [...grouped.entries()]
    .filter(([, insights]) => insights.length > 0)
    .map(([title, insights], index) => ({
      title,
      insights:
        index === 0 && context
          ? [{ text: `Comparison basis: ${context}.`, severity: "info" }, ...insights]
          : insights,
    }));
}

function displaySections(result: NarrativeResult): InsightSection[] {
  const quantitative = quantitativeSections(result);
  if (result.insightSections?.length) {
    return [...quantitative, ...result.insightSections];
  }
  return [
    ...quantitative,
    {
      title: "Executive Summary",
      insights: [{ text: result.summary, severity: "info" }],
    },
  ];
}

function pickPrimaryDatasource(datasources: DatasourceInfo[]): DatasourceInfo | null {
  if (!datasources.length) return null;
  const published = datasources.filter((d) => d.isPublished !== false);
  const pool = published.length ? published : datasources;
  return pool[0];
}

function apFieldCaptions(name: string): string[] | undefined {
  if (!/ap dataset|accounts payable|payable/i.test(name)) return undefined;
  return [
    "Outstanding Amount",
    "Cleared Flag",
    "Due Date",
    "Invoice Date",
    "Creditor",
    "Aging Category (Level 1)",
    "Aging Category (Level 2)",
    "Aging Category (Level 3)",
  ];
}

function InsightSections({
  sections,
}: {
  sections: NonNullable<NarrativeResult["insightSections"]>;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const pauseRef = useRef(false);
  const userScrollUntil = useRef(0);
  const programmaticScroll = useRef(false);

  const track = (
    <>
      {sections.map((section) => (
        <section className="ai-section" key={section.title}>
          <h2>{section.title}</h2>
          {section.insights.map((insight, index) => (
            <div
              className={`ai-insight severity-${insight.severity}`}
              key={`${section.title}-${index}`}
            >
              <span className="insight-arrow">▼</span>
              <p>{highlightInsightText(insight.text, insight.direction)}</p>
            </div>
          ))}
        </section>
      ))}
    </>
  );

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) return;

    let frame = 0;
    let last = performance.now();
    const speed = 28; // px / sec

    const wrapIfNeeded = () => {
      const half = el.scrollHeight / 2;
      if (half <= 0) return;
      if (el.scrollTop >= half) {
        programmaticScroll.current = true;
        el.scrollTop -= half;
        programmaticScroll.current = false;
      } else if (el.scrollTop < 0) {
        programmaticScroll.current = true;
        el.scrollTop += half;
        programmaticScroll.current = false;
      }
    };

    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const paused = pauseRef.current || now < userScrollUntil.current;
      if (!paused) {
        programmaticScroll.current = true;
        el.scrollTop += speed * dt;
        wrapIfNeeded();
        programmaticScroll.current = false;
      }
      frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [sections]);

  return (
    <div className="insight-scroll-shell">
      <p className="insight-scroll-hint">
        Auto-scroll pauses on hover · use the scrollbar to scroll manually
      </p>
      <div
        className="insight-marquee"
        ref={scrollerRef}
        aria-label="Insights auto-scroll. Hover to pause. Use scrollbar to scroll manually."
        onMouseEnter={() => {
          pauseRef.current = true;
        }}
        onMouseLeave={() => {
          pauseRef.current = false;
        }}
        onWheel={() => {
          userScrollUntil.current = performance.now() + 3500;
        }}
        onScroll={() => {
          if (programmaticScroll.current) return;
          userScrollUntil.current = performance.now() + 3500;
          const el = scrollerRef.current;
          if (!el) return;
          const half = el.scrollHeight / 2;
          if (half > 0 && el.scrollTop >= half) {
            programmaticScroll.current = true;
            el.scrollTop -= half;
            programmaticScroll.current = false;
          }
        }}
      >
        <div className="insight-marquee-track">
          <div className="insight-marquee-group">{track}</div>
          <div className="insight-marquee-group" aria-hidden="true">
            {track}
          </div>
        </div>
      </div>
    </div>
  );
}

export function App() {
  const mock = useMockMode();
  const serverMode = useServerContentMode();
  const [context, setContext] = useState<ExtensionContext | null>(null);
  const [activeDatasource, setActiveDatasource] = useState<DatasourceInfo | null>(null);
  const [result, setResult] = useState<NarrativeResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (mock) {
          const ctx = getMockContext();
          if (cancelled) return;
          setContext(ctx);
          setActiveDatasource(pickPrimaryDatasource(ctx.datasources));
          return;
        }

        // Browser-only live test with ?contentUrl=... (uses PAT on the server)
        if (serverMode) {
          const contentUrl = queryParam("contentUrl");
          const workbookId = queryParam("workbookId");
          const dsParam = queryParam("datasource");
          const viewParam = queryParam("view");

          const wb = await resolveWorkbook({
            contentUrl: contentUrl || undefined,
            workbookId: workbookId || undefined,
          });
          if (cancelled) return;

          const viewsPayload = await fetchWorkbookViews(wb.id);
          const viewNames = viewsPayload.views.map((v) => v.name);
          const server = await fetchDatasources(wb.id);
          let dashboardDs = server.datasources;
          if (dsParam && !dashboardDs.some((d) => d.name === dsParam || d.id === dsParam)) {
            dashboardDs = [
              { id: "", name: dsParam, isPublished: true, source: "query" },
              ...dashboardDs,
            ];
          }

          const ctx: ExtensionContext = {
            workbookName: wb.name,
            dashboardName: viewParam || "AI Generated Summary",
            worksheetNames: viewNames,
            datasources: dashboardDs,
            workbook: wb,
            contentUrl: wb.contentUrl || contentUrl,
            source: "tableau",
          };
          setContext(ctx);
          setActiveDatasource(
            dashboardDs.find((d) => d.name === dsParam || d.id === dsParam) ||
              pickPrimaryDatasource(dashboardDs)
          );
          return;
        }

        // Tableau dashboard extension: datasource comes from the live session only
        const ctx = await initTableauExtension();
        if (cancelled) return;
        setContext(ctx);
        setActiveDatasource(pickSessionDatasource(ctx.datasources));
      } catch (e) {
        if (!cancelled) {
          setBootError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mock, serverMode]);

  const runAnalysis = useCallback(async () => {
    if (!context || !activeDatasource) return;

    setLoading(true);
    setError(null);
    try {
      const dsName = activeDatasource.name;
      let narrative: NarrativeResult;

      if (mock) {
        narrative = await fetchNarrative(getMockDatasourceTable(dsName));
      } else if (!serverMode && window.tableau?.extensions) {
        // Session datasource from whatever dashboard this extension is on — no PAT
        const payload = await loadSessionDatasourceTable(activeDatasource, context);
        narrative = await fetchNarrative(payload, true);
      } else {
        // Browser server-mode fallback (PAT)
        try {
          narrative = await fetchNarrativeFromDatasource({
            datasourceLuid:
              activeDatasource.id && activeDatasource.id.includes("-")
                ? activeDatasource.id
                : undefined,
            datasourceName: dsName,
            workbookName: context.workbookName,
            dashboardName: context.dashboardName,
            fieldCaptions: apFieldCaptions(dsName),
          });
        } catch (dsErr) {
          if (context.workbook?.id || context.contentUrl) {
            narrative = await fetchNarrativeFromWorkbook({
              workbookId: context.workbook?.id || undefined,
              contentUrl: context.contentUrl || undefined,
              workbookName: context.workbookName,
              viewName: queryParam("view") || "Executive Summary",
              dashboardName: context.dashboardName,
            });
          } else {
            throw dsErr;
          }
        }
      }

      setResult(narrative);
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [context, activeDatasource, mock, serverMode]);

  useEffect(() => {
    if (!context || !activeDatasource) return;
    void runAnalysis();
  }, [context, activeDatasource, runAnalysis]);

  if (bootError) {
    return (
      <div className="app shell">
        <header className="top">
          <div>
            <p className="eyebrow">Narrative Insights</p>
            <h1>Unable to connect</h1>
          </div>
        </header>
        <div className="error-box">{bootError}</div>
        <p className="hint">
          In Tableau, place this extension on a dashboard — it reads the datasource from that
          session. Browser tests:{" "}
          <a href="?contentUrl=AccountsPayableAI-MCP&datasource=AP%20Dataset">
            ?contentUrl=AccountsPayableAI-MCP&datasource=AP Dataset
          </a>
        </p>
      </div>
    );
  }

  return (
    <div className="app shell">
      <header className="top">
        <div>
          <p className="eyebrow">Narrative Insights</p>
          <h1>{context?.dashboardName || "AI Generated Summary"}</h1>
          <p className="sub">
            {activeDatasource?.name || "Connecting datasource…"}
            {context?.workbookName ? ` · ${context.workbookName}` : ""}
            {result?.context.dateRange
              ? ` · ${result.context.dateRange.min} → ${result.context.dateRange.max}`
              : ""}
            {mock ? " · mock data" : !serverMode ? " · this dashboard’s datasource" : ""}
          </p>
        </div>
        <div className="controls">
          <button
            type="button"
            onClick={() => void runAnalysis()}
            disabled={loading || !activeDatasource}
          >
            {loading ? "Analyzing…" : "Refresh"}
          </button>
        </div>
      </header>

      {error && <div className="error-box">{error}</div>}

      {loading && !result && (
        <div className="loading-panel">
          <div className="pulse" />
          <p>
            {serverMode
              ? "Querying datasource and generating insights…"
              : "Reading datasource from this dashboard session and generating insights…"}
          </p>
        </div>
      )}

      {result && (
        <InsightSections sections={displaySections(result)} />
      )}
    </div>
  );
}
