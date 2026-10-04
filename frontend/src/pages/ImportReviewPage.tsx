import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, missingImport } from "../api";
import type { ImportDetail } from "../types";
import { forgetImport, rememberImport } from "../workspace";

export function ImportReviewPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.detail(id).then((next) => {
      rememberImport(id);
      if (next.status === "completed") navigate(`/imports/${id}`);
      setDetail(next);
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
    if (!detail || (detail.status !== "queued" && detail.status !== "processing")) return;
    const timer = window.setInterval(() => {
      api.detail(id).then((next) => {
        setDetail(next);
        if (next.status === "completed") navigate(`/imports/${id}`);
        if (next.status === "failed") setError(next.error_message ?? "Processing failed.");
      }).catch((caught) => setError(errorMessage(caught)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [detail, id, navigate]);

  async function confirm() {
    setBusy(true);
    setError("");
    try {
      const queued = await api.process(id);
      setDetail(queued);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  const totals = detail?.report?.totals;
  const accepted = Number(totals?.accepted ?? 0);
  const rejected = Number(totals?.rejected ?? 0);
  const skipped = Number(totals?.skipped_empty ?? 0) + Number(totals?.skipped_explicit ?? 0);
  const inspected = Number(totals?.data_rows_inspected ?? 0);
  const sheets = detail?.configuration?.sheets.filter((sheet) => sheet.include) ?? [];

  return (
    <section className="page">
      <Steps current={3} id={id} />
      <h1>Review</h1>
      <p className="lede">These counts come from checking every selected row. Confirming the import saves the accepted rows and leaves rejected rows in the issue report.</p>
      {error ? <p className="banner error">{error}</p> : null}
      {(detail?.status === "queued" || detail?.status === "processing") && <p className="banner">Processing {detail.status}. The overview opens when the job completes.</p>}
      <section className="panel">
        <h2>{detail?.original_filename}</h2>
        <p>{detail ? `${Math.round(detail.byte_size / 1024)} KB` : ""} · {sheets.map((sheet) => sheet.name).join(", ") || "No sheets selected"}</p>
      </section>
      <div className="split-panels">
        <section className="panel">
          <h2>Import summary</h2>
          <p>Source rows {inspected}</p>
          <p>Accepted rows {accepted}</p>
          <p>Rejected rows {rejected}</p>
          <p>Skipped rows {skipped}</p>
        </section>
        <section className="panel">
          <h2>Needs your attention</h2>
          {(detail?.report?.issue_groups ?? []).filter((group) => group.severity === "error").length === 0 ? <p className="banner ok">No rejected rows.</p> : null}
          <ul>
            {(detail?.report?.issue_groups ?? []).filter((group) => group.severity === "error").map((group) => (
              <li key={`${group.sheet}-${group.code}-${group.column}`}>
                {group.sheet} · {group.column || "sheet"} · {group.code} · {group.count} rows
                <small className="muted"> Rows {group.source_rows.join(", ")}</small>
              </li>
            ))}
          </ul>
        </section>
      </div>
      {rejected > 0 ? <p className="banner warn">Continue with accepted rows. Rejected records remain in the issue report and are excluded from imported results.</p> : null}
      {accepted === 0 ? <p className="banner error">No rows were accepted. Return to configuration and fix the header row or column types before confirming.</p> : null}
      <div className="row">
        <Link className="button ghost" to={`/imports/${id}/setup`}>Return to configuration</Link>
        <button className="button" disabled={busy || accepted === 0 || detail?.status === "queued" || detail?.status === "processing"} onClick={() => void confirm()}>
          {rejected > 0 ? "Continue with accepted rows" : "Confirm import"}
        </button>
      </div>
    </section>
  );
}

export function Steps({ current, id }: { current: number; id?: string }) {
  const items = [
    ["Upload", "/upload"],
    ["Select sheets", id ? `/imports/${id}/setup` : "/upload"],
    ["Review", id ? `/imports/${id}/review` : "/upload"],
    ["Dashboard", id ? `/imports/${id}` : "/"],
  ];
  return (
    <div className="steps" aria-label="Import steps">
      {items.map(([label, href], index) => (
        <Link key={label} className={index + 1 === current ? "current" : ""} to={href}>{index + 1}. {label}</Link>
      ))}
    </div>
  );
}
