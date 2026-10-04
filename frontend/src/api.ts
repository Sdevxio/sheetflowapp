import type { AggregateResponse, Filters, Health, ImportConfig, ImportDetail, ImportSummary, IssuePage, Preview, Profile, TableResponse, UdsFilters, UdsLink, UdsMappingVersion, UdsOrderDetail, UdsOrderList, UdsQuality, UdsResolution, UdsResolutionInput, UdsReview, UdsSummary } from "./types";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "Request failed");
    this.status = status;
    this.detail = detail;
  }
}

export function missingImport(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
    if (Array.isArray(detail)) {
      return detail.map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : JSON.stringify(item))).join(" ");
    }
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body;
    } catch {
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function filtersToQuery(filters: Filters, extra?: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
  }
  if (filters.search) params.set("search", filters.search);
  if (filters.dateColumn && (filters.dateFrom || filters.dateTo)) {
    params.set("date_column", filters.dateColumn);
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
  }
  for (const filter of filters.columnFilters) {
    if (filter.column && filter.op && filter.value !== "") params.append("filter", `${filter.column}:${filter.op}:${filter.value}`);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function describeFilters(filters: Filters, columns: { normalized_name: string; original_name: string }[]): string {
  const labels = new Map(columns.map((column) => [column.normalized_name, column.original_name]));
  const parts: string[] = [];
  if (filters.search) parts.push(`search “${filters.search}”`);
  if (filters.dateColumn && (filters.dateFrom || filters.dateTo)) {
    parts.push(`${labels.get(filters.dateColumn) ?? filters.dateColumn} ${filters.dateFrom || "…"} to ${filters.dateTo || "…"}`);
  }
  for (const filter of filters.columnFilters) {
    if (!filter.value) continue;
    parts.push(`${labels.get(filter.column) ?? filter.column} ${filter.op} ${filter.value}`);
  }
  return parts.length ? parts.join("; ") : "No filters";
}

export const api = {
  health: () => request<Health>("/api/health"),
  imports: () => request<{ imports: ImportSummary[] }>("/api/imports"),
  detail: (id: string) => request<ImportDetail>(`/api/imports/${id}`),
  preview: (id: string, sheet: string, headerRow?: number) => {
    const params = new URLSearchParams({ sheet });
    if (headerRow) params.set("header_row", String(headerRow));
    return request<Preview>(`/api/imports/${id}/preview?${params.toString()}`);
  },
  saveConfig: (id: string, config: ImportConfig) =>
    request<ImportDetail>(`/api/imports/${id}/configuration`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }),
  review: (id: string) => request<ImportDetail>(`/api/imports/${id}/review`, { method: "POST" }),
  process: (id: string) => request<ImportDetail>(`/api/imports/${id}/process`, { method: "POST" }),
  issues: (id: string, query: string) => request<IssuePage>(`/api/imports/${id}/issues${query}`),
  remove: (id: string) => request<void>(`/api/imports/${id}`, { method: "DELETE" }),
  restore: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return request<{ imports: number }>("/api/backup/restore", { method: "POST", body });
  },
  rows: (id: string, sheet: string, filters: Filters, page: number, sort: string, direction: string) =>
    request<TableResponse>(`/api/imports/${id}/rows${filtersToQuery(filters, { sheet, page, page_size: 25, sort, direction })}`),
  aggregate: (id: string, sheet: string, filters: Filters, metric: string, aggregation: string, groupBy: string) =>
    request<AggregateResponse>(
      `/api/imports/${id}/aggregate${filtersToQuery(filters, { sheet, metric, aggregation, group_by: groupBy || undefined })}`,
    ),
  profile: (id: string, sheet: string) => request<Profile>(`/api/imports/${id}/profile?sheet=${encodeURIComponent(sheet)}`),
  udsMappings: () => request<{ versions: UdsMappingVersion[] }>("/api/uds/mappings"),
  uploadMapping: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return request<{ version_number: number }>("/api/uds/mappings", { method: "POST", body });
  },
  udsOrders: (query: string) => request<UdsOrderList>(`/api/uds/orders${query}`),
  udsOrder: (reportId: string, importId?: string) =>
    request<UdsOrderDetail>(`/api/uds/orders/${encodeURIComponent(reportId)}${importId ? `?import_id=${importId}` : ""}`),
  udsQuality: (importId: string) => request<UdsQuality>(`/api/uds/quality?import_id=${importId}`),
  udsSummary: (query: string) => request<UdsSummary>(`/api/uds/summary${query}`),
  udsReview: () => request<UdsReview>("/api/uds/review"),
  resolveIssue: (body: UdsResolutionInput) =>
    request<UdsResolution>("/api/uds/resolutions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  decideLink: (id: number, body: { status: "accepted" | "rejected"; reason: string; actor: string }) =>
    request<UdsLink>(`/api/uds/links/${id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  upload: async (file: File, acknowledgeDuplicate: boolean) => {
    const body = new FormData();
    body.set("file", file);
    body.set("acknowledge_duplicate", acknowledgeDuplicate ? "true" : "false");
    return request<ImportDetail>("/api/imports", { method: "POST", body });
  },
};

export function udsQuery(filters: UdsFilters): string {
  const params = new URLSearchParams();
  if (filters.importId) params.set("import_id", filters.importId);
  params.set("current_only", filters.currentOnly ? "true" : "false");
  if (filters.practice) params.set("practice", filters.practice);
  if (filters.facility) params.set("facility", filters.facility);
  if (filters.orderClass) params.set("order_class", filters.orderClass);
  if (filters.billing) params.set("billing", filters.billing);
  if (filters.confirmation) params.set("confirmation", filters.confirmation);
  if (filters.lifecycle) params.set("lifecycle", filters.lifecycle);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function exportUrl(id: string, sheet: string, filters: Filters, kind: "data" | "issues", fmt: "csv" | "xlsx"): string {
  return `/api/imports/${id}/export${filtersToQuery(filters, { sheet, kind, fmt })}`;
}
