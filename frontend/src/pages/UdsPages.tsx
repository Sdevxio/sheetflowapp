import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage, udsQuery } from "../api";
import type { UdsFilters, UdsIssue, UdsOrder, UdsOrderDetail, UdsQuality, UdsReview, UdsSummary } from "../types";

const EMPTY: UdsFilters = {
  importId: "",
  currentOnly: true,
  practice: "",
  facility: "",
  orderClass: "",
  billing: "",
  confirmation: "",
  lifecycle: "",
};

const VIEWS = [
  ["overview", "Overview"],
  ["follow-up", "Follow-up"],
  ["billing", "Billing"],
  ["compliance", "Compliance"],
  ["explorer", "Explorer"],
] as const;

export function UdsDashboardPage() {
  const [filters, setFilters] = useState<UdsFilters>(EMPTY);
  const [view, setView] = useState<(typeof VIEWS)[number][0]>("overview");
  const [summary, setSummary] = useState<UdsSummary | null>(null);
  const [orders, setOrders] = useState<UdsOrder[]>([]);
  const [versions, setVersions] = useState<{ version_number: number; original_filename: string; conflicts: number }[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const query = udsQuery(filters);
    Promise.all([api.udsSummary(query), api.udsOrders(query), api.udsMappings()])
      .then(([nextSummary, nextOrders, mappings]) => {
        setSummary(nextSummary);
        setOrders(nextOrders.orders);
        setVersions(mappings.versions);
      })
      .catch((caught) => setError(errorMessage(caught)));
  }, [filters]);

  const visible = orders.filter((order) => {
    const confirmation = order.classification.confirmation?.value;
    const billing = order.classification.billing?.value;
    const compliance = order.classification.compliance?.value;
    if (view === "follow-up") return confirmation === "no_confirmation_in_available_data" || confirmation === "suggested" || confirmation === "linkage_uncertain";
    if (view === "billing") return billing === "eligible" || billing === "not_eligible" || billing === "pending" || billing === "review_required" || billing === "excluded_cancelled";
    if (view === "compliance") return compliance === "confirmed_exception" || compliance === "insufficient_evidence";
    return true;
  });

  return (
    <section className="page">
      <p className="eyebrow">UDS</p>
      <h1>Order reconciliation</h1>
      <p className="lede">Counts are orders, one Report ID each. Account and encounter labels are provisional. Confirmation links count only after a reviewer accepts them.</p>
      {error ? <p className="banner error">{error}</p> : null}
      <MappingUpload onUploaded={() => setFilters({ ...filters })} versions={versions} />
      <FilterBar filters={filters} onChange={setFilters} />
      <div className="tabs">
        {VIEWS.map(([id, label]) => (
          <button key={id} className={view === id ? "button" : "button ghost"} onClick={() => setView(id)}>{label}</button>
        ))}
        <Link className="button ghost" to="/uds/review">Review queue</Link>
        <a className="button ghost" href={`/api/uds/export${udsQuery(filters)}`}>Export these orders</a>
      </div>
      {summary ? <SummaryCards summary={summary} view={view} /> : null}
      <OrderTable orders={visible} />
    </section>
  );
}

export function OrderExplorerPage() {
  const { id = "" } = useParams();
  const [orders, setOrders] = useState<UdsOrder[]>([]);
  const [selected, setSelected] = useState<UdsOrderDetail | null>(null);
  const [error, setError] = useState("");
  const filters = { ...EMPTY, importId: id, currentOnly: false };

  useEffect(() => {
    api.udsOrders(udsQuery(filters)).then((body) => setOrders(body.orders)).catch((caught) => setError(errorMessage(caught)));
  }, [id]);

  async function open(reportId: string) {
    try {
      setSelected(await api.udsOrder(reportId, id));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <section className="page">
      <p className="eyebrow">This import</p>
      <h1>Order explorer</h1>
      <p className="lede">Each row is one order. Provisional account and provisional encounter are not patient counts.</p>
      <p><Link to={`/imports/${id}/quality`}>Import quality</Link></p>
      {error ? <p className="banner error">{error}</p> : null}
      <OrderTable orders={orders} onOpen={(reportId) => void open(reportId)} />
      {selected ? <ObservationDetail detail={selected} /> : null}
    </section>
  );
}

export function QualityPage() {
  const { id = "" } = useParams();
  const [quality, setQuality] = useState<UdsQuality | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.udsQuality(id).then(setQuality).catch((caught) => setError(errorMessage(caught)));
  }, [id]);

  return (
    <section className="page">
      <p className="eyebrow">Import quality</p>
      <h1>{quality ? `${quality.order_count} orders` : "Quality"}</h1>
      <p className="lede">{quality?.counting_unit}. Each issue links to the order and the source rows that produced it.</p>
      <p><Link to={`/imports/${id}/orders`}>Back to orders</Link></p>
      {error ? <p className="banner error">{error}</p> : null}
      {quality && Object.keys(quality.groups).length === 0 ? <p className="banner ok">No quality issues on this import.</p> : null}
      {Object.entries(quality?.groups ?? {}).map(([code, issues]) => (
        <section key={code}>
          <h2>{code} · {issues.length}</h2>
          <IssueList issues={issues} />
        </section>
      ))}
    </section>
  );
}

