"""Fabric BOM lifecycle: create, version, edit (draft only), approve, obsolete.

Version control rules
---------------------
* A BOM always has at least one version; new versions start as ``DRAFT``.
* Only ``DRAFT`` versions are editable; ``APPROVED`` versions are frozen.
* Approving a version snapshots its standard-cost roll-up and makes it the
  *effective* version from ``effective_from``. The previously-effective approved
  version is **superseded** (its ``effective_to`` is closed the day before) but
  is **preserved** — production orders created against it keep pointing at it.
* ``effective_version(on_date)`` returns the one approved version effective on a
  date; historical orders are unaffected by later revisions.

Standard cost roll-up (per kg finished fabric)
----------------------------------------------
    yarn        = Σ  gross_consumption(yarn_i) * std_unit_cost(yarn_i)
                  where gross = std_consumption_per_kg * (1 + wastage%)        \
                                                       * (1 + process_loss%)
    dye_chem    = Σ  (percent_owf/100 or grams_per_kg/1000) * std_cost(chem)
                  over the linked dyeing RecipeVersion's materials
    finishing   = Σ  std_cost_per_kg(step)
    total/kg    = yarn + dye_chem + knitting + dyeing + finishing
                       + labour + overhead
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, QTY_SCALE, quantize
from app.models.fabric_bom import (
    BomStatus,
    FabricBom,
    FabricBomFinishing,
    FabricBomKnitting,
    FabricBomVersion,
    FabricBomYarn,
)
from app.models.masters import Chemical, RecipeMaterial, RecipeVersion

MZERO = Decimal("0")


class BomError(Exception):
    pass


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else MZERO


def _today() -> date:
    return datetime.now(timezone.utc).date()


# --- creation / versioning --------------------------------------------------

def create_bom(
    db: Session,
    *,
    company_id: uuid.UUID,
    code: str,
    name: str,
    fabric_type_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    shade_id: uuid.UUID | None = None,
    finished_quality: str | None = None,
    uom_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> FabricBom:
    exists = db.execute(
        select(FabricBom).where(FabricBom.company_id == company_id, FabricBom.code == code)
    ).scalar_one_or_none()
    if exists is not None:
        raise BomError(f"A fabric BOM with code '{code}' already exists for this company")
    bom = FabricBom(
        company_id=company_id, code=code, name=name, fabric_type_id=fabric_type_id,
        customer_id=customer_id, shade_id=shade_id, finished_quality=finished_quality,
        uom_id=uom_id, created_by=created_by,
    )
    db.add(bom)
    db.flush()
    # first draft version
    v = FabricBomVersion(bom_id=bom.id, version_no=1, status=BomStatus.DRAFT, created_by=created_by)
    db.add(v)
    db.flush()
    return bom


def _next_version_no(db: Session, bom_id: uuid.UUID) -> int:
    nos = db.execute(
        select(FabricBomVersion.version_no).where(FabricBomVersion.bom_id == bom_id)
    ).scalars().all()
    return (max(nos) + 1) if nos else 1


def add_version(
    db: Session,
    *,
    bom_id: uuid.UUID,
    clone_from_version_id: uuid.UUID | None = None,
    revision_notes: str | None = None,
    created_by: uuid.UUID | None = None,
) -> FabricBomVersion:
    """Add a new DRAFT version, optionally cloning an existing version's content."""
    bom = db.get(FabricBom, bom_id)
    if bom is None:
        raise BomError("BOM not found")
    v = FabricBomVersion(
        bom_id=bom_id, version_no=_next_version_no(db, bom_id), status=BomStatus.DRAFT,
        revision_notes=revision_notes, created_by=created_by,
    )
    if clone_from_version_id is not None:
        src = db.get(FabricBomVersion, clone_from_version_id)
        if src is None or src.bom_id != bom_id:
            raise BomError("clone source version does not belong to this BOM")
        for f in ("construction", "composition", "yarn_count", "gsm", "width_cm", "gauge",
                  "diameter_inch", "stitch_length_mm", "finished_gsm", "finished_width_cm",
                  "dye_recipe_version_id", "std_knitting_cost", "std_dyeing_cost",
                  "std_finishing_cost", "std_labour_cost", "std_overhead_cost"):
            setattr(v, f, getattr(src, f))
        v.supersedes_id = src.id
    db.add(v)
    db.flush()
    if clone_from_version_id is not None:
        for y in src.yarns:
            db.add(FabricBomYarn(
                version_id=v.id, yarn_id=y.yarn_id, sequence=y.sequence,
                blend_percent=y.blend_percent, feeder_allocation=y.feeder_allocation,
                std_consumption_per_kg=y.std_consumption_per_kg, wastage_percent=y.wastage_percent,
                process_loss_percent=y.process_loss_percent, std_unit_cost=y.std_unit_cost,
            ))
        if src.knitting is not None:
            k = src.knitting
            db.add(FabricBomKnitting(
                version_id=v.id, machine_type=k.machine_type, machine_id=k.machine_id,
                gauge=k.gauge, diameter_inch=k.diameter_inch, feeders=k.feeders,
                stitch_length_mm=k.stitch_length_mm, rpm=k.rpm, efficiency_percent=k.efficiency_percent,
                expected_output_kg_per_shift=k.expected_output_kg_per_shift, settings=k.settings,
            ))
        for s in src.finishing_steps:
            db.add(FabricBomFinishing(
                version_id=v.id, sequence=s.sequence, process_type=s.process_type,
                machine_type=s.machine_type, target_gsm=s.target_gsm,
                target_width_cm=s.target_width_cm, params=s.params, std_cost_per_kg=s.std_cost_per_kg,
            ))
        db.flush()
    return v


