import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, describeFilters, errorMessage, exportUrl } from "../api";
import type { AggregateResponse, Filters, ImportDetail, ImportSummary, Profile, TableResponse } from "../types";

const EMPTY_FILTERS: Filters = { search: "", dateColumn: "", dateFrom: "", dateTo: "", columnFilters: [] };

export function DashboardPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [imports, setImports] = useState<ImportSummary[]>([]);
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [sheet, setSheet] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [metric, setMetric] = useState("_count");
  const [aggregation, setAggregation] = useState("count");
  const [groupBy, setGroupBy] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("source_row");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [table, setTable] = useState<TableResponse | null>(null);
  const [summary, setSummary] = useState<AggregateResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.imports().then((body) => setImports(body.imports.filter((item) => item.status === "completed"))).catch((caught) => setError(errorMessage(caught)));
  }, []);

  useEffect(() => {
    setLoading(true);
    setFilters(EMPTY_FILTERS);
    setPage(1);
    api.detail(id).then((next) => {
      if (next.status !== "completed") {
        navigate(`/imports/${id}/setup`);
        return;
      }
      setDetail(next);
      const first = next.configuration?.sheets.find((item) => item.include)?.name ?? next.sheets[0] ?? "";
      setSheet(first);
    }).catch((caught) => setError(errorMessage(caught)));
  }, [id, navigate]);

  useEffect(() => {
    if (!sheet) return;
    api.profile(id, sheet).then((next) => {
      setProfile(next);
      const text = next.columns.find((column) => column.type === "text" || column.type === "boolean");
      setGroupBy(text?.normalized_name ?? "");
      setMetric("_count");
      setAggregation("count");
      setFilters((current) => ({ ...current, dateColumn: next.date_columns[0] ?? "" }));
    }).catch((caught) => setError(errorMessage(caught)));
  }, [id, sheet]);

  useEffect(() => {
    if (!sheet) return;
    let stopped = false;
    setLoading(true);
    Promise.all([
      api.rows(id, sheet, filters, page, sort, direction),
      api.aggregate(id, sheet, filters, metric, aggregation, groupBy),
    ]).then(([rows, aggregate]) => {
      if (stopped) return;
      setTable(rows);
      setSummary(aggregate);
      setError("");
    }).catch((caught) => setError(errorMessage(caught))).finally(() => {
      if (!stopped) setLoading(false);
    });
    return () => {
      stopped = true;
    };
  }, [id, sheet, filters, page, sort, direction, metric, aggregation, groupBy]);

  const filterLabel = useMemo(() => describeFilters(filters, profile?.columns ?? []), [filters, profile]);
  const stats = detail?.report?.sheets[sheet];
  const chartData = (summary?.groups ?? []).map((group) => ({ name: group.key, value: group.value === null ? 0 : Number(group.value) }));
  const groupType = profile?.columns.find((column) => column.normalized_name === groupBy)?.type;
  const mismatch = table && summary && table.total !== summary.row_count;

  function setColumnFilter(column: string, op: string, value: string) {
    setPage(1);
    setFilters((current) => ({
      ...current,
      columnFilters: [...current.columnFilters.filter((item) => item.column !== column || item.op !== op), ...(value ? [{ column, op, value }] : [])],
    }));
  }

  return (
    <section className="page">
      <p className="eyebrow">Dashboard</p>
      <div className="row spread">
        <h1>{detail?.original_filename ?? "Dataset"}</h1>
        <label className="field inline">
          Import
          <select value={id} onChange={(event) => navigate(`/imports/${event.target.value}`)}>
            {imports.map((item) => (
              <option key={item.id} value={item.id}>{item.original_filename}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="tabs">
        {(detail?.configuration?.sheets ?? []).filter((item) => item.include).map((item) => (
          <button key={item.name} className={item.name === sheet ? "active" : ""} onClick={() => { setSheet(item.name); setPage(1); setFilters(EMPTY_FILTERS); }}>{item.name}</button>
        ))}
      </div>
      {error ? <p className="banner error">{error}</p> : null}
      {!detail ? <p>Loading import…</p> : null}
      {detail && detail.status !== "completed" ? <p className="banner">This import is {detail.status}. Dashboard data appears only after processing completes.</p> : null}
      {stats ? (
        <div className="quality">
          <article>
            <span>Inspected</span>
            <strong>{stats.data_rows_inspected}</strong>
            <small>after header row {stats.header_row}</small>
          </article>
          <article>
            <span>Accepted</span>
            <strong>{stats.accepted}</strong>
            <small>used in this dashboard</small>
          </article>
          <article>
            <span>Rejected</span>
            <strong>{stats.rejected}</strong>
            <small>{stats.error_count} cell errors</small>
          </article>
          <article>
            <span>Skipped</span>
            <strong>{stats.skipped_empty + stats.skipped_explicit}</strong>
            <small>{stats.skipped_empty} empty · {stats.skipped_explicit} totals</small>
          </article>
          <article>
            <span>Duplicates kept</span>
            <strong>{stats.duplicate_candidate_rows}</strong>
            <small>{stats.formula_missing_cache} uncached formulas</small>
          </article>
        </div>
      ) : null}
      {stats?.sheet_warnings.map((warning) => <p key={warning} className="banner warn">{warning}</p>)}
      <p className="note">Import quality above describes the whole sheet. The summary, chart, table, and exports below all use the same filters: {filterLabel}.</p>
      <div className="filters">
        <label className="field">Search
          <input value={filters.search} onChange={(event) => { setPage(1); setFilters((current) => ({ ...current, search: event.target.value })); }} />
        </label>
        {profile?.date_columns.length ? (
          <>
            <label className="field">Date column
              <select value={filters.dateColumn} onChange={(event) => setFilters((current) => ({ ...current, dateColumn: event.target.value }))}>
                {profile.date_columns.map((column) => <option key={column} value={column}>{profile.columns.find((item) => item.normalized_name === column)?.original_name}</option>)}
              </select>
            </label>
            <label className="field">From
              <input type="date" value={filters.dateFrom} onChange={(event) => { setPage(1); setFilters((current) => ({ ...current, dateFrom: event.target.value })); }} />
            </label>
            <label className="field">To
              <input type="date" value={filters.dateTo} onChange={(event) => { setPage(1); setFilters((current) => ({ ...current, dateTo: event.target.value })); }} />
            </label>
          </>
        ) : null}
        {profile?.columns.filter((column) => column.distinct_values.length > 0).map((column) => (
          <label key={column.normalized_name} className="field">{column.original_name}
            <select value={filters.columnFilters.find((item) => item.column === column.normalized_name)?.value ?? ""} onChange={(event) => setColumnFilter(column.normalized_name, "eq", event.target.value)}>
              <option value="">Any</option>
              {column.distinct_values.map((item) => <option key={item.value} value={item.value}>{item.value}</option>)}
            </select>
          </label>
        ))}
      </div>
      <div className="builder">
        <label className="field">Metric
          <select value={metric} onChange={(event) => {
            const next = event.target.value;
            setMetric(next);
            setAggregation(next === "_count" ? "count" : "avg");
          }}>
            <option value="_count">Row count</option>
            {profile?.columns.filter((column) => ["integer", "decimal", "percentage"].includes(column.type)).map((column) => (
              <option key={column.normalized_name} value={column.normalized_name}>{column.original_name}</option>
            ))}
          </select>
        </label>
        <label className="field">Aggregation
          <select value={aggregation} onChange={(event) => setAggregation(event.target.value)}>
            <option value="count">Count</option>
            {metric !== "_count" && profile?.columns.find((column) => column.normalized_name === metric)?.additive ? <option value="sum">Sum</option> : null}
            {metric !== "_count" ? <option value="avg">Average</option> : null}
            {metric !== "_count" ? <option value="min">Minimum</option> : null}
            {metric !== "_count" ? <option value="max">Maximum</option> : null}
          </select>
        </label>
        <label className="field">Group chart by
          <select value={groupBy} onChange={(event) => setGroupBy(event.target.value)}>
            <option value="">No chart grouping</option>
            {profile?.columns.map((column) => <option key={column.normalized_name} value={column.normalized_name}>{column.original_name}</option>)}
          </select>
        </label>
      </div>
      {mismatch ? <p className="banner error">The table and summary returned different row counts. Reload before trusting either number.</p> : null}
      <div className="summary-card">
        {loading && !summary ? <p>Loading summary…</p> : null}
        {summary ? (
          <>
            <span>{summary.aggregation} of {summary.metric_label}</span>
            <strong>{summary.value ?? "—"}{summary.unit === "fraction" ? " (fraction)" : ""}</strong>
            <small>
              {summary.filename} · {summary.sheet} · {summary.row_count} filtered rows
              {summary.metric !== "_count" ? ` · ${summary.non_null_count} non-null values` : ""}
              {summary.group_by_label ? ` · grouped by ${summary.group_by_label}` : ""}
            </small>
            <small>{filterLabel}</small>
          </>
        ) : null}
      </div>
      <div className="chart-card">
        {!groupBy ? <p className="empty">Choose a column to group the chart. The number above still uses every filtered row.</p> : null}
        {groupBy && chartData.length === 0 ? <p className="empty">No rows match these filters.</p> : null}
        {groupBy && chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            {groupType === "date" || groupType === "datetime" ? (
              <LineChart data={chartData}>
                <CartesianGrid stroke="#e4dccf" vertical={false} />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Line type="monotone" dataKey="value" stroke="#0f6a4f" strokeWidth={2} dot={false} />
              </LineChart>
            ) : (
              <BarChart data={chartData}>
                <CartesianGrid stroke="#e4dccf" vertical={false} />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="value" fill="#0f6a4f" />
              </BarChart>
            )}
          </ResponsiveContainer>
        ) : null}
        {summary?.groups_truncated ? <p className="note">{summary.note}</p> : null}
      </div>
      <div className="row">
        <a className="button" href={exportUrl(id, sheet, filters, "data", "csv")}>Export filtered data (CSV)</a>
        <a className="button ghost" href={exportUrl(id, sheet, filters, "data", "xlsx")}>Export filtered data (Excel)</a>
        <a className="button ghost" href={exportUrl(id, sheet, filters, "issues", "csv")}>Export issues (CSV)</a>
        <Link to={`/imports/${id}/setup`}>Processing setup</Link>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th><button onClick={() => toggleSort("source_row", sort, direction, setSort, setDirection)}>Source row</button></th>
              {(table?.columns ?? profile?.columns ?? []).map((column) => (
                <th key={column.normalized_name}>
                  <button onClick={() => toggleSort(column.normalized_name, sort, direction, setSort, setDirection)}>{column.original_name}</button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table?.rows.map((row) => (
              <tr key={row.source_row}>
                <td>{row.source_row}</td>
                {(table.columns).map((column) => (
                  <td key={column.normalized_name}>
                    {row.values[column.normalized_name] ?? ""}
                    {row.issues.filter((issue) => issue.column === column.normalized_name).length ? <small>{row.issues.filter((issue) => issue.column === column.normalized_name).map((issue) => issue.code).join(", ")}</small> : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {table && table.total === 0 ? <p className="empty">No accepted rows match these filters.</p> : null}
      </div>
      {table ? (
        <div className="row">
          <button disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>Previous</button>
          <span>Page {table.page} · {table.total} rows</span>
          <button disabled={page * table.page_size >= table.total} onClick={() => setPage((current) => current + 1)}>Next</button>
        </div>
      ) : null}
    </section>
  );
}

function toggleSort(column: string, sort: string, direction: "asc" | "desc", setSort: (value: string) => void, setDirection: (value: "asc" | "desc") => void) {
  if (sort === column) setDirection(direction === "asc" ? "desc" : "asc");
  else {
    setSort(column);
    setDirection("asc");
  }
}
