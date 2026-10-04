"""UDS reconciliation endpoints."""

import csv
import io
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MappingEntry, MappingVersion, UdsIssue, UdsLine, UdsLink, UdsObservation, UdsResolution
from app.services.security import sanitize_export_value
from app.services.uds import UdsError, latest_mapping, load_mapping_workbook, public_observation, refresh_links, reclassify_all

router = APIRouter(prefix="/api/uds", tags=["uds"])


class ResolutionIn(BaseModel):
    report_id: str | None = None
    code: str = Field(min_length=1, max_length=64)
    scope: Literal["order", "mapping"]
    decision: dict
    reason: str = Field(min_length=1, max_length=2000)
    actor: str = Field(min_length=1, max_length=200)


class LinkDecision(BaseModel):
    status: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1, max_length=2000)
    actor: str = Field(min_length=1, max_length=200)


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)) -> dict:
    versions = db.scalars(select(MappingVersion).order_by(MappingVersion.version_number.desc())).all()
    payload = []
    for version in versions:
        counts = dict(
            db.execute(
                select(MappingEntry.kind, func.count()).where(MappingEntry.version_id == version.id).group_by(MappingEntry.kind)
            ).all()
        )
        conflicts = db.scalar(
            select(func.count()).select_from(MappingEntry).where(MappingEntry.version_id == version.id, MappingEntry.conflict.is_(True))
        )
        payload.append(
            {
                "id": str(version.id),
                "version_number": version.version_number,
                "original_filename": version.original_filename,
                "created_at": version.created_at.isoformat(),
                "counts": {kind: counts.get(kind, 0) for kind in ("facility", "payer", "order")},
                "conflicts": conflicts or 0,
            }
        )
    return {"versions": payload}


@router.post("/mappings")
async def upload_mapping(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The mapping workbook is empty.")
    try:
        version = load_mapping_workbook(payload, file.filename or "mapping.xlsx", db)
        db.commit()
    except UdsError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"version_number": version.version_number, "id": str(version.id)}