def _editable(v: FabricBomVersion) -> None:
    if v.status != BomStatus.DRAFT:
        raise BomError(f"version {v.version_no} is {v.status.value}; only DRAFT versions are editable")


def set_spec(db: Session, version_id: uuid.UUID, **fields) -> FabricBomVersion:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise BomError("version not found")
    _editable(v)
    allowed = {"construction", "composition", "yarn_count", "gsm", "width_cm", "gauge",
               "diameter_inch", "stitch_length_mm", "finished_gsm", "finished_width_cm",
               "dye_recipe_version_id", "std_knitting_cost", "std_dyeing_cost",
               "std_labour_cost", "std_overhead_cost", "revision_notes"}
    for k, val in fields.items():
        if k in allowed:
            setattr(v, k, val)
    db.flush()
    return v


def set_yarns(db: Session, version_id: uuid.UUID, rows: list[dict]) -> list[FabricBomYarn]:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise BomError("version not found")
    _editable(v)
    for y in list(v.yarns):
        db.delete(y)
    db.flush()
    out = []
    for i, r in enumerate(rows, start=1):
        if r.get("yarn_id") is None:
            raise BomError("each yarn row needs a yarn_id")
        y = FabricBomYarn(
            version_id=version_id, yarn_id=r["yarn_id"], sequence=r.get("sequence", i),
            blend_percent=r.get("blend_percent", 0), feeder_allocation=r.get("feeder_allocation"),
            std_consumption_per_kg=r.get("std_consumption_per_kg", 0),
            wastage_percent=r.get("wastage_percent", 0),
            process_loss_percent=r.get("process_loss_percent", 0),
            std_unit_cost=r.get("std_unit_cost", 0),
        )
        db.add(y)
        out.append(y)
    db.flush()
    db.expire(v, ["yarns"])   # keep the cached relationship in sync with the rows
    return out


def set_knitting(db: Session, version_id: uuid.UUID, **fields) -> FabricBomKnitting:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise BomError("version not found")
    _editable(v)
    k = v.knitting
    if k is None:
        k = FabricBomKnitting(version_id=version_id)
        db.add(k)
    for f in ("machine_type", "machine_id", "gauge", "diameter_inch", "feeders",
              "stitch_length_mm", "rpm", "efficiency_percent", "expected_output_kg_per_shift", "settings"):
        if f in fields:
            setattr(k, f, fields[f])
    db.flush()
    db.expire(v, ["knitting"])
    return k


def set_finishing(db: Session, version_id: uuid.UUID, rows: list[dict]) -> list[FabricBomFinishing]:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise BomError("version not found")
    _editable(v)
    for s in list(v.finishing_steps):
        db.delete(s)
    db.flush()
    out = []
    for i, r in enumerate(rows, start=1):
        if not r.get("process_type"):
            raise BomError("each finishing row needs a process_type")
        s = FabricBomFinishing(
            version_id=version_id, sequence=r.get("sequence", i), process_type=r["process_type"],
            machine_type=r.get("machine_type"), target_gsm=r.get("target_gsm"),
            target_width_cm=r.get("target_width_cm"), params=r.get("params"),
            std_cost_per_kg=r.get("std_cost_per_kg", 0),
        )
        db.add(s)
        out.append(s)
    db.flush()
    db.expire(v, ["finishing_steps"])
    return out


# --- standard cost roll-up --------------------------------------------------

def _dye_chem_cost_per_kg(db: Session, recipe_version_id: uuid.UUID | None) -> Decimal:
    if recipe_version_id is None:
        return MZERO
    rv = db.get(RecipeVersion, recipe_version_id)
    if rv is None:
        return MZERO
    total = MZERO
    mats = db.execute(
        select(RecipeMaterial).where(RecipeMaterial.version_id == recipe_version_id)
    ).scalars().all()
    for m in mats:
        if m.percent_owf is not None:
            per_kg = _d(m.percent_owf) / Decimal("100")
        elif m.grams_per_kg is not None:
            per_kg = _d(m.grams_per_kg) / Decimal("1000")
        else:
            per_kg = MZERO
        chem = db.get(Chemical, m.chemical_id)
        total += per_kg * (_d(chem.standard_cost) if chem else MZERO)
    return quantize(total, MONEY_SCALE)


