import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, describeFilters, errorMessage, exportUrl, missingImport } from "../api";
import type { Filters, ImportDetail, Profile, TableResponse } from "../types";
import { forgetImport, rememberImport } from "../workspace";

const EMPTY: Filters = { search: "", dateColumn: "", dateFrom: "", dateTo: "", columnFilters: [] };

export function ExplorerPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [sheet, setSheet] = useState(searchParams.get("sheet") ?? "");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("source_row");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [table, setTable] = useState<TableResponse | null>(null);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [selectedColumn, setSelectedColumn] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.detail(id).then((next) => {
      rememberImport(id, sheet || undefined);
      setDetail(next);
      const names = next.configuration?.sheets.filter((item) => item.include).map((item) => item.name) ?? [];
      setSheet((current) => current || names[0] || "");
    }).catch((caught) => {
      if (missingImport(caught)) {
        forgetImport(id);
        navigate("/", { replace: true });
        return;
      }
      setError(errorMessage(caught));
    });
  }, [id, navigate]);

  useEffect(() => {
    if (!sheet) return;
    api.profile(id, sheet).then((next) => {
      setProfile(next);
      setFilters((current) => ({ ...current, dateColumn: next.date_columns[0] ?? "" }));
    }).catch((caught) => setError(errorMessage(caught)));
  }, [id, sheet]);

  useEffect(() => {
    if (!sheet) return;
    let stopped = false;
    setLoading(true);
    api.rows(id, sheet, filters, page, sort, direction).then((next) => {
      if (!stopped) setTable(next);
    }).catch((caught) => setError(errorMessage(caught))).finally(() => {
      if (!stopped) setLoading(false);
    });
    return () => {
      stopped = true;
    };
  }, [id, sheet, filters, page, sort, direction]);

  const filterLabel = useMemo(() => describeFilters(filters, profile?.columns ?? []), [filters, profile]);
  const record = table?.rows.find((row) => row.source_row === selectedRow) ?? null;
  const column = profile?.columns.find((item) => item.normalized_name === selectedColumn) ?? null;
  const configured = detail?.configuration?.sheets.find((item) => item.name === sheet)?.columns.find((item) => item.normalized_name === selectedColumn);

  function toggleSort(name: string) {
    if (sort === name) setDirection(direction === "asc" ? "desc" : "asc");
    else {
      setSort(name);
      setDirection("asc");
    }
  }

  return (
    <section className="page">
      <p className="eyebrow">Data explorer</p>
      <h1>{detail?.original_filename ?? "Records"}</h1>
      <div className="filters">
        <label className="field">Sheet
          <select value={sheet} onChange={(event) => { setSheet(event.target.value); setPage(1); setSelectedRow(null); }}>
            {(detail?.configuration?.sheets ?? []).filter((item) => item.include).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
        <label className="field">Search
          <input value={filters.search} onChange={(event) => { setPage(1); setFilters((current) => ({ ...current, search: event.target.value })); }} />
        </label>
        {profile?.columns.filter((item) => item.distinct_values.length > 0 && item.distinct_values.length <= 12).map((item) => (
          <label key={item.normalized_name} className="field">{item.original_name}
            <select value={filters.columnFilters.find((filter) => filter.column === item.normalized_name)?.value ?? ""} onChange={(event) => {
              const value = event.target.value;
              setPage(1);
              setFilters((current) => ({
                ...current,
                columnFilters: [...current.columnFilters.filter((filter) => filter.column !== item.normalized_name), ...(value ? [{ column: item.normalized_name, op: "eq", value }] : [])],
              }));
            }}>
              <option value="">Any</option>
              {item.distinct_values.map((value) => <option key={value.value} value={value.value}>{value.value}</option>)}
            </select>
          </label>
        ))}
      </div>
      <div className="row spread">
        <p className="note">{table ? `${table.total} matching records` : "Loading records…"} · {filterLabel} · {sheet || "no sheet"}</p>
        {sheet ? <a className="button" href={exportUrl(id, sheet, filters, "data", "csv")}>Export filtered results</a> : null}
      </div>
      {error ? <p className="banner error">{error}</p> : null}
      <div className="explorer">
        <div className="table-wrap">
          {loading && !table ? <p className="empty">Loading records…</p> : null}
          <table>
            <thead>
              <tr>
                <th><button onClick={() => toggleSort("source_row")}>Source row</button></th>
                {(table?.columns ?? []).map((item) => (
                  <th key={item.normalized_name}><button onClick={() => { toggleSort(item.normalized_name); setSelectedColumn(item.normalized_name); }}>{item.original_name}</button></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table?.rows.map((row) => (
                <tr key={row.source_row} className={row.source_row === selectedRow ? "selected" : ""}>
                  <td><button className="linkish" onClick={() => setSelectedRow(row.source_row)}>Row {row.source_row}</button></td>
                  {table.columns.map((item) => <td key={item.normalized_name}>{row.values[item.normalized_name] ?? ""}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {table && table.total === 0 ? <p className="empty">No matching records.</p> : null}
          {table ? (
            <div className="row">
              <button className="button ghost" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>Previous</button>
              <span>Page {table.page} · {table.total} records</span>
              <button className="button ghost" disabled={page * table.page_size >= table.total} onClick={() => setPage((current) => current + 1)}>Next</button>
            </div>
          ) : null}
        </div>
        <aside className="panel">
          <h2>Inspector</h2>
          {!column && !record ? <p className="empty">Select a column heading or a record.</p> : null}
          {column ? (
            <div>
              <p><strong>{column.original_name}</strong></p>
              <p>Normalized name <code>{column.normalized_name}</code></p>
              <p>Detected type {column.type}. Configured type {configured?.type ?? column.type}.{configured?.required ? " Required." : ""}</p>
              <p>{column.null_count} missing values among {profile?.accepted_rows ?? 0} accepted rows on this sheet.</p>
              <p>{transformation(configured?.type ?? column.type)}</p>
            </div>
          ) : null}
          {record ? (
            <div>
              <h3>Record</h3>
              <p>{sheet}, source row {record.source_row}</p>
              {(table?.columns ?? []).map((item) => (
                <p key={item.normalized_name}>
                  <strong>{item.original_name}</strong><br />
                  Original: {displayRaw(record.raw?.[item.normalized_name])}<br />
                  Processed: {record.values[item.normalized_name] ?? "empty"}
                </p>
              ))}
              {record.issues.length === 0 ? <p className="banner ok">No validation issues on this record.</p> : record.issues.map((issue) => (
                <p key={`${issue.code}-${issue.column}`} className={issue.severity === "error" ? "banner error" : "banner warn"}>{issue.severity}: {issue.message}</p>
              ))}
            </div>
          ) : null}
        </aside>
      </div>
    </section>
  );
}

function displayRaw(value: unknown): string {
  if (value == null || value === "") return "empty";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function transformation(type: string): string {
  if (type === "identifier") return "Identifiers are stored as text, so leading zeros remain part of the value.";
  if (type === "date" || type === "datetime") return "Dates are converted to ISO values. A value that does not parse is rejected and the original text is kept.";
  if (type === "integer" || type === "decimal" || type === "percentage") return "Numbers are parsed with the selected locale. Invalid numbers are rejected rather than replaced.";
  return "Text is trimmed. The original cell is kept beside the processed value.";
}
