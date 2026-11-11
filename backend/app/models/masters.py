"""Master-data models (Module 1).

Reference/configuration data used across manufacturing, inventory, commercial
and accounting modules. Transactional manufacturing/dispensing tables (dye
batches, baths, dispensing transactions, GRNs, invoices) are specified in
docs/ER_DIAGRAM.md and built in later stages; this file establishes the master
foundation every module hangs off.

Design note: knitting/dyeing/finishing machines are modelled as one ``machines``
table discriminated by ``machine_type`` rather than three near-identical tables.
This keeps scheduling, downtime and cost-centre linkage uniform; the logical
"knitting_machines / dyeing_machines / finishing_machines" views from the spec
are filters on ``machine_type``.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.types import Money, Quantity
from app.models.base import AuditMixin, Base


class MachineType(str, enum.Enum):
    KNITTING = "KNITTING"
    DYEING = "DYEING"
    FINISHING = "FINISHING"


class CostCentreType(str, enum.Enum):
    COST = "COST"       # a department / overhead cost centre
    PROFIT = "PROFIT"   # a profit centre
    WORK = "WORK"       # a production work centre (a machine), under a department


# --- units / tax / costing --------------------------------------------------

class UnitOfMeasure(AuditMixin, Base):
    __tablename__ = "units_of_measure"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # KG, G, L, ML, M, ROLL, PCS
    name: Mapped[str] = mapped_column(String(50))
    dimension: Mapped[str] = mapped_column(String(20))  # MASS / VOLUME / LENGTH / COUNT
    base_factor: Mapped[object] = mapped_column(Quantity, default=1)  # conversion to base unit of dimension


class TaxCode(AuditMixin, Base):
    """Configurable GST tax code. Rates are NEVER hardcoded in the engine."""

    __tablename__ = "tax_codes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)   # e.g. GST18
    name: Mapped[str] = mapped_column(String(100))
    cgst_rate: Mapped[object] = mapped_column(Quantity, default=0)   # percent
    sgst_rate: Mapped[object] = mapped_column(Quantity, default=0)
    igst_rate: Mapped[object] = mapped_column(Quantity, default=0)
    is_reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)
    hsn_sac: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CostCentre(AuditMixin, Base):
    __tablename__ = "cost_centres"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(100))
    centre_type: Mapped[CostCentreType] = mapped_column(String(10), default=CostCentreType.COST)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PaymentTerm(AuditMixin, Base):
    __tablename__ = "payment_terms"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(100))
    days: Mapped[int] = mapped_column(Integer, default=0)


class BankAccount(AuditMixin, Base):
    __tablename__ = "bank_accounts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    coa_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chart_of_accounts.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ifsc: Mapped[str | None] = mapped_column(String(15), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- commercial parties -----------------------------------------------------

class Customer(AuditMixin, Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    credit_limit: Mapped[object] = mapped_column(Money, default=0)
    payment_term_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_terms.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(AuditMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_chemical_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_term_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_terms.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- inventory / facilities -------------------------------------------------

class Warehouse(AuditMixin, Base):
    __tablename__ = "warehouses"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), default="STORE")  # STORE / GREIGE / FG / DISPENSING_TANK
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- textile technical masters ---------------------------------------------

class YarnMaster(AuditMixin, Base):
    __tablename__ = "yarn_masters"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    yarn_type: Mapped[str | None] = mapped_column(String(60), nullable=True)   # Cotton / Polyester / blend
    count: Mapped[str | None] = mapped_column(String(30), nullable=True)       # e.g. 30s, 2/40s
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FabricType(AuditMixin, Base):
    __tablename__ = "fabric_types"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    construction: Mapped[str | None] = mapped_column(String(120), nullable=True)   # Single Jersey, Rib, Interlock
    composition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gsm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    width_cm: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Machine(AuditMixin, Base):
    __tablename__ = "machines"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    # factory/branch owner (additive; nullable — backfill is approval-gated)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"), nullable=True, index=True)
    machine_type: Mapped[MachineType] = mapped_column(String(12), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    capacity_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    cost_centre_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cost_centres.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- chemical / dye masters -------------------------------------------------

class ChemicalCategory(AuditMixin, Base):
    __tablename__ = "chemical_categories"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(100))


class Chemical(AuditMixin, Base):
    """Chemicals and dyes share one master, discriminated by ``is_dye``."""

    __tablename__ = "chemicals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)          # material code used by Color Service
    name: Mapped[str] = mapped_column(String(160))
    is_dye: Mapped[bool] = mapped_column(Boolean, default=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chemical_categories.id"), nullable=True)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("units_of_measure.id"), nullable=True)
    tax_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tax_codes.id"), nullable=True)
    dye_class: Mapped[str | None] = mapped_column(String(60), nullable=True)   # Reactive, Disperse ...
    standard_cost: Mapped[object] = mapped_column(Money, default=0)
    reorder_level: Mapped[object] = mapped_column(Quantity, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChemicalLot(AuditMixin, Base):
    __tablename__ = "chemical_lots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    chemical_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chemicals.id"), index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    internal_lot_no: Mapped[str] = mapped_column(String(40), index=True)
    supplier_lot_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    mfg_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)


class YarnLot(AuditMixin, Base):
    __tablename__ = "yarn_lots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    yarn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("yarn_masters.id"), index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    internal_lot_no: Mapped[str] = mapped_column(String(40), index=True)
    supplier_lot_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    received_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unit_cost: Mapped[object] = mapped_column(Money, default=0)


class ShadeCode(AuditMixin, Base):
    __tablename__ = "shade_codes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    depth: Mapped[str | None] = mapped_column(String(30), nullable=True)  # light/medium/dark
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# --- recipes (master; production use governed by approval) ------------------

class Recipe(AuditMixin, Base):
    __tablename__ = "recipes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    shade_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shade_codes.id"), nullable=True)
    fabric_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fabric_types.id"), nullable=True)
    dye_class: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    versions: Mapped[list["RecipeVersion"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeVersion(AuditMixin, Base):
    __tablename__ = "recipe_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    liquor_ratio: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    temperature_c: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    ph: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    holding_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Only APPROVED versions may be dispatched to production / Color Service.
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT/APPROVED/REVISED
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    recipe: Mapped[Recipe] = relationship(back_populates="versions")
    materials: Mapped[list["RecipeMaterial"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="RecipeMaterial.sequence"
    )


class RecipeMaterial(AuditMixin, Base):
    __tablename__ = "recipe_materials"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recipe_versions.id", ondelete="CASCADE"), index=True
    )
    chemical_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chemicals.id"))
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    # Percentage on weight of fabric (OWF) OR grams-per-kg; scaling uses whichever is set.
    percent_owf: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    grams_per_kg: Mapped[object | None] = mapped_column(Quantity, nullable=True)
    dosing_group: Mapped[str | None] = mapped_column(String(40), nullable=True)

    version: Mapped[RecipeVersion] = relationship(back_populates="materials")
