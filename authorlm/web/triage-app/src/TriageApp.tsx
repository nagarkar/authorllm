import {
  useDeferredValue,
  useEffect,
  useState,
  startTransition,
} from "react";
import {
  CompactSelection,
  DataEditor,
  GridCellKind,
  type EditableGridCell,
  type EditListItem,
  type GridCell,
  type GridColumn,
  type GridSelection,
  type Item,
} from "@glideapps/glide-data-grid";
import type {
  ActionSchema,
  Analysis,
  Draft,
  Evidence,
  Recommendation,
  RecommendationResult,
  Snapshot,
  Transport,
  TriageRow,
  TriageType,
} from "./types";
// The canonical tutorial, imported at build time from the doc that ships
// with the package (authorlm/docs/writing-essays-tutorial.md). One copy,
// one source of truth: the Help tab can never drift from the doc.
import tutorial from "../../../docs/writing-essays-tutorial.md?raw";
import { renderMarkdown } from "./markdown";

type View = "pending" | "all" | "decided" | "outdated";
type ReviewState = "any" | "recommended" | "staged" | "unreviewed";
type ColumnDef = GridColumn & {
  key: string;
  source: "database" | "analysis" | "recommendation" | "decision";
  editable?: string;
};
type FieldFilter = {value?: string; min?: string; max?: string};
type FieldFilterDef = {
  key: string;
  id: string;
  label: string;
  control: "select" | "range" | "count";
  source: "database" | "analysis";
  options?: string[];
};
type AliasWizard = {ids: string[]; index: number; ignored: number};

const EMPTY_SELECTION: GridSelection = {
  columns: CompactSelection.empty(),
  rows: CompactSelection.empty(),
};

