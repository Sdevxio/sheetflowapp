import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, missingImport } from "../api";
import { Steps } from "./ImportReviewPage";
import { forgetImport } from "../workspace";
import { COLUMN_TYPES, type ColumnConfig, type ImportDetail, type Preview, type SheetInspection } from "../types";

export function SetupPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [active, setActive] = useState("");
  const [included, setIncluded] = useState<Record<string, boolean>>({});
  const [headers, setHeaders] = useState<Record<string, number>>({});
  const [columns, setColumns] = useState<Record<string, ColumnConfig[]>>({});
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [locale, setLocale] = useState<"en-US" | "de-DE">("en-US");
  const [orderDetails, setOrderDetails] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stopped = false;
    api.detail(id).then(async (next) => {
      if (stopped) return;
      setDetail(next);
      const names = next.inspection?.sheets.map((sheet) => sheet.name) ?? [];
      const saved = new Map((next.configuration?.sheets ?? []).map((sheet) => [sheet.name, sheet.include]));
      setActive(names.find((name) => saved.get(name) !== false) ?? names[0] ?? "");
      setIncluded(Object.fromEntries(names.map((name) => [name, saved.has(name) ? saved.get(name) !== false : true])));
      setLocale(next.locale === "de-DE" ? "de-DE" : "en-US");
      const loaded = await Promise.all(names.map(async (name) => [name, await api.preview(id, name)] as const));
      if (stopped) return;
      setPreviews(Object.fromEntries(loaded.map(([name, preview]) => [name, preview])));
      setHeaders(Object.fromEntries(loaded.map(([name, preview]) => [name, preview.suggested_header_row])));
      setColumns(Object.fromEntries(loaded.map(([name, preview]) => [name, preview.columns.map(toColumn)])));
      setOrderDetails(loaded.some(([, preview]) => preview.order_details_profile));
    }).catch((caught) => {
      if (missingImport(caught)) {
        forgetImport(id);
        navigate("/", { replace: true });
        return;
      }
      setError(errorMessage(caught));
    });
    return () => {
      stopped = true;
    };
  }, [id, navigate]);

  async function changeHeader(sheet: string, headerRow: number) {
    setHeaders((current) => ({ ...current, [sheet]: headerRow }));
    try {
      const preview = await api.preview(id, sheet, headerRow);
      setPreviews((current) => ({ ...current, [sheet]: preview }));
      setColumns((current) => ({ ...current, [sheet]: preview.columns.map(toColumn) }));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  useEffect(() => {
    if (!detail || (detail.status !== "queued" && detail.status !== "processing")) return;
    const timer = window.setInterval(() => {
      api.detail(id).then(setDetail).catch((caught) => setError(errorMessage(caught)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [detail, id]);

  async function processImport() {
    setBusy(true);
    setError("");
    try {
      const sheets = (detail?.inspection?.sheets ?? []).filter((sheet) => included[sheet.name]).map((sheet) => ({
        name: sheet.name,
        include: true,
        header_row: headers[sheet.name] ?? sheet.suggested_header_row,
        columns: (columns[sheet.name] ?? []).map((column) => orderDetails ? withOrderType(column) : column),
      }));
      await api.saveConfig(id, { locale, sheets, profile: orderDetails ? "order_details" : "generic" });
      await api.review(id);
      navigate(`/imports/${id}/review`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const preview = previews[active];
  const selectedCount = Object.values(included).filter(Boolean).length;
  const isRuleBook = isRequirementsWorkbook(detail?.inspection?.sheets ?? []);

  return (
    <section className="page">
      <Steps current={2} id={id} />
      <p className="eyebrow">Select sheets</p>
      <h1>{detail?.original_filename ?? "Import"}</h1>
      <p className="lede">Each box is one sheet in the file. Leave it checked to import that sheet. Click the name to preview its columns. Unrelated sheets are not joined.</p>
      {isRuleBook ? (
        <p className="banner">
          This is the requirements file. Payer categories, lab orders, and practices are three separate lists. Leave all three checked.
          To apply them to the orders, upload this same file under Mapping workbook on <Link to="/uds">Order reconciliation</Link>.
        </p>
      ) : null}
      {error ? <p className="banner error">{error}</p> : null}
      {detail?.status === "failed" ? <p className="banner error">{detail.error_message}</p> : null}
      {detail?.status === "completed" ? (
        <p className="banner ok">
          Processing finished.{" "}
          <Link to={`/imports/${id}`}>Open the overview</Link>
        </p>
      ) : null}
      {(detail?.status === "queued" || detail?.status === "processing") && (
        <p className="banner">Processing in the background. This page updates when the job finishes.</p>
      )}
      <div className="split">
        <aside className="sheet-list">
          {(detail?.inspection?.sheets ?? []).map((sheet) => {
            const isIncluded = included[sheet.name] ?? false;
            return (
              <div key={sheet.name} className={sheet.name === active ? "sheet active" : isIncluded ? "sheet" : "sheet excluded"}>
                <label>
                  <input
                    type="checkbox"
                    checked={isIncluded}
                    aria-label={`Include ${sheet.name}`}
                    onChange={(event) => setIncluded((current) => ({ ...current, [sheet.name]: event.target.checked }))}
                  />
                  Include
                </label>
                <button type="button" className="sheet-name" onClick={() => setActive(sheet.name)}>
                  {sheet.name}
                </button>
                <SheetMeta sheet={sheet} />
              </div>
            );
          })}
          {Object.values(previews).some((item) => item.order_details_profile) ? (
            <label className="field">
              <input type="checkbox" checked={orderDetails} onChange={(event) => setOrderDetails(event.target.checked)} /> Order details
            </label>
          ) : null}
          <label className="field">
            Locale
            <select value={locale} onChange={(event) => setLocale(event.target.value as "en-US" | "de-DE")}>
              <option value="en-US">en-US · MDY, comma thousands</option>
              <option value="de-DE">de-DE · DMY, dot thousands</option>
            </select>
          </label>
        </aside>
        <div>
          {!preview ? <p>Loading preview…</p> : <PreviewPanel preview={preview} headerRow={headers[active] ?? preview.header_row} columns={columns[active] ?? []} onHeader={(value) => void changeHeader(active, value)} onType={(index, type) => setColumns((current) => ({ ...current, [active]: (current[active] ?? []).map((column) => column.index === index ? { ...column, type } : column) }))} onRequired={(index, required) => setColumns((current) => ({ ...current, [active]: (current[active] ?? []).map((column) => column.index === index ? { ...column, required } : column) }))} />}
        </div>
      </div>
      <div className="confirm">
        <div>
          <strong>{selectedCount} sheet{selectedCount === 1 ? "" : "s"} selected</strong>
          <p>Processing reads every data row. If a sheet is over the limit, the import fails and nothing is published.</p>
        </div>
        <button className="button" disabled={busy || selectedCount === 0 || detail?.status === "queued" || detail?.status === "processing" || detail?.status === "completed"} onClick={() => void processImport()}>
          {busy ? "Checking rows…" : "Review import"}
        </button>
      </div>
    </section>
  );
}

function withOrderType(column: ColumnConfig): ColumnConfig {
  const name = column.original_name.trim().toLowerCase();
  if (name === "report id" || name === "patient acct no") return { ...column, type: "identifier" };
  if (name.includes("date")) return { ...column, type: "date" };
  if (column.type === "integer" || column.type === "decimal") return { ...column, type: "text" };
  return column;
}

function toColumn(column: Preview["columns"][number]): ColumnConfig {
  const name = column.original_name.trim().toLowerCase();
  const type = name === "order id" ? "identifier" : name === "date" ? "date" : column.suggested_type;
  return {
    index: column.index,
    original_name: column.original_name,
    normalized_name: column.normalized_name,
    type,
    date_format: null,
    required: name === "order id",
  };
}

function SheetMeta({ sheet }: { sheet: SheetInspection }) {
  const purpose = sheetPurpose(sheet.name);
  return (
    <small>
      {purpose ? `${purpose} ` : ""}
      {sheet.physical_rows_exact ? `${sheet.physical_rows} rows` : "over the row limit"} · header {sheet.suggested_header_row}
      {sheet.formula_missing_cache ? ` · ${sheet.formula_missing_cache} uncached formulas` : ""}
    </small>
  );
}

function sheetPurpose(name: string): string {
  switch (name.trim().toLowerCase()) {
    case "payer and categories":
      return "Which insurance belongs to which payer category.";
    case "lab co and labs":
      return "Which order name is a screening or a confirmation.";
    case "practices":
      return "Which facility belongs to which practice.";
    default:
      return "";
  }
}

function isRequirementsWorkbook(sheets: { name: string }[]): boolean {
  const names = new Set(sheets.map((sheet) => sheet.name.trim().toLowerCase()));
  return ["payer and categories", "lab co and labs", "practices"].every((name) => names.has(name));
}

function PreviewPanel({
  preview,
  headerRow,
  columns,
  onHeader,
  onType,
  onRequired,
}: {
  preview: Preview;
  headerRow: number;
  columns: ColumnConfig[];
  onHeader: (value: number) => void;
  onType: (index: number, type: string) => void;
  onRequired: (index: number, required: boolean) => void;
}) {
  return (
    <div className="panel">
      <div className="row spread">
        <h2>{preview.sheet}</h2>
        <label className="field inline">
          Header row
          <input type="number" min={1} value={headerRow} onChange={(event) => onHeader(Number(event.target.value))} />
        </label>
      </div>
      <p className="note">{preview.preview_note}</p>
      {preview.warnings.map((warning) => (
        <p key={warning} className="banner warn">{warning}</p>
      ))}
      <p className="meta">
        {preview.grid_truncated ? `Preview shows ${preview.grid.length} of the rows read.` : `Showing ${preview.grid.length} physical rows.`}
        {preview.data_row_count !== null ? ` ${preview.data_row_count} data rows follow the header.` : " Full data-row count is unavailable because the sheet exceeds a limit."}
      </p>
      <div className="table-wrap">
        <table>
          <tbody>
            {preview.grid.map((row) => (
              <tr key={row.source_row} className={row.source_row === headerRow ? "header-row" : ""}>
                <th>{row.source_row}</th>
                {row.cells.map((cell, index) => (
                  <td key={`${row.source_row}-${index}`}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Column types</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Original name</th>
              <th>Normalized</th>
              <th>Type</th>
              <th>Required</th>
              <th>Samples</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => {
              const suggestion = preview.columns.find((item) => item.index === column.index);
              return (
                <tr key={column.normalized_name}>
                  <td>{column.original_name}</td>
                  <td><code>{column.normalized_name}</code></td>
                  <td>
                    <select value={column.type} onChange={(event) => onType(column.index, event.target.value)}>
                      {COLUMN_TYPES.map((type) => (
                        <option key={type} value={type}>{type}</option>
                      ))}
                    </select>
                    {suggestion?.mixed ? <small> Mixed values. Invalid cells will be reported, not replaced.</small> : null}
                  </td>
                  <td><input aria-label={`${column.original_name} required`} type="checkbox" checked={column.required ?? false} onChange={(event) => onRequired(column.index, event.target.checked)} /></td>
                  <td>{suggestion?.samples.join(", ")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
