import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errorMessage, missingImport } from "../api";
import type { AggregateResponse, ImportDetail, ImportSummary } from "../types";
import { forgetImport, rememberImport, rememberedImport, rememberedSheet } from "../workspace";

const EMPTY_FILTERS = { search: "", dateColumn: "", dateFrom: "", dateTo: "", columnFilters: [] };

export function OverviewPage() {
  const params = useParams();
  const navigate = useNavigate();
  const [imports, setImports] = useState<ImportSummary[]>([]);
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [sheet, setSheet] = useState("");
  const [summary, setSummary] = useState<AggregateResponse | null>(null);
  const [recent, setRecent] = useState<{ source_row: number; values: Record<string, string | null> }[]>([]);
  const [columns, setColumns] = useState<{ original_name: string; normalized_name: string }[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.imports().then((body) => {
      setImports(body.imports);
      if (!params.id) {
        const remembered = rememberedImport();
        const next = body.imports.find((item) => item.id === remembered)?.id
          ?? body.imports.find((item) => item.status === "completed")?.id;
        if (next) navigate(`/imports/${next}`, { replace: true });
        else if (remembered) forgetImport(remembered);
      }
    }).catch((caught) => setError(errorMessage(caught)));
  }, [navigate, params.id]);

  useEffect(() => {
    const id = params.id;
    if (!id) return;
    api.detail(id).then((next) => {
      rememberImport(id);
      setDetail(next);
      setError("");
      if (next.status === "queued" || next.status === "processing") return;
      const names = next.configuration?.sheets.filter((item) => item.include).map((item) => item.name) ?? next.sheets;
      const preferred = names.includes(rememberedSheet()) ? rememberedSheet() : names[0] ?? "";
      setSheet(preferred);
    }).catch((caught) => {
      if (missingImport(caught)) {
        forgetImport(id);
        navigate("/", { replace: true });
        return;
      }
      setError(errorMessage(caught));
    });
  }, [navigate, params.id]);

  useEffect(() => {
    if (!params.id || !sheet || detail?.status !== "completed") return;
    rememberImport(params.id, sheet);
    const textColumn = detail.configuration?.sheets.find((item) => item.name === sheet)?.columns.find((column) => column.normalized_name === "status")
      ?? detail.configuration?.sheets.find((item) => item.name === sheet)?.columns.find((column) => column.type === "text" || column.type === "boolean");
    const groupBy = textColumn?.normalized_name ?? "";
    Promise.all([
      api.aggregate(params.id, sheet, EMPTY_FILTERS, "_count", "count", groupBy),
      api.rows(params.id, sheet, EMPTY_FILTERS, 1, "source_row", "asc"),
    ]).then(([aggregate, table]) => {
      setSummary(groupBy ? aggregate : null);
      setRecent(table.rows.slice(0, 8));
      setColumns(table.columns);
    }).catch((caught) => setError(errorMessage(caught)));
  }, [params.id, sheet, detail]);

  useEffect(() => {
    if (!params.id || (detail?.status !== "queued" && detail?.status !== "processing")) return;
    const timer = window.setInterval(() => {
      api.detail(params.id!).then(setDetail).catch((caught) => setError(errorMessage(caught)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [detail?.status, params.id]);

  if (!params.id && imports.length === 0) {
    return (
      <section className="page">
        <p className="eyebrow">Overview</p>
        <h1>Workbook overview.</h1>
        <p className="lede">No imports yet. Upload a workbook to inspect sheets, review validation, and open the results.</p>
        <Link className="button" to="/upload">Upload workbook</Link>
      </section>
    );
  }

  const stats = detail?.report?.sheets[sheet];
  const totals = detail?.report?.totals;
  const groupLabel = summary?.group_by_label || "status";
  const chart = (summary?.groups ?? []).map((group) => ({ name: group.key, value: group.rows }));
  const sheetGroups = (detail?.report?.issue_groups ?? []).filter((group) => group.sheet === sheet && group.severity === "error");

  return (
    <section className="page">
      <p className="eyebrow">Overview</p>
      <div className="row spread">
        <h1>Workbook overview.</h1>
        <Link className="button" to="/upload">Upload workbook</Link>
      </div>
      {error ? <p className="banner error">{error}</p> : null}
      <div className="filters">
        <label className="field">Import
          <select value={params.id ?? ""} onChange={(event) => navigate(`/imports/${event.target.value}`)}>
            {imports.map((item) => <option key={item.id} value={item.id}>{item.original_filename}</option>)}
          </select>
        </label>
        <label className="field">Sheet
          <select value={sheet} onChange={(event) => setSheet(event.target.value)}>
            {(detail?.configuration?.sheets ?? []).filter((item) => item.include).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
      </div>
      <p className="muted">{detail?.original_filename}{sheet ? ` · ${sheet}` : ""}</p>
      {detail ? <p><span className={`pill ${detail.status}`}>{detail.status}</span> {detail.finished_at ? <span className="muted">Completed {new Date(detail.finished_at).toLocaleString()}</span> : null}</p> : <p>Loading import…</p>}
      {detail?.status === "failed" ? <p className="banner error">{detail.error_message} <button className="button" onClick={() => void api.process(detail.id).then(() => api.detail(detail.id).then(setDetail))}>Retry</button></p> : null}
      {(detail?.status === "queued" || detail?.status === "processing") && <p className="banner">Processing is still running. This page updates when the job finishes.</p>}
      {detail?.status === "reviewed" ? <p className="banner">This workbook has been reviewed and is not confirmed yet. <Link to={`/imports/${detail.id}/review`}>Return to review</Link></p> : null}
      {stats && totals ? (
        <div className="cards">
          <article className="metric">
            <span>Accepted rows</span>
            <strong>{stats.accepted}</strong>
            <small>Selected sheet. Workbook total {String(totals.accepted)}.</small>
          </article>
          <article className="metric">
            <span>Rejected rows</span>
            <strong>{stats.rejected}</strong>
            <small>Selected sheet. Workbook total {String(totals.rejected)}.</small>
          </article>
          <article className="metric">
            <span>Sheets imported</span>
            <strong>{Object.keys(detail.report?.sheets ?? {}).length}</strong>
            <small>Whole workbook.</small>
          </article>
        </div>
      ) : null}
      {detail?.configuration?.profile === "order_details" && detail.status === "completed" ? (
        <p><Link to={`/imports/${detail.id}/orders`}>Open order reconciliation</Link></p>
      ) : null}
      {detail?.status === "completed" ? (
        <div className="split-panels">
          <section className="panel">
            <h2>{sheet} by {groupLabel}</h2>
            <p className="note">Accepted rows on the selected sheet. Rejected and skipped rows are excluded.</p>
            {chart.length === 0 ? <p className="empty">This sheet has no accepted rows to chart.</p> : (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={chart} layout="vertical" margin={{ left: 16 }}>
                  <CartesianGrid stroke="#e6edf5" horizontal={false} />
                  <XAxis type="number" />
                  <YAxis type="category" dataKey="name" width={110} />
                  <Tooltip />
                  <Bar dataKey="value" fill="#1f6feb" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </section>
          <section className="panel">
            <h2>Data quality</h2>
            <p className="note">Rejected records stay in the issue report and are excluded from dashboard calculations.</p>
            {sheetGroups.length === 0 ? <p className="banner ok">No rejected rows on this sheet.</p> : null}
            <ul>
              {sheetGroups.map((group) => (
                <li key={`${group.code}-${group.column}`}>
                  <Link to={`/imports/${detail.id}/validation?sheet=${encodeURIComponent(sheet)}&code=${encodeURIComponent(group.code)}`}>{group.code}</Link>
                  {" "}· {group.count} rows{group.column ? ` · ${group.column}` : ""}
                  <small className="muted"> {group.message}</small>
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}
      {detail?.status === "completed" ? (
        <section>
          <div className="row spread">
            <h2>Recent records</h2>
            <Link className="button ghost" to={`/imports/${detail.id}/explore?sheet=${encodeURIComponent(sheet)}`}>Open data explorer</Link>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Source row</th>
                  {columns.map((column) => <th key={column.normalized_name}>{column.original_name}</th>)}
                </tr>
              </thead>
              <tbody>
                {recent.map((row) => (
                  <tr key={row.source_row}>
                    <td>{row.source_row}</td>
                    {columns.map((column) => <td key={column.normalized_name}>{row.values[column.normalized_name] ?? ""}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
            {recent.length === 0 ? <p className="empty">No accepted records on this sheet.</p> : null}
          </div>
        </section>
      ) : null}
    </section>
  );
}