function textValue(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map(textValue).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function scoreTone(score?: number): string {
  if (score == null) return "unscored";
  if (score >= 80) return "strong";
  if (score >= 55) return "mixed";
  return "weak";
}

function shortLabel(row: TriageRow): string {
  if (row.name) return row.name;
  if (row.statement) {
    const text = String(row.statement);
    return text.length > 80 ? `${text.slice(0, 77)}...` : text;
  }
  return `${row.from_name} -${row.relation}-> ${row.to_name}`;
}

/**
 * The Help tab's body. It reads no snapshot and makes no request — the
 * tutorial is inlined at build time — which is what lets the boot-error
 * screen offer it too, when the backend is exactly what is missing.
 */
function HelpDoc() {
  return (
    <section className="help-doc">
      <span className="kicker">AUTHORLM / HELP</span>
      <p className="help-doc-intro">
        This is the workbench's help. It covers the beat loop end to end,
        and it covers the three triage tabs beside it too: the concepts,
        edges and proposals you rule on here are the same graph the beat
        loop draws its grounding from, and the decisions you stage and
        apply on those tabs are recorded as the same author evidence a
        beat verdict is. Nothing on any tab — analysis, safe
        recommendation or accepted beat — changes the manuscript or the
        graph until you apply or accept it. The help button in the
        masthead still explains the current tab's own verbs and analyzer.
      </p>
      <article className="help-doc-body">{renderMarkdown(tutorial)}</article>
    </section>
  );
}

function cardKicker(type: TriageType, row: any): string {
  if (type === "edges") return textValue(row.relation);
  return textValue(row.kind);
}

function cardTitle(type: TriageType, row: any): string {
  if (type === "concepts") return textValue(row.name);
  if (type === "proposals") return textValue(row.target_name);
  if (type === "critique") return textValue(row.statement);
  return `${textValue(row.from_name)} -> ${textValue(row.to_name)}`;
}

export function TriageApp({transport, manuscript}: {transport: Transport; manuscript?: string}) {
  const [triageType, setTriageType] = useState<TriageType>("concepts");
  const [profileId, setProfileId] = useState<string>();
  const [profileVersion, setProfileVersion] = useState<string>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [gridFocus, setGridFocus] = useState<GridSelection>(EMPTY_SELECTION);
  const [view, setView] = useState<View>("pending");
  const [reviewState, setReviewState] = useState<ReviewState>("any");
  const [recommendations, setRecommendations] = useState<Record<string, Recommendation>>({});
  const [recommending, setRecommending] = useState(false);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [sort, setSort] = useState("score-desc");
  const [fieldFilters, setFieldFilters] = useState<Record<string, FieldFilter>>({});
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpTab, setHelpTab] = useState(false);
  const [drawer, setDrawer] = useState<{row: TriageRow; field: string}>();
  const [actionDialog, setActionDialog] = useState<ActionSchema>();
  const [parameterValue, setParameterValue] = useState("");
  const [parameterSearch, setParameterSearch] = useState("");
  const [reasonValue, setReasonValue] = useState("");
  const [aliasWizard, setAliasWizard] = useState<AliasWizard>();
  const [applyOpen, setApplyOpen] = useState(false);
  // Grid layout preferences: per-column width overrides, hidden columns,
  // and the Columns popover. Persisted per triage type in localStorage so
  // the author's layout survives reloads; storage failures are harmless.
  const [colWidths, setColWidths] = useState<Record<string, number>>({});
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(new Set());
  const [columnsOpen, setColumnsOpen] = useState(false);

  useEffect(() => {
    try {
      setColWidths(JSON.parse(localStorage.getItem(`triage.widths.${triageType}`) ?? "{}"));
      setHiddenCols(new Set(JSON.parse(localStorage.getItem(`triage.hidden.${triageType}`) ?? "[]")));
    } catch {
      setColWidths({});
      setHiddenCols(new Set());
    }
    setColumnsOpen(false);
  }, [triageType]);

  function resizeColumn(id: string, width: number) {
    setColWidths((current) => {
      const next = {...current, [id]: Math.max(60, Math.round(width))};
      try { localStorage.setItem(`triage.widths.${triageType}`, JSON.stringify(next)); } catch { /* per-viewer nicety */ }
      return next;
    });
  }

  function toggleColumn(id: string, all: string[]) {
    setHiddenCols((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      if (next.size >= all.length) return current;  // never hide everything
      try { localStorage.setItem(`triage.hidden.${triageType}`, JSON.stringify([...next])); } catch { /* per-viewer nicety */ }
      return next;
    });
  }
  const [applying, setApplying] = useState<{completed: number; total: number}>();
  const [syncPrompt, setSyncPrompt] = useState<{ids: string[]; reanalyze: boolean}>();
  const [syncReport, setSyncReport] = useState<Record<string, unknown>>();
  const [analysis, setAnalysis] = useState<{
    runId: string;
    completed: number;
    total: number;
    remaining: string[];
    batchSize: number;
    failed?: string;
  }>();

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(undefined);
    transport.request<Snapshot>("snapshot", {
      manuscript,
      triage_type: triageType,
      profile_id: profileId,
      profile_version: profileVersion,
    }).then((data) => {
      if (!active) return;
      startTransition(() => {
        setSnapshot(data);
        if (data.profile) {
          setProfileId(data.profile.id);
          setProfileVersion(String(data.profile.version));
        }
        setSelected(new Set());
        setRecommendations({});
        setReviewState("any");
        setGridFocus(EMPTY_SELECTION);
        setLoading(false);
      });
    }).catch((reason: Error) => {
      if (active) {
        setError(reason.message);
        setLoading(false);
      }
    });
    return () => { active = false; };
  }, [transport, manuscript, triageType, profileId, profileVersion]);

  async function refresh(preserveSelection = true) {
    const data = await transport.request<Snapshot>("snapshot", {
      manuscript,
      triage_type: triageType,
      profile_id: profileId,
      profile_version: profileVersion,
    });
    setSnapshot(data);
    setRecommendations((current) => Object.fromEntries(
      Object.entries(current).filter(([id, recommendation]) => {
        const row = data.rows.find((candidate) => candidate.id === id);
        return row?.pending && !row.draft && row.version === recommendation.object_version;
      }),
    ));
    setSelected((current) => {
      if (!preserveSelection) return new Set();
      const valid = new Set(data.rows.map((row) => row.id));
      return new Set([...current].filter((id) => valid.has(id)));
    });
    return data;
  }

  if (loading && !snapshot) {
    return <div className="boot-screen"><span />Setting out the manuscript...</div>;
  }
  if (!snapshot) {
    // Help is most wanted when the backend is down, and it needs no
    // backend: the tutorial is inlined at build time. The boot error
    // stays on screen above it rather than being replaced by it.
    return (
      <main className="app-shell">
        <div className={`boot-error ${helpTab ? "compact" : ""}`}>{error || "The Triage App could not load."}</div>
        <nav className="tab-rail" aria-label="Help">
          <button className={helpTab ? "active" : ""}
                  onClick={() => setHelpTab((open) => !open)}>
            <span>05</span>help
          </button>
          <div className="tab-rule" />
        </nav>
        {helpTab && <HelpDoc />}
      </main>
    );
  }
  const currentSnapshot = snapshot;

  function recommendationFor(row: TriageRow): Recommendation | undefined {
    const recommendation = recommendations[row.id];
    return recommendation && row.pending && !row.draft
      && recommendation.object_version === row.version
      ? recommendation : undefined;
  }

  const filterDefs: FieldFilterDef[] = [
    ...snapshot.schema.columns.map((column) => ({column, source: "database" as const})),
    ...snapshot.schema.analysis_columns.map((column) => ({column, source: "analysis" as const})),
  ].flatMap<FieldFilterDef>(({column, source}) => {
    const key = `${source}:${column.id}`;
    if (["enum", "status"].includes(column.kind)) {
      const options = [...new Set(snapshot.rows
        .map((row) => textValue(source === "database" ? row[column.id] : row.analysis?.[column.id]))
        .filter(Boolean))].sort((a, b) => a.localeCompare(b));
      return [{key, id: column.id, label: column.label, control: "select" as const, source, options}];
    }
    if ((column.kind === "number" && column.id !== "version") || column.kind === "score") {
      return [{key, id: column.id, label: column.label, control: "range" as const, source}];
    }
    if (["list", "evidence"].includes(column.kind)) {
      return [{key, id: column.id, label: `${column.label} count`, control: "count" as const, source}];
    }
    return [];
  });

  function updateFieldFilter(key: string, patch: Partial<FieldFilter>) {
    setFieldFilters((current) => {
      const value = {...current[key], ...patch};
      const next = {...current};
      if (!value.value && !value.min && !value.max) delete next[key];
      else next[key] = value;
      return next;
    });
  }

  const normalizedQuery = deferredQuery.trim().toLocaleLowerCase();
  let filteredRows = snapshot.rows.filter((row) => {
    const recommendation = recommendationFor(row);
    if (view === "pending" && !row.pending) return false;
    if (view === "decided" && row.pending) return false;
    if (view === "outdated" && row.analysis?.state !== "outdated") return false;
    if (reviewState === "recommended" && !recommendation) return false;
    if (reviewState === "staged" && !row.draft) return false;
    if (reviewState === "unreviewed" && (row.draft || recommendation)) return false;
    if (normalizedQuery && !JSON.stringify({row, recommendation}).toLocaleLowerCase().includes(normalizedQuery)) return false;
    return filterDefs.every((filter) => {
      const active = fieldFilters[filter.key];
      if (!active) return true;
      const raw = filter.source === "database" ? row[filter.id] : row.analysis?.[filter.id];
      if (filter.control === "select") return textValue(raw) === active.value;
      const value = filter.control === "count" ? (Array.isArray(raw) ? raw.length : 0) : Number(raw);
      if (!Number.isFinite(value)) return false;
      if (active.min && value < Number(active.min)) return false;
      if (active.max && value > Number(active.max)) return false;
      return true;
    });
  });
  filteredRows = [...filteredRows].sort((a, b) => {
    if (sort === "score-desc") {
      const delta = (b.analysis?.score ?? -1) - (a.analysis?.score ?? -1);
      if (delta) return delta;
    }
    return shortLabel(a).localeCompare(shortLabel(b));
  });

  const columns: ColumnDef[] = [
    ...snapshot.schema.columns.map((column, index) => ({
      id: `db:${column.id}`,
      key: column.id,
      title: column.label,
      width: column.width || 140,
      group: "DATABASE",
      source: "database" as const,
      editable: column.editable,
      themeOverride: {bgCell: "#fffaf0", bgHeader: "#e8dfcf", textHeader: "#51483b"},
    })),
    ...snapshot.schema.analysis_columns.map((column) => ({
      id: `analysis:${column.id}`,
      key: column.id,
      title: column.label,
      width: column.width || 150,
      group: `ANALYSIS / ${(snapshot.profile?.label ?? "").toUpperCase()}`,
      source: "analysis" as const,
      themeOverride: {bgCell: "#f2f7f3", bgHeader: "#dcebe2", textHeader: "#254d3a"},
    })),
    {
      id: "recommendation:action", key: "action", title: "Recommended", width: 145,
      group: "DETERMINISTIC RECOMMENDATION", source: "recommendation",
      themeOverride: {bgCell: "#fff8df", bgHeader: "#eadca7", textHeader: "#604d0f"},
    },
    {
      id: "recommendation:reason", key: "reason", title: "Rule / reason", width: 300,
      group: "DETERMINISTIC RECOMMENDATION", source: "recommendation",
      themeOverride: {bgCell: "#fff8df", bgHeader: "#eadca7", textHeader: "#604d0f"},
    },
    {
      id: "decision:action", key: "action", title: "Decision", width: 145,
      group: "AUTHOR DECISION", source: "decision",
      themeOverride: {bgCell: "#fff3ea", bgHeader: "#f2d9c7", textHeader: "#74371f"},
    },
    {
      id: "decision:reason", key: "reason", title: "Reason", width: 270,
      group: "AUTHOR DECISION", source: "decision",
      themeOverride: {bgCell: "#fff3ea", bgHeader: "#f2d9c7", textHeader: "#74371f"},
    },
  ];

  // What the grid actually shows: hidden columns removed, author-resized
  // widths applied, and the first visible column given its own blank
  // group — it is the frozen region, and glide paints group labels once
  // per region, so sharing a label would render it twice.
  const visibleColumns: ColumnDef[] = columns
    .filter((column) => !hiddenCols.has(String(column.id)))
    .map((column, index) => ({
      ...column,
      width: colWidths[String(column.id)] ?? (column as {width?: number}).width ?? 140,
      group: index === 0 ? "" : column.group,
    }));

  // Rows grow to fit their longest wrapped text cell (capped at ten
  // lines), so long items read in place instead of truncating.
  function rowHeightFor(row: TriageRow): number {
    let lines = 1;
    for (const column of visibleColumns) {
      let value: unknown;
      if (column.source === "database") value = stagedRevision(row, column) ?? row[column.key];
      else if (column.source === "analysis" && column.key !== "evidence") value = row.analysis?.[column.key];
      else if (column.source === "decision") value = column.key === "reason" ? row.draft?.reason : undefined;
      const text = textValue(value);
      if (text.length < 24) continue;
      const width = Math.max(60, Number((column as {width?: number}).width ?? 140)) - 24;
      const perLine = Math.max(8, Math.floor(width / 6.6));
      lines = Math.max(lines, Math.min(10, Math.ceil(text.length / perLine)));
    }
    return 14 + lines * 19;
  }

  function actionAcceptsReason(actionId: string): boolean {
    return !!currentSnapshot.schema.actions.find((action) => action.id === actionId)?.reason;
  }

  // The staged wording of an inline revision (e.g. critique "revise").
  // The grid must display it in place of the database value: the draft is
  // the author's edit, and re-rendering the old text after they commit
  // reads as the edit having been lost.
  function stagedRevision(row: TriageRow, column: ColumnDef): string | undefined {
    if (column.source !== "database" || !column.editable) return undefined;
    if (row.draft?.action !== column.editable) return undefined;
    const parameterId = currentSnapshot.schema.actions
      .find((action) => action.id === column.editable)?.parameter?.id ?? "text";
    const staged = row.draft.parameters?.[parameterId];
    return typeof staged === "string" ? staged : undefined;
  }

  function cellContent([columnIndex, rowIndex]: Item): GridCell {
    const column = visibleColumns[columnIndex];
    const row = filteredRows[rowIndex];
    const revision = stagedRevision(row, column);
    const colors = column.source === "database"
      ? {bgCell: revision !== undefined ? "#fff3ea" : "#fffaf0"}
      : column.source === "analysis"
        ? {bgCell: row.analysis?.state === "outdated" ? "#fff0d7" : "#f2f7f3"}
        : column.source === "recommendation"
          ? {bgCell: recommendationFor(row) ? "#fff8df" : "#fffcf2"}
          : {bgCell: row.draft?.state === "conflict" ? "#ffe2dd" : "#fff3ea"};
    let value: unknown;
    if (column.source === "database") value = revision ?? row[column.key];
    else if (column.source === "analysis") value = row.analysis?.[column.key];
    else if (column.source === "recommendation") {
      const recommendation = recommendationFor(row);
      value = column.key === "action"
        ? recommendation?.action
        : recommendation && `${recommendation.rule_label}: ${recommendation.reason}`;
    }
    else value = column.key === "action" ? row.draft?.action : row.draft?.reason;
    if (column.source === "analysis" && column.key === "evidence") {
      const evidence = value as Evidence[] | undefined;
      return {
        kind: GridCellKind.Text,
        data: evidence?.length ? `${evidence.length} passage${evidence.length === 1 ? "" : "s"}` : "",
        displayData: evidence?.length ? `${evidence.length} passage${evidence.length === 1 ? "" : "s"}` : "",
        allowOverlay: false,
        readonly: true,
        cursor: evidence?.length ? "pointer" : "default",
        themeOverride: colors,
      };
    }
    if (column.source === "analysis" && (column.key === "score" || column.key === "confidence")) {
      const score = typeof value === "number" ? value : undefined;
      return {
        kind: GridCellKind.Number,
        data: score,
        displayData: score == null ? "" : `${row.analysis?.state === "outdated" ? "OLD " : ""}${score}`,
        allowOverlay: false,
        readonly: true,
        contentAlign: "right",
        themeOverride: colors,
      };
    }
    const editableReason = column.source === "decision" && column.key === "reason"
      && !!row.draft && actionAcceptsReason(row.draft.action);
    const editableItem = column.source === "database" && !!column.editable;
    return {
      kind: GridCellKind.Text,
      data: textValue(value),
      displayData: textValue(value),
      // Every text cell opens on double-click: editable cells for
      // editing, the rest as a read-only overlay whose text can be
      // selected and copied (standard grid behavior).
      allowOverlay: true,
      readonly: !(editableReason || editableItem),
      allowWrapping: true,
      cursor: column.source === "analysis" && column.key === "why" ? "pointer" : "default",
      themeOverride: colors,
    };
  }

  let selectedRows = CompactSelection.empty();
  filteredRows.forEach((row, index) => {
    if (selected.has(row.id)) selectedRows = selectedRows.add(index);
  });
  const gridSelection: GridSelection = {...gridFocus, rows: selectedRows};

  function changeSelection(next: Set<string>) {
    setSelected(next);
  }

  function toggleCard(row: TriageRow) {
    const next = new Set(selected);
    if (next.has(row.id)) next.delete(row.id);
    else next.add(row.id);
    changeSelection(next);
  }

  function onGridSelectionChange(nextGrid: GridSelection) {
    // Clicking a cell proposes a selection with a focused cell and an
    // EMPTY row set. That is a focus change, not a deselection — the
    // author's checkboxes survive it (they clear only via the markers,
    // the Clear button, or Escape with nothing focused).
    if (nextGrid.current !== undefined && nextGrid.rows.length === 0) {
      setGridFocus({...nextGrid, rows: CompactSelection.empty()});
      return;
    }
    const visibleIds = new Set(filteredRows.map((row) => row.id));
    const next = new Set([...selected].filter((id) => !visibleIds.has(id)));
    for (const index of nextGrid.rows) {
      if (filteredRows[index]) next.add(filteredRows[index].id);
    }
    setGridFocus({...nextGrid, rows: CompactSelection.empty()});
    changeSelection(next);
  }

  async function stage(
    action: ActionSchema,
    parameter?: string,
    reason?: string,
    objectIds: string[] = [...selected],
    closeDialog = true,
  ): Promise<boolean> {
    if (!objectIds.length) return false;
    // Accept keeps a staged inline revision: the revision IS an accept in
    // the author's own wording, so plain Accept must not downgrade the row
    // back to the original text. Any other action (e.g. Reject) is a
    // change of mind and overwrites the revision as before.
    const editableActions = new Set(
      currentSnapshot.schema.columns.map((column) => column.editable).filter(Boolean));
    const revised = action.id === "accept"
      ? objectIds.filter((objectId) => {
          const draft = currentSnapshot.rows.find((row) => row.id === objectId)?.draft;
          return !!draft && editableActions.has(draft.action);
        })
      : [];
    const staging = objectIds.filter((objectId) => !revised.includes(objectId));
    const revisedNote = revised.length
      ? ` ${revised.length} edited row${revised.length === 1 ? " keeps its" : "s keep their"} revision (accepted in your wording).`
      : "";
    if (!staging.length) {
      setNotice(`Nothing to assign.${revisedNote}`);
      return true;
    }
    const parameters: Record<string, unknown> = {};
    if (action.parameter) parameters[action.parameter.id] = parameter;
    try {
      await transport.request("stage", {
        manuscript,
        triage_type: triageType,
        decisions: staging.map((objectId) => {
          const existing = currentSnapshot.rows.find((row) => row.id === objectId)?.draft;
          const stagedReason = action.reason
            ? reason === undefined ? existing?.reason : reason.trim() || undefined
            : undefined;
          return {object_id: objectId, action: action.id, parameters, reason: stagedReason};
        }),
      });
      if (closeDialog) {
        setActionDialog(undefined);
        setParameterValue("");
        setParameterSearch("");
        setReasonValue("");
      }
      setNotice(`${action.label} assigned to ${staging.length} selected row${staging.length === 1 ? "" : "s"}.${revisedNote}`);
      await refresh(true);
      return true;
    } catch (reason) {
      setError((reason as Error).message);
      return false;
    }
  }

  async function findRecommendations() {
    setRecommending(true);
    setError(undefined);
    try {
      const result = await transport.request<RecommendationResult>("recommendations", {
        manuscript,
        triage_type: triageType,
      });
      setRecommendations(Object.fromEntries(
        result.decisions.map((recommendation) => [recommendation.object_id, recommendation]),
      ));
      setView("pending");
      setReviewState("recommended");
      setNotice(result.decisions.length
        ? `${result.decisions.length} safe recommendation${result.decisions.length === 1 ? "" : "s"} found.`
          + (result.protected.length ? ` ${result.protected.length} protected row${result.protected.length === 1 ? " was" : "s were"} left untouched.` : "")
        : "No mechanically safe recommendations were found for this tab.");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setRecommending(false);
    }
  }

  async function stageRecommendations(objectIds?: string[]) {
    const ids = objectIds || [...selected].filter((id) => {
      const row = currentSnapshot.rows.find((candidate) => candidate.id === id);
      return row && recommendationFor(row);
    });
    if (!ids.length) {
      setNotice("Select at least one recommended row.");
      return;
    }
    try {
      await transport.request("stage_recommendations", {
        manuscript,
        triage_type: triageType,
        object_ids: ids,
      });
      setRecommendations((current) => Object.fromEntries(
        Object.entries(current).filter(([id]) => !ids.includes(id)),
      ));
      setNotice(`${ids.length} recommendation${ids.length === 1 ? "" : "s"} staged for your review.`);
      await refresh(true);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function unstageSelected() {
    const ids = currentSnapshot.rows
      .filter((row) => selected.has(row.id) && row.draft)
      .map((row) => row.id);
    if (!ids.length) return;
    try {
      await transport.request("unstage", {
        manuscript,
        triage_type: triageType,
        object_ids: ids,
      });
      setNotice(`${ids.length} staged decision${ids.length === 1 ? "" : "s"} removed.`);
      await refresh(true);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  function chooseAction(action: ActionSchema) {
    if (action.id === "alias") {
      const rows = currentSnapshot.rows.filter((row) => selected.has(row.id));
      const candidates = rows.filter((row) => row.draft?.action !== "alias");
      const ignored = rows.length - candidates.length;
      if (!candidates.length) {
        setNotice(ignored
          ? `Every selected row already has an alias merge assigned.`
          : "Select at least one row for alias merge.");
        return;
      }
      setAliasWizard({ids: candidates.map((row) => row.id), index: 0, ignored});
      setParameterValue("");
      setParameterSearch("");
      setReasonValue("");
      setActionDialog(action);
      return;
    }
    if (action.parameter || action.reason) {
      setParameterValue("");
      setParameterSearch("");
      setReasonValue("");
      setActionDialog(action);
    } else {
      void stage(action);
    }
  }

  function closeActionDialog() {
    setActionDialog(undefined);
    setAliasWizard(undefined);
    setParameterValue("");
    setParameterSearch("");
    setReasonValue("");
  }

  async function stageAliasStep() {
    if (!aliasWizard || !actionDialog) return;
    const objectId = aliasWizard.ids[aliasWizard.index];
    const saved = await stage(
      actionDialog, parameterValue, reasonValue, [objectId], false);
    if (!saved) return;
    const nextIndex = aliasWizard.index + 1;
    if (nextIndex >= aliasWizard.ids.length) {
      const message = `Alias merge assigned to ${aliasWizard.ids.length} row${aliasWizard.ids.length === 1 ? "" : "s"}.`
        + (aliasWizard.ignored ? ` ${aliasWizard.ignored} already assigned and skipped.` : "")
        + " Review, then apply selected.";
      closeActionDialog();
      setNotice(message);
      return;
    }
    setAliasWizard({...aliasWizard, index: nextIndex});
    setParameterValue("");
    setParameterSearch("");
    setReasonValue("");
  }

  async function editReasons(edits: readonly EditListItem[]) {
    const decisions = [];
    for (const edit of edits) {
      const column = visibleColumns[edit.location[0]];
      const row = filteredRows[edit.location[1]];
      if (!column || !row) continue;
      const value = edit.value as EditableGridCell & {data?: unknown};
      // An inline edit of an editable database column (e.g. the critique
      // Item) stages the schema-named action ("revise") with the new
      // wording: the row shows as a dirty draft, and Accept/Apply lands
      // it — no separate Revise & accept round trip.
      if (column.source === "database" && column.editable) {
        const text = textValue(value.data).trim();
        if (!text || text === textValue(row[column.key])) continue;
        decisions.push({
          object_id: row.id,
          action: column.editable,
          parameters: {text},
          reason: row.draft?.reason,
        });
        continue;
      }
      if (column.source !== "decision" || column.key !== "reason" || !row.draft
          || !actionAcceptsReason(row.draft.action)) continue;
      decisions.push({
        object_id: row.id,
        action: row.draft.action,
        parameters: row.draft.parameters,
        reason: textValue(value.data),
      });
    }
    if (!decisions.length) return;
    try {
      await transport.request("stage", {manuscript, triage_type: triageType, decisions});
      await refresh(true);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function saveReason(row: TriageRow, reason: string) {
    if (!row.draft || !actionAcceptsReason(row.draft.action)
        || reason === (row.draft.reason || "")) return;
    try {
      await transport.request("stage", {
        manuscript,
        triage_type: triageType,
        decisions: [{
          object_id: row.id,
          action: row.draft.action,
          parameters: row.draft.parameters,
          reason,
        }],
      });
      await refresh(true);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }

  async function applySelected() {
    const drafts = currentSnapshot.rows.filter((row) => selected.has(row.id) && row.draft);
    const sequential = drafts.length > 1 && drafts.some((row) => row.draft?.action === "alias");
    const batches = sequential ? drafts.map((row) => [row.id]) : [[...selected]];
    const remaining = new Set(selected);
    let completed = 0;
    setApplying({completed, total: selected.size});
    try {
      for (const objectIds of batches) {
        const result = await transport.request<{count: number}>("apply", {
          manuscript, triage_type: triageType, object_ids: objectIds,
        });
        completed += result.count;
        objectIds.forEach((id) => remaining.delete(id));
        setApplying({completed, total: selected.size});
        setSelected(new Set(remaining));
      }
      setApplyOpen(false);
      setSelected(new Set());
      setNotice(`Applied ${completed} decision${completed === 1 ? "" : "s"}.`);
      await refresh(false);
    } catch (reason) {
      setApplyOpen(false);
      const message = (reason as Error).message;
      setError(completed
        ? `Applied ${completed} decision${completed === 1 ? "" : "s"}, then stopped: ${message}`
        : message);
      await refresh(true);
    } finally {
      setApplying(undefined);
    }
  }

  function analysisCandidates(reanalyze: boolean, selectedOnly = false): string[] {
    const source = selectedOnly
      ? currentSnapshot.rows.filter((row) => selected.has(row.id))
      : filteredRows;
    return source.filter((row) => reanalyze || !row.analysis || row.analysis.state === "outdated")
      .map((row) => row.id);
  }

  function askToAnalyze(ids: string[], reanalyze: boolean) {
    if (!ids.length) {
      setNotice(reanalyze ? "There are no rows to reanalyze." : "Every row has a current assessment.");
      return;
    }
    if (currentSnapshot.sync.linked) {
      setSyncReport(undefined);
      setSyncPrompt({ids, reanalyze});
    } else {
      void beginAnalysis(ids);
    }
  }

  async function beginAnalysis(ids: string[]) {
    if (!currentSnapshot.profile) return;
    setSyncPrompt(undefined);
    setError(undefined);
    setNotice(undefined);
    try {
      const run = await transport.request<{
        run_id: string; batch_size: number; requested_ids: string[];
      }>("start_analysis", {
        manuscript,
        triage_type: triageType,
        object_ids: ids,
        profile_id: currentSnapshot.profile!.id,
        profile_version: currentSnapshot.profile!.version,
      });
      const state = {
        runId: run.run_id, completed: 0, total: run.requested_ids.length,
        remaining: run.requested_ids, batchSize: run.batch_size,
      };
      setAnalysis(state);
      await continueAnalysis(state);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function continueAnalysis(current: NonNullable<typeof analysis>) {
    let remaining = [...current.remaining];
    let completed = current.total - remaining.length;
    let total = current.total;
    try {
      while (remaining.length) {
        const batch = remaining.slice(0, current.batchSize);
        const result = await transport.request<{
          results?: Array<Analysis & {object_id: string}>;
          remaining_ids: string[];
          completed: number;
          total: number;
        }>("analyze_batch", {
          manuscript, run_id: current.runId, object_ids: batch,
        });
        completed = result.completed;
        total = result.total;
        remaining = result.remaining_ids;
        if (result.results) {
          setSnapshot((existing) => existing && ({
            ...existing,
            rows: existing.rows.map((row) => {
              const output = result.results?.find((item) => item.object_id === row.id);
              return output ? {...row, analysis: {...output, state: "current"}} : row;
            }),
          }));
        }
        setAnalysis({...current, completed, total, remaining});
      }
      setNotice(`Analysis complete: ${completed} row${completed === 1 ? "" : "s"} assessed.`);
      setAnalysis(undefined);
      await refresh(true);
    } catch (reason) {
      setAnalysis({...current, completed, total, remaining, failed: (reason as Error).message});
    }
  }

  async function reconcileThenAnalyze() {
    if (!syncPrompt) return;
    try {
      const report = await transport.request<Record<string, unknown>>(
        "reconcile_docs", {manuscript});
      setSyncReport(report);
      const conflicts = report.conflicts as unknown[] | undefined;
      const errors = report.errors as unknown[] | undefined;
      if (!conflicts?.length && !errors?.length) await beginAnalysis(syncPrompt.ids);
      else await refresh(true);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  const selectedDrafts = snapshot.rows.filter((row) => selected.has(row.id) && row.draft);
  const selectedRecommendations = snapshot.rows.filter(
    (row) => selected.has(row.id) && recommendationFor(row));
  const activeRefinementCount = Object.keys(fieldFilters).length
    + (reviewState === "any" ? 0 : 1);
  const canApply = selected.size > 0 && selectedDrafts.length === selected.size;
  const appliesSequentially = selectedDrafts.length > 1
    && selectedDrafts.some((row) => row.draft?.action === "alias");
  const actionCounts = selectedDrafts.reduce<Record<string, number>>((counts, row) => {
    const action = row.draft!.action;
    counts[action] = (counts[action] || 0) + 1;
    return counts;
  }, {});
  const chosenAction = actionDialog;
  const aliasWizardRow = aliasWizard
    ? snapshot.rows.find((row) => row.id === aliasWizard.ids[aliasWizard.index])
    : undefined;
  let parameterOptions = chosenAction?.parameter?.options || [];
  if (chosenAction?.parameter?.source === "concepts") {
    parameterOptions = snapshot.rows
      .filter((row) => !selected.has(row.id) && row.name && row.status !== "retired")
      .map((row) => `${row.id}|${row.name}`);
  }
  if (chosenAction?.parameter?.source === "edge_endpoints") {
    const row = aliasWizardRow
      || snapshot.rows.find((candidate) => selected.has(candidate.id));
    parameterOptions = row ? [
      `${row.from_node}|${row.from_name}`,
      `${row.to_node}|${row.to_name}`,
    ] : [];
  }
  const searchableParameter = chosenAction?.parameter?.source === "concepts";
  const allMatchingParameterOptions = searchableParameter
    ? parameterOptions.filter((option) => option.toLocaleLowerCase().includes(parameterSearch.trim().toLocaleLowerCase()))
    : parameterOptions;
  const matchingParameterOptions = allMatchingParameterOptions.slice(0, 40);

  function chooseParameterOption(option: string) {
    const separator = option.indexOf("|");
    const value = separator < 0 ? option : option.slice(0, separator);
    const label = separator < 0 ? option : option.slice(separator + 1);
    setParameterValue(value);
    setParameterSearch(label);
  }

  return (
    <main className="app-shell">
      <header className="masthead">
        <div className="brand-block">
          <span className="kicker">AUTHORLM / EDITORIAL WORKBENCH</span>
          <h1>Triage <i>App</i></h1>
        </div>
        <div className="manuscript-stamp">
          <span>MANUSCRIPT</span>
          <strong>{snapshot.manuscript.name}</strong>
          <small>{snapshot.manuscript_version
            ? `${snapshot.manuscript_version.local_dirty ? "local edits after " : ""}collected v${snapshot.manuscript_version.version_no}`
            : "not collected"}</small>
        </div>
        <button className="icon-button" onClick={() => setHelpOpen(true)} aria-label="Open help">?</button>
      </header>

      <nav className="tab-rail" aria-label="Triage type">
        {(["concepts", "edges", "proposals", "critique"] as TriageType[]).map((tab, i) => (
          <button key={tab} className={!helpTab && triageType === tab ? "active" : ""}
                  disabled={!!analysis}
                  onClick={() => {
                    setHelpTab(false);
                    setProfileId(undefined); setProfileVersion(undefined); setTriageType(tab);
                    setView("pending"); setReviewState("any"); setRecommendations({});
                    setFieldFilters({}); setMobileFiltersOpen(false);
                  }}>
            <span>{String(i + 1).padStart(2, "0")}</span>{tab}
          </button>
        ))}
        <button className={helpTab ? "active" : ""}
                onClick={() => { setHelpTab(true); setMobileFiltersOpen(false); }}>
          <span>05</span>help
        </button>
        <div className="tab-rule" />
        {!helpTab && <div className="row-count"><strong>{filteredRows.length}</strong> shown / {snapshot.rows.length}</div>}
      </nav>

      {helpTab && <HelpDoc />}
      {!helpTab && <>

      {snapshot.incomplete_runs.length > 0 && !analysis && (
        <section className="run-banner">
          <div><strong>Incomplete analysis</strong><span>{snapshot.incomplete_runs[0].remaining_ids.length} rows remain from an interrupted run.</span></div>
          <button onClick={() => {
            const run = snapshot.incomplete_runs[0];
            const next = {runId: run.id,
              completed: run.requested_ids.length - run.remaining_ids.length,
              total: run.requested_ids.length, remaining: run.remaining_ids,
              batchSize: 12};
            setAnalysis(next);
            void continueAnalysis(next);
          }}>Resume missing</button>
        </section>
      )}

      <section className="control-deck">
        <div className="filters">
          <label className="search-box"><span>Find</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${triageType}...`} /></label>
          <button className="mobile-filter-toggle" onClick={() => setMobileFiltersOpen((open) => !open)} aria-expanded={mobileFiltersOpen}>
            Filters{activeRefinementCount ? ` (${activeRefinementCount})` : ""}
            <small>{filteredRows.length} / {snapshot.rows.length}</small>
          </button>
          <label className={`refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`}><span>View</span><select value={view} onChange={(event) => setView(event.target.value as View)}>
            <option value="pending">Pending</option><option value="all">All</option>
            <option value="decided">Previously decided</option><option value="outdated">Outdated analysis</option>
          </select></label>
          <label className={`refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`}><span>Review state</span><select value={reviewState} onChange={(event) => setReviewState(event.target.value as ReviewState)}>
            <option value="any">Any</option><option value="recommended">Recommended</option>
            <option value="staged">Staged by you</option><option value="unreviewed">Unreviewed</option>
          </select></label>
          <label className={`refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`}><span>Order</span><select value={sort} onChange={(event) => setSort(event.target.value)}>
            <option value="score-desc">Score: high to low</option><option value="name">Name</option>
          </select></label>
          {snapshot.profile && <label className={`profile-select refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`}><span>Analyzer</span><select disabled={!!analysis} value={`${snapshot.profile.id}@${snapshot.profile.version}`} onChange={(event) => {
            const [nextId, nextVersion] = event.target.value.split("@", 2);
            setProfileId(nextId); setProfileVersion(nextVersion); setFieldFilters({});
          }}>
            {snapshot.profiles.map((profile) => <option key={`${profile.id}@${profile.version}`} value={`${profile.id}@${profile.version}`}>{profile.label} v{profile.version}</option>)}
          </select></label>}
          {filterDefs.map((filter) => filter.control === "select" ? (
            <label className={`schema-filter refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`} key={filter.key}>
              <span>{filter.label}</span>
              <select value={fieldFilters[filter.key]?.value || ""} onChange={(event) => updateFieldFilter(filter.key, {value: event.target.value})}>
                <option value="">Any</option>
                {filter.options?.map((option) => <option value={option} key={option}>{option}</option>)}
              </select>
            </label>
          ) : (
            <label className={`schema-filter range-filter refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`} key={filter.key}>
              <span>{filter.label}</span>
              <span className="range-inputs">
                <input type="number" inputMode="numeric" placeholder="Min" aria-label={`Minimum ${filter.label}`} value={fieldFilters[filter.key]?.min || ""} onChange={(event) => updateFieldFilter(filter.key, {min: event.target.value})} />
                <i>to</i>
                <input type="number" inputMode="numeric" placeholder="Max" aria-label={`Maximum ${filter.label}`} value={fieldFilters[filter.key]?.max || ""} onChange={(event) => updateFieldFilter(filter.key, {max: event.target.value})} />
              </span>
            </label>
          ))}
          {!!activeRefinementCount && <button className={`clear-filters refine-control ${mobileFiltersOpen ? "mobile-open" : ""}`} onClick={() => { setFieldFilters({}); setReviewState("any"); }}>Clear refinements</button>}
        </div>
        <div className="analysis-actions">
          <button className="button recommendation-button" onClick={() => void findRecommendations()} disabled={recommending || !!analysis}>{recommending ? "Finding..." : "Find safe recommendations"}</button>
          {snapshot.profile && <>
          <button className="button quiet" onClick={() => askToAnalyze(analysisCandidates(false, true), false)} disabled={!selected.size || !!analysis}>Analyze selected</button>
          <button className="button ink" onClick={() => askToAnalyze(analysisCandidates(false), false)} disabled={!!analysis}>Analyze all</button>
          <button className="text-button" onClick={() => askToAnalyze(analysisCandidates(true), true)} disabled={!!analysis}>Reanalyze all</button>
          </>}
        </div>
      </section>

      {analysis && (
        <section className={`progress-strip ${analysis.failed ? "failed" : ""}`}>
          <div className="progress-copy">
            <strong>{analysis.failed ? "Analysis paused" : "Analyzing in batches"}</strong>
            <span>{analysis.completed} / {analysis.total} complete</span>
          </div>
          <div className="progress-track"><i style={{width: `${analysis.total ? analysis.completed / analysis.total * 100 : 0}%`}} /></div>
          {analysis.failed && <button onClick={() => continueAnalysis({...analysis, failed: undefined})}>Retry failed</button>}
        </section>
      )}

      {(error || notice) && <div className={error ? "flash error" : "flash notice"}>
        <span>{error || notice}</span><button onClick={() => { setError(undefined); setNotice(undefined); }}>Close</button>
      </div>}

      <section className={`selection-bar ${selected.size ? "" : "empty"}`}>
        <div className="selection-tools">
          <button onClick={() => changeSelection(new Set([...selected, ...filteredRows.map((row) => row.id)]))}>Select all filtered</button>
          <button onClick={() => changeSelection(new Set())} disabled={!selected.size}>Clear</button>
          <div className="columns-menu">
            <button onClick={() => setColumnsOpen((open) => !open)}>Columns</button>
            {columnsOpen && <div className="columns-popover">
              {columns.map((column) => (
                <label key={String(column.id)}>
                  <input
                    type="checkbox"
                    checked={!hiddenCols.has(String(column.id))}
                    onChange={() => toggleColumn(String(column.id), columns.map((c) => String(c.id)))}
                  />
                  {String(column.title)}
                </label>
              ))}
            </div>}
          </div>
          <span><strong>{selected.size}</strong> selected across the full result set</span>
        </div>
        <div className="verb-tools">
          <button className="recommendation-action" onClick={() => void stageRecommendations()} disabled={!selectedRecommendations.length}>Stage recommendations{selectedRecommendations.length ? ` (${selectedRecommendations.length})` : ""}</button>
          {snapshot.schema.actions.filter((action) => !action.hidden).map((action) => <button key={action.id} onClick={() => chooseAction(action)} disabled={!selected.size} title={action.help}>{action.label}</button>)}
          <button onClick={() => void unstageSelected()} disabled={!selectedDrafts.length}>Remove staged</button>
          <button className="apply-button" disabled={!canApply || !!applying} onClick={() => setApplyOpen(true)}>Apply selected</button>
        </div>
      </section>

      <section className="grid-frame">
        <div className="desktop-grid">
          <DataEditor
            width="100%" height="100%" columns={visibleColumns} rows={filteredRows.length}
            getCellContent={cellContent} rowMarkers="checkbox" smoothScrollX smoothScrollY
            rowSelectionMode="multi"
            freezeColumns={1} getCellsForSelection gridSelection={gridSelection}
            rowHeight={(index: number) => {
              const row = filteredRows[index];
              return row ? rowHeightFor(row) : 33;
            }}
            onColumnResize={(column, newSize) => resizeColumn(String((column as ColumnDef).id), newSize)}
            onGridSelectionChange={onGridSelectionChange}
            onCellsEdited={(edits) => { void editReasons(edits); return true; }}
            onPaste
            onCellClicked={(cell) => {
              const column = visibleColumns[cell[0]];
              const row = filteredRows[cell[1]];
              if (column?.source === "analysis" && ["why", "evidence"].includes(column.key)) setDrawer({row, field: column.key});
            }}
            theme={{
              accentColor: "#c94f2d", accentFg: "#fffaf0", bgCell: "#fffaf0",
              bgHeader: "#e8dfcf", bgHeaderHovered: "#ded3c2", bgHeaderHasFocus: "#d8cbb8",
              borderColor: "rgba(67,56,43,.16)", horizontalBorderColor: "rgba(67,56,43,.1)",
              textDark: "#29251f", textMedium: "#61594f", textLight: "#8c8275",
              fontFamily: "Avenir Next, Gill Sans, sans-serif", baseFontStyle: "13px",
              headerFontStyle: "600 11px", cellHorizontalPadding: 12,
            }}
          />
        </div>
        <div className="mobile-cards">
          {filteredRows.map((row) => {
            const recommendation = recommendationFor(row);
            return <article
            className={`triage-card ${recommendation ? "recommended" : ""} ${row.draft ? "staged" : ""} ${selected.has(row.id) ? "selected" : ""}`}
            data-triage-row-id={row.id}
            key={row.id}
            onClick={(event) => {
              const target = event.target as HTMLElement;
              if (target.closest("button, input, textarea, select, label, a")) return;
              toggleCard(row);
            }}
          >
            <label className="card-check"><input type="checkbox" checked={selected.has(row.id)} onChange={() => {
              const next = new Set(selected);
              if (next.has(row.id)) next.delete(row.id); else next.add(row.id);
              changeSelection(next);
            }} /><span /></label>
            <div className="card-heading"><small>{cardKicker(triageType, row)}</small><h2>{cardTitle(triageType, row)}</h2></div>
            <div className={`card-score ${scoreTone(row.analysis?.score)}`}><strong>{row.analysis?.score ?? "--"}</strong><span>keepability</span>{row.analysis?.state === "outdated" && <em>outdated</em>}</div>
            <p className="card-why">{row.analysis?.why || "Not analyzed yet."}</p>
            {row.analysis && <button className="evidence-link" onClick={() => setDrawer({row, field: "analysis"})}>
              View analysis{row.analysis.evidence?.length ? ` / ${row.analysis.evidence.length} passage${row.analysis.evidence.length === 1 ? "" : "s"}` : ""}
            </button>}
            {recommendation && <section className="card-recommendation">
              <div><span>Deterministic recommendation</span><strong>{recommendation.action}</strong></div>
              <p><b>{recommendation.rule_label}.</b> {recommendation.reason}</p>
              <button type="button" onClick={() => void stageRecommendations([row.id])}>Stage recommendation</button>
            </section>}
            {row.draft && actionAcceptsReason(row.draft.action) && <label className="card-reason"><span>Author reason (optional)</span><textarea defaultValue={row.draft.reason || ""} onBlur={(event) => void saveReason(row, event.target.value)} placeholder="Add your reason..." /></label>}
            <footer>
              <span>{row.status as string}</span>
              <strong>{row.draft
                ? `Staged by you: ${row.draft.action}`
                : recommendation
                  ? `Recommended: ${recommendation.action}`
                  : "Tap card to select"}</strong>
            </footer>
          </article>;
          })}
        </div>
      </section>

      </>}

      {helpOpen && <div className="overlay" onMouseDown={() => setHelpOpen(false)}><aside className="panel help-panel" onMouseDown={(event) => event.stopPropagation()}>
        <button className="panel-close" onClick={() => setHelpOpen(false)}>Close</button>
        <span className="kicker">SHARED AUTHORLM HELP</span><h2>{snapshot.schema.label} triage</h2>
        <p>{snapshot.schema.help}</p>
        <div className="help-list">{snapshot.schema.actions.map((action) => <div key={action.id}><strong>{action.label}</strong><span>{action.help}</span></div>)}</div>
        {snapshot.profile && <><hr /><h3>{snapshot.profile.label} v{snapshot.profile.version}</h3><p>{snapshot.profile.description}</p>
        {snapshot.profile.model && <p><strong>Model:</strong> {snapshot.profile.model}</p>}</>}
        <small>Safe recommendations are deterministic and remain separate from your staged decisions. Stage a recommendation to adopt it as your pending decision; only Apply selected changes the graph. Selecting or deselecting rows never changes staged decisions. Analysis never becomes an author decision automatically.</small>
      </aside></div>}

      {drawer && <div className="overlay drawer-overlay" onMouseDown={() => setDrawer(undefined)}><aside className="panel evidence-panel" onMouseDown={(event) => event.stopPropagation()}>
        <button className="panel-close" onClick={() => setDrawer(undefined)}>Close</button>
        <span className="kicker">ANALYSIS / {snapshot.profile?.label}</span><h2>{shortLabel(drawer.row)}</h2>
        {drawer.field === "why" ? <p className="large-copy">{drawer.row.analysis?.why}</p> : <>
          {drawer.field === "analysis" && <p className="large-copy">{drawer.row.analysis?.why}</p>}
          <div className="passage-list">
            {(drawer.row.analysis?.evidence || []).map((evidence) => <article key={evidence.passage_id}><header><strong>{evidence.file}</strong><span>{evidence.heading}</span></header><blockquote>{evidence.excerpt}</blockquote><p>{evidence.reason}</p></article>)}
            {!drawer.row.analysis?.evidence?.length && <p>No manuscript passage was cited.</p>}
          </div>
        </>}
      </aside></div>}

      {actionDialog && <div className="overlay"><section className="modal-card">
        <span className="kicker">{aliasWizard ? `ALIAS MERGE ${aliasWizard.index + 1} OF ${aliasWizard.ids.length}` : "ASSIGN DECISION"}</span>
        <h2>{aliasWizardRow ? shortLabel(aliasWizardRow) : actionDialog.label}</h2>
        <p>{aliasWizardRow ? "Choose the canonical concept that should absorb this duplicate." : actionDialog.help}</p>
        {aliasWizard?.ignored ? <p className="wizard-note">{aliasWizard.ignored} selected row{aliasWizard.ignored === 1 ? " already has" : "s already have"} an alias merge and will be skipped.</p> : null}
        {actionDialog.parameter && <label><span>{actionDialog.parameter.label}</span>
          {searchableParameter
            ? <div className="searchable-select">
              <input autoFocus type="search" value={parameterSearch} placeholder="Type to filter concepts..." onChange={(event) => {
                setParameterSearch(event.target.value);
                setParameterValue("");
              }} onKeyDown={(event) => {
                if (event.key === "Enter" && matchingParameterOptions.length) {
                  event.preventDefault();
                  chooseParameterOption(matchingParameterOptions[0]);
                }
              }} />
              <div className="search-results" role="listbox" aria-label="Matching concepts">
                {matchingParameterOptions.map((option) => {
                  const separator = option.indexOf("|");
                  const value = separator < 0 ? option : option.slice(0, separator);
                  const label = separator < 0 ? option : option.slice(separator + 1);
                  return <button type="button" role="option" aria-selected={parameterValue === value} className={parameterValue === value ? "selected" : ""} key={value} onClick={() => chooseParameterOption(option)}>
                    <span>{label}</span><small>{value.slice(0, 8)}</small>
                  </button>;
                })}
                {!matchingParameterOptions.length && <p>No matching concepts.</p>}
              </div>
              <small className="search-hint">{parameterValue ? "Canonical concept selected" : `${matchingParameterOptions.length}${allMatchingParameterOptions.length > 40 ? "+" : ""} matches shown`}</small>
            </div>
            : actionDialog.parameter.multiline
            ? <textarea autoFocus value={parameterValue} onChange={(event) => setParameterValue(event.target.value)} />
            : <select autoFocus value={parameterValue} onChange={(event) => setParameterValue(event.target.value)}><option value="">Choose...</option>{parameterOptions.map((option) => {
              const [value, label] = option.includes("|") ? option.split("|", 2) : [option, option];
              return <option value={value} key={value}>{label}</option>;
            })}</select>}
        </label>}
        {actionDialog.reason && <label><span>{actionDialog.reason.label}</span>
          <textarea autoFocus={!actionDialog.parameter} value={reasonValue} onChange={(event) => setReasonValue(event.target.value)} placeholder={actionDialog.reason.placeholder} />
        </label>}
        <div className="modal-actions">
          <button onClick={closeActionDialog}>{aliasWizard ? "Finish later" : "Cancel"}</button>
          <button className="button ink" disabled={(!!actionDialog.parameter && !parameterValue.trim()) || (!!actionDialog.reason?.required && !reasonValue.trim())} onClick={() => aliasWizard ? stageAliasStep() : stage(actionDialog, parameterValue, actionDialog.reason ? reasonValue : undefined)}>
            {aliasWizard
              ? aliasWizard.index + 1 === aliasWizard.ids.length ? "Assign & finish" : "Assign & next"
              : `Assign to ${selected.size}`}
          </button>
        </div>
      </section></div>}

      {applyOpen && <div className="overlay"><section className="modal-card apply-card">
        <span className="kicker">FINAL CHECK</span><h2>Apply {selected.size} selected decision{selected.size === 1 ? "" : "s"}?</h2>
        <p>This changes the AuthorLM graph. It does not edit manuscript files, push Google Docs, or run analysis.</p>
        {appliesSequentially && <p className="wizard-note">Alias merges will be applied safely one at a time. If a later merge conflicts, completed merges remain applied and processing stops for review.</p>}
        <div className="decision-summary">{Object.entries(actionCounts).map(([action, count]) => <div key={action}><strong>{count}</strong><span>{action}</span></div>)}</div>
        <div className="modal-actions"><button disabled={!!applying} onClick={() => setApplyOpen(false)}>Cancel</button><button className="button danger" disabled={!!applying} onClick={applySelected}>{applying ? `Applying ${applying.completed} / ${applying.total}` : "Apply selected"}</button></div>
      </section></div>}

      {syncPrompt && <div className="overlay"><section className="modal-card sync-card">
        <span className="kicker">GOOGLE DOCS PREFLIGHT</span><h2>Which manuscript should the analyzer read?</h2>
        <p>{snapshot.sync.message}</p>
        {syncReport && <div className="sync-report"><strong>Reconcile needs attention.</strong><span>{textValue(syncReport.conflicts || syncReport.errors)}</span></div>}
        <div className="modal-actions stacked"><button className="button ink" onClick={reconcileThenAnalyze}>Pull / reconcile first</button><button onClick={() => beginAnalysis(syncPrompt.ids)}>Analyze the local snapshot anyway</button><button className="text-button" onClick={() => setSyncPrompt(undefined)}>Cancel</button></div>
      </section></div>}

      <nav className="scroll-jump" aria-label="Page navigation">
        <button type="button" title="Back to top" onClick={() => window.scrollTo({top: 0})}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 10.5 8 5l5 5.5" /></svg>
          <span>Top</span>
        </button>
        <button type="button" title="Jump to bottom" onClick={() => window.scrollTo({top: document.documentElement.scrollHeight})}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 5.5 5 5.5 5-5.5" /></svg>
          <span>End</span>
        </button>
      </nav>
    </main>
  );
}