def compute_cost_rollup(db: Session, version: FabricBomVersion) -> dict:
    """Pure-ish roll-up of the standard cost per kg for a version.

    Returns the component costs and the total; does not mutate the version.
    """
    yarn = MZERO
    for y in version.yarns:
        gross = (_d(y.std_consumption_per_kg)
                 * (Decimal("1") + _d(y.wastage_percent) / Decimal("100"))
                 * (Decimal("1") + _d(y.process_loss_percent) / Decimal("100")))
        yarn += gross * _d(y.std_unit_cost)
    yarn = quantize(yarn, MONEY_SCALE)
    dye_chem = _dye_chem_cost_per_kg(db, version.dye_recipe_version_id)
    finishing = quantize(sum((_d(s.std_cost_per_kg) for s in version.finishing_steps), MZERO), MONEY_SCALE)
    knitting = _d(version.std_knitting_cost)
    dyeing = _d(version.std_dyeing_cost)
    labour = _d(version.std_labour_cost)
    overhead = _d(version.std_overhead_cost)
    total = quantize(yarn + dye_chem + knitting + dyeing + finishing + labour + overhead, MONEY_SCALE)
    return {
        "std_yarn_cost": yarn,
        "std_dye_chem_cost": dye_chem,
        "std_knitting_cost": quantize(knitting, MONEY_SCALE),
        "std_dyeing_cost": quantize(dyeing, MONEY_SCALE),
        "std_finishing_cost": finishing,
        "std_labour_cost": quantize(labour, MONEY_SCALE),
        "std_overhead_cost": quantize(overhead, MONEY_SCALE),
        "std_total_cost_per_kg": total,
    }


def _snapshot_cost(db: Session, version: FabricBomVersion) -> None:
    roll = compute_cost_rollup(db, version)
    version.std_yarn_cost = roll["std_yarn_cost"]
    version.std_dye_chem_cost = roll["std_dye_chem_cost"]
    version.std_finishing_cost = roll["std_finishing_cost"]
    version.std_total_cost_per_kg = roll["std_total_cost_per_kg"]
    version.cost_rolled_at = datetime.now(timezone.utc)


# --- approval / lifecycle ---------------------------------------------------

def approve_version(
    db: Session,
    *,
    version_id: uuid.UUID,
    approved_by: uuid.UUID | None,
    effective_from: date | None = None,
) -> FabricBomVersion:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise BomError("version not found")
    if v.status != BomStatus.DRAFT:
        raise BomError(f"version {v.version_no} is {v.status.value}; only DRAFT can be approved")
    if not v.yarns:
        raise BomError("cannot approve a BOM version with no yarn components")
    # a linked dyeing recipe, if any, must itself be approved
    if v.dye_recipe_version_id is not None:
        rv = db.get(RecipeVersion, v.dye_recipe_version_id)
        if rv is None:
            raise BomError("linked dyeing recipe version not found")
        if rv.status != "APPROVED":
            raise BomError("linked dyeing recipe version must be APPROVED first")

    eff = effective_from or _today()
    # supersede the currently-effective approved version of this BOM
    current = effective_version(db, v.bom_id, eff)
    if current is not None and current.id != v.id:
        current.status = BomStatus.SUPERSEDED
        current.effective_to = eff - timedelta(days=1)

    _snapshot_cost(db, v)
    v.status = BomStatus.APPROVED
    v.approved_by = approved_by
    v.approved_at = datetime.now(timezone.utc)
    v.effective_from = eff
    v.effective_to = None
    db.flush()
    return v


def obsolete_version(db: Session, version_id: uuid.UUID) -> FabricBomVersion:
    v = db.get(FabricBomVersion, version_id)
    if v is None:
        raise BomError("version not found")
    if v.status == BomStatus.DRAFT:
        raise BomError("a DRAFT version cannot be obsoleted; delete it or approve it")
    v.status = BomStatus.OBSOLETE
    if v.effective_to is None:
        v.effective_to = _today()
    db.flush()
    return v


def effective_version(db: Session, bom_id: uuid.UUID, on_date: date | None = None) -> FabricBomVersion | None:
    """The version whose effective window covers ``on_date`` (defaults to today).

    Considers every version that was once approved (APPROVED, and the historical
    SUPERSEDED / OBSOLETE windows) so a past date resolves to what was effective
    then — the basis for keeping old orders on their originally-approved version.
    Callers that require a *currently*-approved version (e.g. creating a new
    order) additionally check ``status == APPROVED``.
    """
    on_date = on_date or _today()
    candidates = db.execute(
        select(FabricBomVersion).where(
            FabricBomVersion.bom_id == bom_id,
            FabricBomVersion.status.in_(
                [BomStatus.APPROVED, BomStatus.SUPERSEDED, BomStatus.OBSOLETE]
            ),
            FabricBomVersion.effective_from.is_not(None),
        )
    ).scalars().all()
    best = None
    for v in candidates:
        if v.effective_from <= on_date and (v.effective_to is None or v.effective_to >= on_date):
            if best is None or v.effective_from >= best.effective_from:
                best = v
    return best
