export type ImportStatus = "inspected" | "configured" | "reviewed" | "queued" | "processing" | "completed" | "failed";

export interface ImportSummary {
  id: string;
  original_filename: string;
  extension: string;
  byte_size: number;
  content_hash: string;
  status: ImportStatus;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  attempt_count: number;
  sheets: string[];
  accepted_rows: number | null;
  rejected_rows: number | null;
  warning_count: number | null;
  duplicate_candidate_rows: number | null;
  duplicate_warning: boolean;
  related_import_ids: string[];
  transform_version: string;
  locale: string;
}

export interface SheetInspection {
  name: string;
  suggested_header_row: number;
  physical_rows: number | null;
  physical_rows_exact: boolean;
  column_count: number;
  exceeds_row_limit: boolean;
  exceeds_column_limit: boolean;
  formula_count: number;
  formula_missing_cache: number;
  merged_count: number;
}

export interface ImportDetail extends ImportSummary {
  configuration: ImportConfig | null;
  inspection: { sheets: SheetInspection[]; limits: Record<string, number> } | null;
  report: ProcessingReport | null;
  duplicate_acknowledged: boolean;
}

export interface PreviewColumn {
  index: number;
  original_name: string;
  normalized_name: string;
  suggested_type: string;
  mixed: boolean;
  samples: string[];
  additive: boolean;
}

export interface Preview {
  sheet: string;
  suggested_header_row: number;
  header_row: number;
  columns: PreviewColumn[];
  grid: { source_row: number; cells: string[] }[];
  grid_truncated: boolean;
  preview_row_limit: number;
  physical_rows: number | null;
  physical_rows_exact: boolean;
  data_row_count: number | null;
  exceeds_row_limit: boolean;
  exceeds_column_limit: boolean;
  warnings: string[];
  preview_note: string;
  order_details_profile?: boolean;
  formula_count: number;
  formula_missing_cache: number;
  merged_count: number;
}

export interface ColumnConfig {
  index: number;
  original_name: string;
  normalized_name: string;
  type: string;
  date_format: string | null;
  required?: boolean;
}

export interface SheetConfig {
  name: string;
  include: boolean;
  header_row: number;
  columns: ColumnConfig[];
}

export interface ImportConfig {
  locale: "en-US" | "de-DE";
  sheets: SheetConfig[];
  profile?: "generic" | "order_details";
}

export interface SheetStats {
  physical_rows: number;
  header_row: number;
  preamble_rows: number;
  data_rows_inspected: number;
  skipped_empty: number;
  skipped_explicit: number;
  rejected: number;
  accepted: number;
  warning_count: number;
  error_count: number;
  duplicate_candidate_rows: number;
  duplicate_source_rows: number[];
  formula_cells: number;
  formula_missing_cache: number;
  sheet_warnings: string[];
  columns: ColumnConfig[];
}

export interface ProcessingReport {
  transform_version: string;
  locale: string;
  duration_ms: number;
  reconciled: boolean;
  identity: string;
  definitions: Record<string, string>;
  sheets: Record<string, SheetStats>;
  totals: Record<string, number | boolean>;
  issue_groups?: IssueGroup[];
  review_only?: boolean;
}

export interface IssueGroup {
  sheet: string;
  code: string;
  severity: string;
  column: string;
  message: string;
  count: number;
  source_rows: number[];
}

export interface ProfileColumn {
  original_name: string;
  normalized_name: string;
  type: string;
  additive: boolean;
  null_count: number;
  distinct_count: number;
  distinct_values: { value: string; count: number }[];
  min: string | null;
  max: string | null;
}

export interface Profile {
  sheet: string;
  accepted_rows: number;
  columns: ProfileColumn[];
  date_columns: string[];
  profile_scope: string;
  filename: string;
}

export interface TableResponse {
  total: number;
  page: number;
  page_size: number;
  columns: ProfileColumn[];
  rows: { source_row: number; status: string; values: Record<string, string | null>; raw?: Record<string, unknown>; issues: Issue[] }[];
  filters: Record<string, unknown>;
}

export interface Issue {
  severity: string;
  code: string;
  message: string;
  column?: string | null;
  original_column?: string | null;
}

