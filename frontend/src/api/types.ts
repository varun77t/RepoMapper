/**
 * Mirrors the backend response contract.
 *
 * The backend deliberately returns a flat, library-neutral {nodes, edges}
 * shape rather than Cytoscape's element format, so these types describe the
 * API -- the mapping into Cytoscape lives in lib/elements.ts and nowhere else.
 */

export type AnalysisStatus =
  | "queued"
  | "cloning"
  | "parsing"
  | "building_graph"
  | "computing_insights"
  | "complete"
  | "failed";

export interface AnalyzeResponse {
  analysis_id: string;
  status: AnalysisStatus;
  cached: boolean;
}

export interface CallResolutionStats {
  total_calls: number;
  local_count: number;
  import_count: number;
  heuristic_count: number;
  builtin_count: number;
  unresolved_count: number;
  in_repo_calls: number;
  resolved_count: number;
  resolved_pct: number;
  high_confidence_pct: number;
}

export interface AnalysisState {
  analysis_id: string;
  repo_url: string;
  repo_name: string;
  commit_sha: string | null;
  default_branch: string | null;
  status: AnalysisStatus;
  progress_message: string | null;
  error: string | null;
  file_count: number;
  symbol_count: number;
  call_resolution_stats: CallResolutionStats | null;
  skipped_files: Record<string, unknown> | null;
  created_at: string;
  completed_at: string | null;
}

export type GraphLevel = "file" | "symbol";

/** A file node carries the attributes the graph is styled by. */
export interface FileNode {
  id: string;
  label: string;
  type: "file";
  path: string;
  language: string | null;
  line_count: number | null;
  symbol_count: number | null;
  pagerank: number | null;
  community: number | null;
  in_degree: number;
  out_degree: number;
}

export interface SymbolNode {
  id: string;
  label: string;
  type: "function" | "method" | "class";
  path: string;
  name: string;
  start_line: number | null;
  end_line: number | null;
  is_exported: boolean | null;
  parent_node_id: string | null;
}

export type GraphNode = FileNode | SymbolNode;

export type Resolution = "local" | "import" | "heuristic" | "builtin" | "unresolved";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  /** `imports` means source imports target -- the direction is a contract. */
  type: "imports" | "calls" | "inherits_from" | "defines";
  weight: number;
  resolution?: Resolution;
  confidence?: number;
}

export interface GraphResponse {
  level: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface CentralFile {
  rank: number;
  node_id: string;
  path: string;
  pagerank: number;
  in_degree: number;
  out_degree: number;
}

export interface Community {
  id: number;
  label: string;
  size: number;
  node_ids: string[];
  paths: string[];
  central_paths: string[];
  common_directory: string;
}

export interface Cycle {
  paths: string[];
  length: number;
}

export interface CycleReport {
  cycles: Cycle[];
  scc_count: number;
  files_in_cycles: number;
  self_loops: string[];
  truncated: boolean;
  has_cycles: boolean;
}

export interface ReadingStep {
  step: number;
  node_id: string;
  path: string;
  reason: string;
  depth: number;
  in_cycle: boolean;
}

export interface OrphanFile {
  node_id: string;
  path: string;
  reason: string;
}

export interface OrphanSymbol {
  node_id: string;
  path: string;
  label: string;
  kind: string;
  start_line: number | null;
  confidence: "high" | "low";
  reason: string;
}

export interface OrphanReport {
  files: OrphanFile[];
  symbols: OrphanSymbol[];
  counts: {
    files: number;
    symbols: number;
    symbols_high_confidence: number;
    symbols_low_confidence: number;
  };
  note: string;
}

export interface Insights {
  central_files: CentralFile[];
  communities: Community[];
  cycles: CycleReport;
  reading_order: ReadingStep[];
  orphans: OrphanReport;
  entry_points: { node_id: string; path: string }[];
  summary: {
    file_count: number;
    import_edge_count: number;
    community_count: number;
    cycle_count: number;
    orphan_file_count: number;
    orphan_symbol_count: number;
  };
}

export interface SourceFileSummary {
  id: string;
  path: string;
  language: string;
  line_count: number;
  size_bytes: number;
  is_entry_point: boolean;
  parse_error: string | null;
  summary: string | null;
}

export interface SymbolDetail {
  id: string;
  kind: string;
  name: string;
  qualified_name: string;
  start_line: number;
  end_line: number;
  params: string[] | null;
  base_classes: string[] | null;
  decorators: string[] | null;
  is_exported: boolean;
  parent_symbol_id: string | null;
}

export interface ImportDetail {
  raw_specifier: string;
  imported_names: string[] | null;
  line: number;
  is_external: boolean;
  external_module: string | null;
  resolved_path: string | null;
}

export interface FileDetail {
  file: SourceFileSummary;
  symbols: SymbolDetail[];
  imports: ImportDetail[];
  imported_by: string[];
}
