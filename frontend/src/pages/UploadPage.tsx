import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, errorMessage } from "../api";
import type { ImportDetail } from "../types";
import { Steps } from "./ImportReviewPage";

export function UploadPage() {
  const navigate = useNavigate();
  const [limit, setLimit] = useState(20 * 1024 * 1024);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<File | null>(null);
  const [duplicates, setDuplicates] = useState<string[]>([]);

  useEffect(() => {
    api.health().then((health) => setLimit(health.limits.max_upload_bytes)).catch(() => setError("The API is not reachable."));
  }, []);

  async function send(file: File, acknowledge: boolean) {
    setError("");
    if (!file.name.toLowerCase().endsWith(".xls") && !file.name.toLowerCase().endsWith(".xlsx")) {
      setError("Choose an .xls or .xlsx file. Other workbook types are rejected.");
      return;
    }
    if (file.size > limit) {
      setError(`This file is larger than the ${Math.round(limit / (1024 * 1024))} MB upload limit.`);
      return;
    }
    setBusy(true);
    setProgress(0);
    try {
      const created = await uploadWithProgress(file, acknowledge, limit, setProgress);
      navigate(`/imports/${created.id}/setup`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409 && caught.detail && typeof caught.detail === "object") {
        const detail = caught.detail as { existing_import_ids?: string[] };
        setPending(file);
        setDuplicates(detail.existing_import_ids ?? []);
      } else {
        setError(errorMessage(caught));
      }
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  return (
    <section className="page">
      <Steps current={1} />
      <p className="eyebrow">Upload</p>
      <h1>Upload a workbook.</h1>
      <p className="lede">
        SheetFlow keeps the original file, shows a preview, and waits for you to choose the header row and column types. Sheets stay separate.
      </p>
      <div
        className={dragging ? "dropzone dragging" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files[0];
          if (file) void send(file, false);
        }}
      >
        <p>{busy ? "Uploading and inspecting…" : "Drop an .xls or .xlsx file here"}</p>
        <label className="button">
          Choose a file
          <input
            type="file"
            accept=".xls,.xlsx,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void send(file, false);
            }}
          />
        </label>
        <small>Supported formats: .xls and .xlsx. Limit {Math.round(limit / (1024 * 1024))} MB. Macros are not run.</small>
        {progress !== null ? <div className="progress" aria-label="Upload progress"><span style={{ width: `${progress}%` }} /></div> : null}
      </div>
      {error ? <p className="banner error">{error}</p> : null}
      {pending ? (
        <div className="modal">
          <div>
            <h2>This file was uploaded before</h2>
            <p>Creating another import keeps a separate copy. It does not replace the earlier one.</p>
            <ul>
              {duplicates.map((id) => (
                <li key={id}>
                  <code>{id}</code>
                </li>
              ))}
            </ul>
            <div className="row">
              <button className="button" onClick={() => pending && void send(pending, true)}>
                Create another import
              </button>
              <button className="button ghost" onClick={() => setPending(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function uploadWithProgress(file: File, acknowledge: boolean, limit: number, onProgress: (value: number) => void): Promise<ImportDetail> {
  if (file.size > limit) return Promise.reject(new ApiError(413, `This file is larger than the ${Math.round(limit / (1024 * 1024))} MB upload limit.`));
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.set("file", file);
    body.set("acknowledge_duplicate", acknowledge ? "true" : "false");
    const request = new XMLHttpRequest();
    request.open("POST", "/api/imports");
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () => reject(new ApiError(0, "The upload failed before the server responded."));
    request.onload = () => {
      const payload = request.responseText ? JSON.parse(request.responseText) : {};
      if (request.status >= 200 && request.status < 300) resolve(payload as ImportDetail);
      else reject(new ApiError(request.status, payload.detail ?? request.statusText));
    };
    request.send(body);
  });
}