export interface IssuePage {
  total: number;
  page: number;
  page_size: number;
  scope: string;
  issues: {
    sheet: string;
    source_row: number;
    severity: string;
    code: string;
    column: string | null;
    message: string;
    raw_value: string | null;
    processed_value: string | null;
  }[];
}

export interface AggregateResponse {
  metric: string;
  metric_label: string;
  aggregation: string;
  value: string | null;
  unit: string | null;
  additive: boolean;
  row_count: number;
  non_null_count: number;
  filename: string;
  sheet: string;
  filters: Record<string, unknown>;
  group_by: string | null;
  group_by_label: string | null;
  groups: { key: string; value: string | null; rows: number }[];
  groups_truncated: boolean;
  note: string;
}

export interface Health {
  status: string;
  limits: { max_upload_bytes: number; preview_rows: number; max_rows_per_sheet: number };
}

export interface Filters {
  search: string;
  dateColumn: string;
  dateFrom: string;
  dateTo: string;
  columnFilters: { column: string; op: string; value: string }[];
}

export const COLUMN_TYPES = ["text", "identifier", "integer", "decimal", "percentage", "date", "datetime", "boolean"] as const;

export interface UdsFilters {
  importId: string;
  currentOnly: boolean;
  practice: string;
  facility: string;
  orderClass: string;
  billing: string;
  confirmation: string;
  lifecycle: string;
}

export interface UdsOrder {
  id: number;
  import_id: string;
  report_id: string;
  order_name: string | null;
  facility: string | null;
  practice: string | null;
  payer_name: string | null;
  payer_category: string | null;
  provisional_account: string | null;
  provisional_encounter_date: string | null;
  order_class: string;
  lab_from_order: string | null;
  expected_lab: string | null;
  lab_source: string;
  is_current: boolean;
  identity_conflict: boolean;
  selection_reason: string | null;
  analyte_count: number;
  comment_count: number;
  line_count: number;
  classification: Record<string, { value?: string; reason?: string; destination?: string; override_open?: boolean; coverage?: string }>;
  counting_unit: string;
}

export interface UdsOrderList {
  counting_unit: string;
  orders: UdsOrder[];
}

export interface UdsOrderDetail {
  counting_unit: string;
  observations: (UdsOrder & {
    lines: { source_row: number; role: string; lab_attribute: string | null; lab_attribute_value: string | null; icd_code: string | null }[];
    issues: UdsIssue[];
  })[];
  links: UdsLink[];
}

export interface UdsIssue {
  id: number;
  import_id: string;
  report_id: string | null;
  code: string;
  message: string;
  source_rows: number[];
}

export interface UdsQuality {
  import_id: string;
  order_count: number;
  counting_unit: string;
  groups: Record<string, UdsIssue[]>;
}

export interface UdsSummary {
  counting_unit: string;
  orders: number;
  screenings: number;
  confirmations: number;
  unclassified: number;
  positive: number;
  not_established: number;
  cancelled_visible: number;
  billing_excluded_cancelled: number;
  billing_review: number;
  billing_eligible: number;
  follow_up: number;
  compliance_exception: number;
  compliance_insufficient: number;
  identity_conflicts: number;
  conversion: { label: string; numerator: number; denominator: number; rate: number | null; display: number | "N/A" };
}

export interface UdsLink {
  id: number;
  screening_report_id: string;
  confirmation_report_id: string;
  status: string;
  reason: string;
}

export interface UdsResolution {
  id: number;
  report_id: string | null;
  code: string;
  scope: string;
  decision: Record<string, string>;
  reason: string;
  actor: string;
  created_at: string | null;
}

export interface UdsResolutionInput {
  report_id: string | null;
  code: string;
  scope: "order" | "mapping";
  decision: Record<string, string>;
  reason: string;
  actor: string;
}

export interface UdsReview {
  issues: UdsIssue[];
  suggested_links: UdsLink[];
  resolutions: UdsResolution[];
}

export interface UdsMappingVersion {
  id: string;
  version_number: number;
  original_filename: string;
  created_at: string;
  counts: { facility: number; payer: number; order: number };
  conflicts: number;
}
