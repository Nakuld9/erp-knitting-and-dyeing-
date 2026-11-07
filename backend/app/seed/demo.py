"""Sample manufacturing master data + opening stock for demos and Stage 2-4 tests.

Creates a tax code, warehouses, a supplier/customer, chemicals & dyes with
priced lots and opening inventory, a yarn with opening stock, machines, a shade,
a fabric type, and an APPROVED recipe scaled to the brief's example
(Reactive Blue 1.4% OWF -> 3.5 kg at 250 kg fabric).

Returns a dict of handles keyed for convenient use in tests.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.accounting import posting_engine as pe

from app.models.accounting import ChartOfAccount
from app.models.hardware import WeighingScale
from app.models.inventory import ItemType
from app.models.masters import (
    BankAccount,
    Chemical,
    ChemicalLot,
    FabricType,
    Machine,
    MachineType,
    Recipe,
    RecipeMaterial,
    RecipeVersion,
    ShadeCode,
    Supplier,
    TaxCode,
    Customer,
    Warehouse,
    YarnMaster,
    YarnLot,
)
from app.services import inventory
from sqlalchemy import select

# code, name, is_dye, dye_class, unit_cost per kg
CHEMICALS = [
    ("RB-01", "Reactive Blue", True, "Reactive", "1200"),
    ("RBK-01", "Reactive Black", True, "Reactive", "1500"),
    ("SALT", "Common Salt", False, None, "10"),
    ("SODA", "Soda Ash", False, None, "25"),
]
# recipe materials: chemical code, percent OWF, sequence
RECIPE_MATERIALS = [
    ("RB-01", "1.4", 1),
    ("RBK-01", "0.48", 2),
    ("SALT", "20", 3),
    ("SODA", "6", 4),
]


def seed_manufacturing_demo(db: Session, company_id: uuid.UUID, *, opening_qty: str = "500",
                            opening_fy_year: int = 2026) -> dict:
    tax = TaxCode(company_id=company_id, code="GST18", name="GST 18%",
                  cgst_rate=Decimal("9"), sgst_rate=Decimal("9"), igst_rate=Decimal("18"), hsn_sac="5208")
    db.add(tax)

    wh_chem = Warehouse(company_id=company_id, code="WH-CHEM", name="Chemical Store", kind="STORE")
    wh_greige = Warehouse(company_id=company_id, code="WH-GREIGE", name="Greige Store", kind="GREIGE")
    wh_fg = Warehouse(company_id=company_id, code="WH-FG", name="Finished Goods", kind="FG")
    db.add_all([wh_chem, wh_greige, wh_fg])

    supplier = Supplier(company_id=company_id, code="SUP-CHEM", name="Acme Chemicals",
                        state_code="03", is_chemical_supplier=True, gstin="03ZZZZZ0000Z1Z5")
    customer = Customer(company_id=company_id, code="CUST-01", name="Fashion House",
                        state_code="03", gstin="03YYYYY0000Y1Z5")
    db.add_all([supplier, customer])
    db.flush()

    bank_coa = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == "1020")
    ).scalar_one_or_none()
    bank = BankAccount(company_id=company_id, coa_account_id=bank_coa.id if bank_coa else None,
                       name="HDFC Current A/c", bank_name="HDFC Bank",
                       account_number="000111222333", ifsc="HDFC0000123")
    db.add(bank)
    db.flush()

    opening_values = {"1100": Decimal("0"), "1090": Decimal("0"), "1060": Decimal("0")}

    chemicals: dict[str, Chemical] = {}
    lots: dict[str, ChemicalLot] = {}
    for code, name, is_dye, dye_class, cost in CHEMICALS:
        chem = Chemical(company_id=company_id, code=code, name=name, is_dye=is_dye,
                        dye_class=dye_class, tax_code_id=tax.id, standard_cost=Decimal(cost))
        db.add(chem)
        db.flush()
        chemicals[code] = chem
        lot = ChemicalLot(company_id=company_id, chemical_id=chem.id, supplier_id=supplier.id,
                          internal_lot_no=f"LOT-{code}", supplier_lot_no=f"S-{code}",
                          warehouse_id=wh_chem.id, unit_cost=Decimal(cost))
        db.add(lot)
        db.flush()
        lots[code] = lot
        inventory.receive(
            db, company_id=company_id, item_type=ItemType.CHEMICAL, item_id=chem.id,
            qty=opening_qty, unit_cost=Decimal(cost), lot_id=lot.id, warehouse_id=wh_chem.id,
            event_type="OPENING", source_module="seed", source_id="opening",
        )
        opening_values["1090" if is_dye else "1100"] += Decimal(opening_qty) * Decimal(cost)

    yarn = YarnMaster(company_id=company_id, code="Y-30S", name="30s Combed Cotton",
                      yarn_type="Cotton", count="30s")
    db.add(yarn)
    db.flush()
    yarn_lot = YarnLot(company_id=company_id, yarn_id=yarn.id, supplier_id=supplier.id,
                       internal_lot_no="YLOT-Y-30S", warehouse_id=wh_greige.id, unit_cost=Decimal("240"))
    db.add(yarn_lot)
    db.flush()
    inventory.receive(
        db, company_id=company_id, item_type=ItemType.YARN, item_id=yarn.id,
        qty="2000", unit_cost=Decimal("240"), lot_id=yarn_lot.id, event_type="OPENING",
        source_module="seed", source_id="opening",
    )
    opening_values["1060"] += Decimal("2000") * Decimal("240")

    knit = Machine(company_id=company_id, machine_type=MachineType.KNITTING.value, code="K-01",
                   name="Knitting Machine 1", capacity_kg=Decimal("300"))
    jet = Machine(company_id=company_id, machine_type=MachineType.DYEING.value, code="JET-04",
                  name="Soft Flow Jet 04", capacity_kg=Decimal("300"))
    db.add_all([knit, jet])

    scale = WeighingScale(company_id=company_id, code="SCALE-01", name="Greige Weighing Scale",
                          location="Greige Store", capacity_kg=Decimal("500"))
    db.add(scale)
    shade = ShadeCode(company_id=company_id, code="SH-NAVY", name="Navy Blue", customer_id=customer.id)
    fabric = FabricType(company_id=company_id, code="SJ-160", name="Single Jersey 160",
                        construction="Single Jersey", composition="100% Cotton",
                        gsm=Decimal("160"), width_cm=Decimal("180"))
    db.add_all([shade, fabric])
    db.flush()

    recipe = Recipe(company_id=company_id, code="R-NAVY", shade_id=shade.id,
                    fabric_type_id=fabric.id, dye_class="Reactive")
    db.add(recipe)
    db.flush()
    version = RecipeVersion(recipe_id=recipe.id, version_no=1, liquor_ratio=Decimal("8"),
                            temperature_c=Decimal("60"), ph=Decimal("11"), holding_time_min=45,
                            status="APPROVED")
    db.add(version)
    db.flush()
    for code, pct, seq in RECIPE_MATERIALS:
        db.add(RecipeMaterial(version_id=version.id, chemical_id=chemicals[code].id,
                              sequence=seq, percent_owf=Decimal(pct),
                              dosing_group="dye" if chemicals[code].is_dye else "aux"))
    db.flush()

    # Post an opening-balance voucher so the inventory sub-ledger ties to the GL
    # control accounts (Dr inventory accounts / Cr owner's capital).
    opening_lines = [pe.PostingLine(code, debit=val)
                     for code, val in opening_values.items() if val > 0]
    if opening_lines:
        total = sum((ln.debit for ln in opening_lines), Decimal("0"))
        opening_lines.append(pe.PostingLine("3010", credit=total, narration="Opening stock capital"))
        pe.post_voucher(db, pe.PostingRequest(
            company_id=company_id, voucher_type_code="OB", voucher_date=date(opening_fy_year, 4, 1),
            posting_reference="OB:opening-stock", narration="Opening stock",
            source_module="seed", lines=opening_lines,
        ))
    db.flush()

    return {
        "company_id": company_id,
        "tax_id": tax.id,
        "warehouses": {"chem": wh_chem.id, "greige": wh_greige.id, "fg": wh_fg.id},
        "supplier_id": supplier.id,
        "customer_id": customer.id,
        "bank_account_id": bank.id,
        "chemicals": {c: chemicals[c].id for c in chemicals},
        "lots": {c: lots[c].id for c in lots},
        "lot_refs": {c: lots[c].internal_lot_no for c in lots},
        "yarn_id": yarn.id,
        "yarn_lot_id": yarn_lot.id,
        "machines": {"knit": knit.id, "jet": jet.id},
        "shade_id": shade.id,
        "fabric_type_id": fabric.id,
        "recipe_id": recipe.id,
        "recipe_version_id": version.id,
        "scale_id": scale.id,
    }
