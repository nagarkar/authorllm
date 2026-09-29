export type TriageType = "concepts" | "edges" | "proposals" | "critique";

export interface GroupBy {
  id: string;
  label: string;
}

export interface ColumnSchema {
  id: string;
  label: string;
  kind: string;
  width?: number;
  // Names the staging action an inline edit of this column becomes
  // (e.g. "revise"): the author edits the text in place and the row is
  // staged as a dirty revision, tracked like any other draft decision.
  editable?: string;
}

export interface ActionSchema {
  id: string;
  label: string;
  help: string;
  // No action-bar button: the action is staged some other way (e.g. the
  // critique "revise" lands via the Item column's edit-in-place). Still
  // listed in the Help tab, which explains that path.
  hidden?: boolean;
  reason?: {
    label: string;
    placeholder?: string;
    required?: boolean;
  };
  parameter?: {
    id: string;
    label: string;
    options?: string[];
    source?: string;
    multiline?: boolean;
  };
}

export interface AnalyzerProfile {
  id: string;
  version: string;
  label: string;
  description: string;
  model?: string;
  source: string;
  output_fields: ColumnSchema[];
}

export interface Evidence {
  passage_id: string;
  file: string;
  heading: string;
  excerpt: string;
  reason: string;
}

export interface Analysis {
  score?: number;
  confidence?: number;
  why?: string;
  evidence?: Evidence[];
  state: "current" | "outdated";
  [key: string]: unknown;
}

export interface Draft {
  action: string;
  parameters: Record<string, unknown>;
  reason?: string;
  state: "current" | "conflict";
}

export interface Recommendation {
  triage_type: TriageType;
  object_id: string;
  object_version: number;
  object_label: string;
  action: string;
  parameters: Record<string, unknown>;
  rule: string;
  rule_label: string;
  reason: string;
}

export interface RecommendationResult {
  decisions: Recommendation[];
  protected: Array<{
    triage_type: TriageType;
    object_id: string;
    object_label: string;
    reason: string;
  }>;
  counts: {concepts: number; edges: number; protected: number};
}

export interface TriageRow {
  id: string;
  version: number;
  pending: boolean;
  lifecycle: string;
  name?: string;
  statement?: string;
  from_name?: string;
  relation?: string;
  to_name?: string;
  analysis?: Analysis;
  draft?: Draft;
  [key: string]: unknown;
}

export interface TriageSchema {
  id: TriageType;
  label: string;
  singular: string;
  help: string;
  columns: ColumnSchema[];
  analysis_columns: ColumnSchema[];
  actions: ActionSchema[];
}

export interface Snapshot {
  manuscript: {id: string; name: string};
  manuscript_version: {id: string; version_no: number; checksum: string; local_dirty: boolean} | null;
  schema: TriageSchema;
  profile: AnalyzerProfile | null;
  profiles: AnalyzerProfile[];
  rows: TriageRow[];
  incomplete_runs: Array<{
    id: string;
    profile_id: string;
    profile_version: string;
    requested_ids: string[];
    remaining_ids: string[];
  }>;
  sync: {linked: boolean; master_id?: string; files: string[]; message: string};
}

export interface Transport {
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
}