export function ReviewPage() {
  const [review, setReview] = useState<UdsReview | null>(null);
  const [actor, setActor] = useState("local user");
  const [reason, setReason] = useState("");
  const [category, setCategory] = useState("");
  const [practice, setPractice] = useState("");
  const [orderClass, setOrderClass] = useState("");
  const [error, setError] = useState("");

  function reload() {
    api.udsReview().then(setReview).catch((caught) => setError(errorMessage(caught)));
  }

  useEffect(() => {
    reload();
  }, []);

  async function decide(id: number, status: "accepted" | "rejected") {
    if (!reason.trim()) {
      setError("Enter why this decision was made.");
      return;
    }
    try {
      await api.decideLink(id, { status, reason, actor });
      setReason("");
      reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function resolve(issue: UdsIssue, scope: "order" | "mapping") {
    if (!reason.trim()) {
      setError("Enter why this decision was made.");
      return;
    }
    try {
      await api.resolveIssue({
        report_id: issue.report_id,
        code: issue.code,
        scope,
        decision: { category, practice, order_class: orderClass },
        reason,
        actor,
      });
      setReason("");
      reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <section className="page">
      <p className="eyebrow">Review</p>
      <h1>Unresolved orders</h1>
      <p className="lede">A resolution is stored apart from the imported rows. A later file does not delete it.</p>
      {error ? <p className="banner error">{error}</p> : null}
      <div className="filters">
        <label className="field">Who is entering this<input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
        <label className="field">Why<input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <label className="field">Payer category<input value={category} onChange={(event) => setCategory(event.target.value)} /></label>
        <label className="field">Practice<input value={practice} onChange={(event) => setPractice(event.target.value)} /></label>
        <label className="field">Order class<input value={orderClass} onChange={(event) => setOrderClass(event.target.value)} /></label>
      </div>
      <h2>Suggested confirmation links</h2>
      {(review?.suggested_links ?? []).length === 0 ? <p>No suggested links are waiting.</p> : null}
      {(review?.suggested_links ?? []).map((link) => (
        <article key={link.id} className="uds-card">
          <p>Screening {link.screening_report_id} → confirmation {link.confirmation_report_id}</p>
          <p>{link.reason}</p>
          <button className="button" onClick={() => void decide(link.id, "accepted")}>Accept</button>
          <button className="button ghost" onClick={() => void decide(link.id, "rejected")}>Reject</button>
        </article>
      ))}
      <h2>Quality issues</h2>
      <IssueList issues={review?.issues ?? []} onResolve={resolve} />
      <h2>Recorded resolutions</h2>
      <ul>
        {(review?.resolutions ?? []).map((item) => (
          <li key={item.id}>{item.created_at} · {item.actor} · {item.scope} · {item.code} · {item.reason}</li>
        ))}
      </ul>
    </section>
  );
}

function MappingUpload({ versions, onUploaded }: { versions: { version_number: number; original_filename: string; conflicts: number }[]; onUploaded: () => void }) {
  const [error, setError] = useState("");

  async function upload(file: File | undefined) {
    if (!file) return;
    try {
      await api.uploadMapping(file);
      onUploaded();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div className="panel">
      <h2>Mapping workbook</h2>
      <p className="note">Upload the requirements workbook. Each upload becomes the next numbered version.</p>
      <input aria-label="Mapping workbook" type="file" accept=".xlsx,.xlsm" onChange={(event) => void upload(event.target.files?.[0])} />
      {error ? <p className="banner error">{error}</p> : null}
      <ul>
        {versions.map((version) => (
          <li key={version.version_number}>Version {version.version_number}: {version.original_filename} · {version.conflicts} conflicting entries</li>
        ))}
      </ul>
    </div>
  );
}

function FilterBar({ filters, onChange }: { filters: UdsFilters; onChange: (filters: UdsFilters) => void }) {
  function set(patch: Partial<UdsFilters>) {
    onChange({ ...filters, ...patch });
  }
  return (
    <div className="filters">
      <label className="field">Practice<input value={filters.practice} onChange={(event) => set({ practice: event.target.value })} /></label>
      <label className="field">Facility<input value={filters.facility} onChange={(event) => set({ facility: event.target.value })} /></label>
      <label className="field">Order class
        <select value={filters.orderClass} onChange={(event) => set({ orderClass: event.target.value })}>
          <option value="">Any</option>
          <option value="screening">screening</option>
          <option value="confirmation">confirmation</option>
          <option value="unclassified">unclassified</option>
        </select>
      </label>
      <label className="field">Billing
        <select value={filters.billing} onChange={(event) => set({ billing: event.target.value })}>
          <option value="">Any</option>
          <option value="eligible">eligible</option>
          <option value="not_eligible">not eligible</option>
          <option value="pending">pending</option>
          <option value="review_required">review required</option>
          <option value="excluded_cancelled">excluded, cancelled</option>
        </select>
      </label>
      <label className="field">Lifecycle
        <select value={filters.lifecycle} onChange={(event) => set({ lifecycle: event.target.value })}>
          <option value="">Any</option>
          <option value="open">open</option>
          <option value="reviewed">reviewed</option>
          <option value="cancelled">cancelled</option>
        </select>
      </label>
    </div>
  );
}

function SummaryCards({ summary, view }: { summary: UdsSummary; view: string }) {
  const cards: [string, string | number][] =
    view === "follow-up"
      ? [["Follow-up orders", summary.follow_up], ["Positive screenings", summary.positive], ["Conversion", summary.conversion.display]]
      : view === "billing"
        ? [["Eligible", summary.billing_eligible], ["Needs review", summary.billing_review], ["Cancelled, excluded from forecast", summary.billing_excluded_cancelled]]
        : view === "compliance"
          ? [["Confirmed exception", summary.compliance_exception], ["Insufficient evidence", summary.compliance_insufficient]]
          : [
              ["Orders", summary.orders],
              ["Screenings", summary.screenings],
              ["Confirmations", summary.confirmations],
              ["Unclassified", summary.unclassified],
              ["Positive", summary.positive],
              ["Result not established", summary.not_established],
              ["Conversion", summary.conversion.display],
            ];
  return (
    <>
      <p className="note">{summary.counting_unit}. {summary.conversion.label}. Cancelled screenings are omitted from the conversion denominator.</p>
      <div className="uds-cards">
        {cards.map(([label, value]) => (
          <article key={label} className="uds-card">
            <small>{label}</small>
            <strong>{value}</strong>
          </article>
        ))}
      </div>
    </>
  );
}

function OrderTable({ orders, onOpen }: { orders: UdsOrder[]; onOpen?: (reportId: string) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Report ID</th>
            <th>Order</th>
            <th>Facility</th>
            <th>Practice</th>
            <th>Payer category</th>
            <th>Provisional account</th>
            <th>Provisional encounter</th>
            <th>Lines</th>
            <th>Result</th>
            <th>Billing</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr key={`${order.import_id}-${order.report_id}`}>
              <td>{onOpen ? <button className="linkish" onClick={() => onOpen(order.report_id)}>{order.report_id}</button> : order.report_id}</td>
              <td>{order.order_name}</td>
              <td>{order.facility}</td>
              <td>{order.practice ?? "—"}</td>
              <td>{order.payer_category ?? "—"}</td>
              <td>{order.provisional_account}</td>
              <td>{order.provisional_encounter_date}</td>
              <td>{order.line_count} · {order.analyte_count} analyte{order.analyte_count === 1 ? "" : "s"} · {order.comment_count} comment{order.comment_count === 1 ? "" : "s"}</td>
              <td>{order.classification.result?.value}</td>
              <td>{order.classification.billing?.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ObservationDetail({ detail }: { detail: UdsOrderDetail }) {
  return (
    <div>
      {detail.observations.map((observation) => (
        <section key={observation.id}>
          <h2>Report {observation.report_id}</h2>
          <p>{observation.selection_reason}</p>
          <p>Lab from order name: {observation.lab_from_order ?? "none"} ({observation.lab_source}). Expected lab from the practice rule: {observation.expected_lab ?? "not assigned"}.</p>
          {Object.entries(observation.classification).map(([name, item]) => (
            <p key={name}><strong>{name}:</strong> {item.value}. {item.reason}</p>
          ))}
          <IssueList issues={observation.issues} />
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source row</th><th>Role</th><th>Attribute</th><th>Value</th></tr></thead>
              <tbody>
                {observation.lines.map((line) => (
                  <tr key={line.source_row}><td>{line.source_row}</td><td>{line.role}</td><td>{line.lab_attribute}</td><td>{line.lab_attribute_value}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function IssueList({ issues, onResolve }: { issues: UdsIssue[]; onResolve?: (issue: UdsIssue, scope: "order" | "mapping") => void }) {
  return (
    <ul>
      {issues.map((issue) => (
        <li key={issue.id}>
          <Link to={`/imports/${issue.import_id}/orders`}>{issue.code}</Link> · Report {issue.report_id ?? "—"} · rows {issue.source_rows.join(", ") || "—"} · {issue.message}
          {onResolve ? (
            <>
              <button className="button ghost" onClick={() => onResolve(issue, "order")}>Resolve this order</button>
              <button className="button ghost" onClick={() => onResolve(issue, "mapping")}>Save as mapping</button>
            </>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
