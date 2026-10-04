import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api";
import type { ImportSummary } from "../types";
import { forgetImport } from "../workspace";

export function HistoryPage() {
  const [imports, setImports] = useState<ImportSummary[]>([]);
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<ImportSummary | null>(null);
  const [pendingRestore, setPendingRestore] = useState<File | null>(null);
  const restoreInput = useRef<HTMLInputElement>(null);

  function reload() {
    api.imports().then((body) => setImports(body.imports)).catch((caught) => setError(errorMessage(caught)));
  }

  useEffect(() => {
    reload();
  }, []);

  async function retry(id: string) {
    setError("");
    try {
      await api.process(id);
      reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function confirmRestore() {
    if (!pendingRestore) return;
    setError("");
    try {
      await api.restore(pendingRestore);
      setPendingRestore(null);
      if (restoreInput.current) restoreInput.current.value = "";
      reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    try {
      await api.remove(pendingDelete.id);
      forgetImport(pendingDelete.id);
      setPendingDelete(null);
      reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <section className="page">
      <p className="eyebrow">Imports</p>
      <h1>Imports</h1>
      <p className="lede">Reopen a completed dataset without uploading the file again. Failed jobs can be retried. A retry replaces that import’s rows instead of adding a second copy.</p>
      <div className="row">
        <a className="button" href="/api/backup">Download backup</a>
        <button className="button ghost" onClick={() => restoreInput.current?.click()}>Restore backup</button>
        <input
          ref={restoreInput}
          type="file"
          accept=".zip,application/zip"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) setPendingRestore(file);
          }}
        />
      </div>
      {error ? <p className="banner error">{error}</p> : null}
      {imports.length === 0 ? <p className="empty">No imports yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Uploaded</th>
                <th>Status</th>
                <th>Sheets</th>
                <th>Rows</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {imports.map((item) => (
                <tr key={item.id}>
                  <td>
                    <strong>{item.original_filename}</strong>
                    <small>{item.id}</small>
                    {item.duplicate_warning ? <small>Same file content as an earlier import.</small> : null}
                  </td>
                  <td>{new Date(item.created_at).toLocaleString()}</td>
                  <td>
                    <span className={`pill ${item.status}`}>{item.status}</span>
                    {item.duration_ms !== null ? <small>{(item.duration_ms / 1000).toFixed(1)}s</small> : null}
                    {item.error_message ? <small>{item.error_message}</small> : null}
                  </td>
                  <td>{item.sheets.join(", ") || "—"}</td>
                  <td>{item.accepted_rows ?? "—"} accepted{item.warning_count ? ` · ${item.warning_count} warnings` : ""}</td>
                  <td className="actions">
                    <Link to={item.status === "completed" ? `/imports/${item.id}` : item.status === "reviewed" ? `/imports/${item.id}/review` : `/imports/${item.id}/setup`}>
                      {item.status === "completed" ? "Open results" : item.status === "failed" ? "Review failure" : "Open"}
                    </Link>
                    {item.status === "completed" ? <Link to={`/imports/${item.id}/validation`}>Validation</Link> : null}
                    {item.status === "failed" ? <button onClick={() => void retry(item.id)}>Retry</button> : null}
                    {item.status !== "queued" && item.status !== "processing" ? <button onClick={() => setPendingDelete(item)}>Delete</button> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pendingRestore ? (
        <div className="modal">
          <div>
            <h2>Restore this backup?</h2>
            <p>{pendingRestore.name} replaces the saved imports and original workbooks on this computer. SheetFlow refuses the restore while an import is still processing.</p>
            <div className="row">
              <button className="button danger" onClick={() => void confirmRestore()}>Restore backup</button>
              <button className="button ghost" onClick={() => { setPendingRestore(null); if (restoreInput.current) restoreInput.current.value = ""; }}>Cancel</button>
            </div>
          </div>
        </div>
      ) : null}
      {pendingDelete ? (
        <div className="modal">
          <div>
            <h2>Delete this import?</h2>
            <p>{pendingDelete.original_filename} and its stored rows will be removed. The action cannot be undone from this screen.</p>
            <div className="row">
              <button className="button danger" onClick={() => void confirmDelete()}>Delete import</button>
              <button className="button ghost" onClick={() => setPendingDelete(null)}>Cancel</button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