@router.get("/orders")
def list_orders(
    import_id: uuid.UUID | None = None,
    current_only: bool = True,
    practice: str | None = None,
    facility: str | None = None,
    order_class: str | None = None,
    billing: str | None = None,
    confirmation: str | None = None,
    lifecycle: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    orders = _filtered(db, import_id, current_only, practice, facility, order_class, billing, confirmation, lifecycle)
    return {
        "counting_unit": "order (one Report ID)",
        "orders": [public_observation(item) for item in orders],
    }


@router.get("/orders/{report_id}")
def order_detail(report_id: str, import_id: uuid.UUID | None = None, db: Session = Depends(get_db)) -> dict:
    query = select(UdsObservation).where(UdsObservation.report_id == report_id)
    if import_id is not None:
        query = query.where(UdsObservation.import_id == import_id)
    observations = db.scalars(query.order_by(UdsObservation.id)).all()
    if not observations:
        raise HTTPException(status_code=404, detail="No observation exists for this Report ID.")
    details = []
    for observation in observations:
        lines = db.scalars(select(UdsLine).where(UdsLine.observation_id == observation.id).order_by(UdsLine.source_row)).all()
        issues = db.scalars(select(UdsIssue).where(UdsIssue.observation_id == observation.id)).all()
        details.append(
            {
                **public_observation(observation),
                "lines": [
                    {
                        "source_row": line.source_row,
                        "role": line.role,
                        "lab_attribute": line.lab_attribute,
                        "lab_attribute_value": line.lab_attribute_value,
                        "icd_code": line.icd_code,
                    }
                    for line in lines
                ],
                "issues": [_public_issue(issue) for issue in issues],
            }
        )
    links = db.scalars(
        select(UdsLink).where((UdsLink.screening_report_id == report_id) | (UdsLink.confirmation_report_id == report_id))
    ).all()
    return {"observations": details, "links": [_public_link(link) for link in links], "counting_unit": "order (one Report ID)"}


@router.get("/quality")
def quality(import_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    issues = db.scalars(select(UdsIssue).where(UdsIssue.import_id == import_id).order_by(UdsIssue.code, UdsIssue.id)).all()
    grouped: dict[str, list] = {}
    for issue in issues:
        grouped.setdefault(issue.code, []).append(_public_issue(issue))
    order_count = db.scalar(select(func.count()).select_from(UdsObservation).where(UdsObservation.import_id == import_id)) or 0
    return {"import_id": str(import_id), "order_count": order_count, "counting_unit": "order (distinct Report ID in this import)", "groups": grouped}


@router.get("/summary")
def summary(
    import_id: uuid.UUID | None = None,
    current_only: bool = True,
    practice: str | None = None,
    facility: str | None = None,
    order_class: str | None = None,
    billing: str | None = None,
    confirmation: str | None = None,
    lifecycle: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    orders = _filtered(db, import_id, current_only, practice, facility, order_class, billing, confirmation, lifecycle)
    return _summary(db, orders)


@router.get("/review")
def review_queue(db: Session = Depends(get_db)) -> dict:
    issues = db.scalars(select(UdsIssue).order_by(UdsIssue.id.desc()).limit(500)).all()
    links = db.scalars(select(UdsLink).where(UdsLink.status == "suggested").order_by(UdsLink.id.desc())).all()
    resolutions = db.scalars(select(UdsResolution).order_by(UdsResolution.id.desc()).limit(200)).all()
    return {
        "issues": [_public_issue(issue) for issue in issues],
        "suggested_links": [_public_link(link) for link in links],
        "resolutions": [_public_resolution(item) for item in resolutions],
    }


@router.post("/resolutions")
def add_resolution(body: ResolutionIn, db: Session = Depends(get_db)) -> dict:
    if body.scope == "order" and not body.report_id:
        raise HTTPException(status_code=400, detail="An order resolution needs a Report ID.")
    decision = dict(body.decision)
    if body.report_id:
        observed = db.scalar(select(UdsObservation).where(UdsObservation.report_id == body.report_id).order_by(UdsObservation.is_current.desc()))
        if observed is not None:
            decision.setdefault("payer", observed.payer_name)
            decision.setdefault("facility", observed.facility)
            decision.setdefault("order_name", observed.order_name)
    row = UdsResolution(
        report_id=body.report_id,
        code=body.code,
        scope=body.scope,
        decision=decision,
        reason=body.reason,
        actor=body.actor,
        active=True,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    version = latest_mapping(db)
    if version is not None:
        reclassify_all(db, version)
    db.commit()
    return _public_resolution(row)


@router.post("/links/{link_id}")
def decide_link(link_id: int, body: LinkDecision, db: Session = Depends(get_db)) -> dict:
    link = db.get(UdsLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found.")
    link.status = body.status
    link.reason = body.reason
    db.add(
        UdsResolution(
            report_id=link.screening_report_id,
            code="CONFIRMATION_LINK",
            scope="order",
            decision={"status": body.status, "confirmation_report_id": link.confirmation_report_id},
            reason=body.reason,
            actor=body.actor,
            active=True,
            created_at=_now(),
        )
    )
    refresh_links(db)
    db.commit()
    db.refresh(link)
    return _public_link(link)


@router.get("/export")
def export_orders(
    import_id: uuid.UUID | None = None,
    current_only: bool = True,
    practice: str | None = None,
    facility: str | None = None,
    order_class: str | None = None,
    billing: str | None = None,
    confirmation: str | None = None,
    lifecycle: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    orders = _filtered(db, import_id, current_only, practice, facility, order_class, billing, confirmation, lifecycle)
    headers = [
        "counting_unit",
        "report_id",
        "order_name",
        "facility",
        "practice",
        "payer_category",
        "provisional_account",
        "provisional_encounter_date",
        "order_class",
        "lifecycle",
        "result",
        "billing",
        "confirmation",
        "compliance",
        "is_current",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for order in orders:
        classification = order.classification or {}
        writer.writerow(
            [
                sanitize_export_value("order (one Report ID)"),
                sanitize_export_value(order.report_id),
                sanitize_export_value(order.order_name),
                sanitize_export_value(order.facility),
                sanitize_export_value(order.practice),
                sanitize_export_value(order.payer_category),
                sanitize_export_value(order.account_id),
                sanitize_export_value(order.encounter_date),
                sanitize_export_value(order.order_class),
                sanitize_export_value((classification.get("lifecycle") or {}).get("value")),
                sanitize_export_value((classification.get("result") or {}).get("value")),
                sanitize_export_value((classification.get("billing") or {}).get("value")),
                sanitize_export_value((classification.get("confirmation") or {}).get("value")),
                sanitize_export_value((classification.get("compliance") or {}).get("value")),
                sanitize_export_value(order.is_current),
            ]
        )
    return Response(
        content=buffer.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="uds-orders.csv"'},
    )


def _filtered(db, import_id, current_only, practice, facility, order_class, billing, confirmation, lifecycle):
    query = select(UdsObservation).order_by(UdsObservation.report_id)
    if import_id is not None:
        query = query.where(UdsObservation.import_id == import_id)
    if current_only:
        query = query.where(UdsObservation.is_current.is_(True))
    if practice:
        query = query.where(UdsObservation.practice == practice)
    if facility:
        query = query.where(UdsObservation.facility == facility)
    if order_class:
        query = query.where(UdsObservation.order_class == order_class)
    orders = db.scalars(query).all()

    def matches(order: UdsObservation) -> bool:
        classification = order.classification or {}
        if billing and (classification.get("billing") or {}).get("value") != billing:
            return False
        if confirmation and (classification.get("confirmation") or {}).get("value") != confirmation:
            return False
        if lifecycle and (classification.get("lifecycle") or {}).get("value") != lifecycle:
            return False
        return True

    return [order for order in orders if matches(order)]


def _summary(db: Session, orders: list[UdsObservation]) -> dict:
    def count(predicate) -> int:
        return sum(1 for order in orders if predicate(order))

    def value(order: UdsObservation, dimension: str) -> str | None:
        return ((order.classification or {}).get(dimension) or {}).get("value")

    screenings = [
        order
        for order in orders
        if order.order_class == "screening" and value(order, "result") == "positive" and value(order, "lifecycle") != "cancelled"
    ]
    accepted = {
        link.screening_report_id
        for link in db.scalars(select(UdsLink).where(UdsLink.status == "accepted")).all()
    }
    numerator = sum(1 for order in screenings if order.report_id in accepted)
    denominator = len(screenings)
    rate = None if denominator == 0 else round(numerator / denominator, 4)
    return {
        "counting_unit": "order (one current Report ID unless a single import is selected)",
        "orders": len(orders),
        "screenings": count(lambda order: order.order_class == "screening"),
        "confirmations": count(lambda order: order.order_class == "confirmation"),
        "unclassified": count(lambda order: order.order_class == "unclassified"),
        "positive": count(lambda order: value(order, "result") == "positive"),
        "not_established": count(lambda order: value(order, "result") == "not_established"),
        "cancelled_visible": count(lambda order: value(order, "lifecycle") == "cancelled"),
        "billing_excluded_cancelled": count(lambda order: value(order, "billing") == "excluded_cancelled"),
        "billing_review": count(lambda order: value(order, "billing") == "review_required"),
        "billing_eligible": count(lambda order: value(order, "billing") == "eligible"),
        "follow_up": count(lambda order: value(order, "confirmation") in {"no_confirmation_in_available_data", "suggested", "linkage_uncertain"}),
        "compliance_exception": count(lambda order: value(order, "compliance") == "confirmed_exception"),
        "compliance_insufficient": count(lambda order: value(order, "compliance") == "insufficient_evidence"),
        "identity_conflicts": count(lambda order: order.identity_conflict),
        "conversion": {
            "label": "Accepted confirmation links ÷ positive screenings that are not cancelled",
            "numerator": numerator,
            "denominator": denominator,
            "rate": rate,
            "display": "N/A" if rate is None else rate,
        },
    }


def _public_issue(issue: UdsIssue) -> dict:
    return {
        "id": issue.id,
        "import_id": str(issue.import_id),
        "report_id": issue.report_id,
        "code": issue.code,
        "message": issue.message,
        "source_rows": issue.source_rows or [],
    }


def _public_link(link: UdsLink) -> dict:
    return {
        "id": link.id,
        "screening_report_id": link.screening_report_id,
        "confirmation_report_id": link.confirmation_report_id,
        "status": link.status,
        "reason": link.reason,
    }


def _public_resolution(row: UdsResolution) -> dict:
    return {
        "id": row.id,
        "report_id": row.report_id,
        "code": row.code,
        "scope": row.scope,
        "decision": row.decision,
        "reason": row.reason,
        "actor": row.actor,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
