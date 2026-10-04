import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { ExplorerPage } from "./pages/ExplorerPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ImportReviewPage } from "./pages/ImportReviewPage";
import { OverviewPage } from "./pages/OverviewPage";
import { SetupPage } from "./pages/SetupPage";
import { UploadPage } from "./pages/UploadPage";
import { ValidationPage } from "./pages/ValidationPage";
import { OrderExplorerPage, QualityPage, ReviewPage, UdsDashboardPage } from "./pages/UdsPages";
import type { ImportSummary } from "./types";
import { rememberedImport } from "./workspace";

export function App() {
  const location = useLocation();
  const [imports, setImports] = useState<ImportSummary[]>([]);
  const [workspaceRevision, setWorkspaceRevision] = useState(0);
  const selected = rememberedImport();
  const current = imports.find((item) => item.id === selected) ?? imports.find((item) => item.status === "completed");

  useEffect(() => {
    const refresh = () => setWorkspaceRevision((value) => value + 1);
    window.addEventListener("sheetflow-workspace", refresh);
    return () => window.removeEventListener("sheetflow-workspace", refresh);
  }, []);

  useEffect(() => {
    let stopped = false;
    api.imports().then((body) => {
      if (!stopped) setImports(body.imports);
    }).catch(() => {
      if (!stopped) setImports([]);
    });
    return () => {
      stopped = true;
    };
  }, [location.pathname, workspaceRevision]);

  const overview = current ? `/imports/${current.id}` : "/";
  const explorer = current ? `/imports/${current.id}/explore` : "/explore";
  const validation = current ? `/imports/${current.id}/validation` : "/validation";

  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to={overview} className="brand">
          <span className="brand-mark">Sf</span>
          <span>
            <strong>SheetFlow</strong>
            <small>Workbook import</small>
          </span>
        </NavLink>
        <div className="workspace">{current ? current.original_filename : "No workbook selected"}</div>
      </header>
      <div className="shell-body">
        <nav className="sidenav" aria-label="Primary">
          <NavLink to={overview} end>Overview</NavLink>
          <NavLink to="/imports" end>Imports</NavLink>
          <NavLink to={explorer}>Data explorer</NavLink>
          <NavLink to={validation}>Validation</NavLink>
        </nav>
        <main>
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/imports" element={<HistoryPage />} />
            <Route path="/imports/:id/setup" element={<SetupPage />} />
            <Route path="/imports/:id/review" element={<ImportReviewPage />} />
            <Route path="/imports/:id/explore" element={<ExplorerPage />} />
            <Route path="/imports/:id/validation" element={<ValidationPage />} />
            <Route path="/imports/:id/orders" element={<OrderExplorerPage />} />
            <Route path="/imports/:id/quality" element={<QualityPage />} />
            <Route path="/imports/:id" element={<OverviewPage />} />
            <Route path="/explore" element={<EmptyDestination title="Data explorer" body="Import a workbook before searching its records." />} />
            <Route path="/validation" element={<EmptyDestination title="Validation" body="Import a workbook before reviewing its issues." />} />
            <Route path="/uds" element={<UdsDashboardPage />} />
            <Route path="/uds/review" element={<ReviewPage />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function EmptyDestination({ title, body }: { title: string; body: string }) {
  return (
    <section className="page">
      <h1>{title}</h1>
      <p className="lede">{body}</p>
      <Link className="button" to="/upload">Upload workbook</Link>
    </section>
  );
}
