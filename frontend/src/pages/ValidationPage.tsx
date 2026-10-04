import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, errorMessage, exportUrl, missingImport } from "../api";
import type { ImportDetail, IssuePage } from "../types";
import { forgetImport, rememberImport } from "../workspace";

export function ValidationPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [page, setPage] = useState<IssuePage | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState("");
  const sheet = params.get("sheet") ?? "";
  const severity = params.get("severity") ?? "";
  const code = params.get("code") ?? "";

  useEffect(() => {
    api.detail(id).then((next) => {
      rememberImport(id, sheet || undefined);
      setDetail(next);
    }).catch((caught) => {
      if (missingImport(caught)) {
        forgetImport(id);
        navigate("/", { replace: true });
        return;
      }
      setError(errorMessage(caught));
    });
  }, [id, sheet, navigate]);

  useEffect(() => {
    if (!detail) return;
    const query = new URLSearchParams({ page: String(pageNumber), page_size: "25" });
    if (sheet) query.set("sheet", sheet);
    if (severity) query.set("severity", severity);
    if (code) query.set("code", code);
    api.issues(id, `?${query.toString()}`).then(setPage).catch((caught) => setError(errorMessage(caught)));
  }, [detail, id, sheet, severity, code, pageNumber]);

  function update(key: string, value: string) {
    setPageNumber(1);
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  const sheets = detail?.configuration?.sheets.filter((item) => item.include) ?? [];
  const exportSheet = sheet || sheets[0]?.name || "";

  return (
    <section className="page">
      <p className="eyebrow">Validation</p>
      <div className="row spread">
        <h1>Validation</h1>
        {exportSheet ? <a className="button" href={exportUrl(id, exportSheet, { search: "", dateColumn: "", dateFrom: "", dateTo: "", columnFilters: [] }, "issues", "csv")}>Export issue report</a> : null}
      </div>
      <p className="lede">{detail?.original_filename}. Scope: {page?.scope ?? "this import"}. {page ? `${page.total} issues` : ""}</p>
      {error ? <p className="banner error">{error}</p> : null}
      <div className="filters">
        <label className="field">Sheet
          <select value={sheet} onChange={(event) => update("sheet", event.target.value)}>
            <option value="">All sheets</option>
            {sheets.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
        <label className="field">Severity
          <select value={severity} onChange={(event) => update("severity", event.target.value)}>
            <option value="">Any</option>
            <option value="error">Error</option>
            <option value="warning">Warning</option>
          </select>
        </label>
        <label className="field">Issue type
          <input value={code} onChange={(event) => update("code", event.target.value)} placeholder="INVALID_DATE" />
        </label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Sheet</th>
              <th>Source row</th>
              <th>Severity</th>
              <th>Type</th>
              <th>Column</th>
              <th>Original value</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {page?.issues.map((issue, index) => (
              <tr key={`${issue.sheet}-${issue.source_row}-${issue.code}-${index}`}>
                <td>{issue.sheet}</td>
                <td>{issue.source_row}</td>
                <td><span className={`pill ${issue.severity}`}>{issue.severity}</span></td>
                <td>{issue.code}</td>
                <td>{issue.column}</td>
                <td>{issue.raw_value ?? ""}</td>
                <td>{issue.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {page && page.total === 0 ? <p className="empty">No issues match these filters.</p> : null}
      </div>
      {page ? (
        <div className="row">
          <button className="button ghost" disabled={pageNumber <= 1} onClick={() => setPageNumber((current) => current - 1)}>Previous</button>
          <span>Page {page.page}</span>
          <button className="button ghost" disabled={pageNumber * page.page_size >= page.total} onClick={() => setPageNumber((current) => current + 1)}>Next</button>
        </div>
      ) : null}
    </section>
  );
}
