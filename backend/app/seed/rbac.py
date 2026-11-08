"""Roles and permissions (Module 21 access control)."""
from __future__ import annotations

# (code, module, description)
PERMISSIONS: list[tuple[str, str, str]] = [
    ("master.read", "masters", "View master data"),
    ("master.write", "masters", "Create/edit master data"),
    ("sales.manage", "sales", "Manage sales orders and invoices"),
    ("purchase.manage", "purchase", "Manage purchase orders and invoices"),
    ("knitting.manage", "knitting", "Record knitting production"),
    ("greige.manage", "greige", "Manage greige fabric rolls"),
    ("dyeing.manage", "dyeing", "Manage dye batches and baths"),
    ("recipe.read", "recipe", "View recipes and lab dips"),
    ("recipe.approve", "recipe", "Approve recipes / lab dips for production"),
    ("dispensing.operate", "dispensing", "Operate Color Service dispensing"),
    ("dispensing.edit", "dispensing", "Edit dispensing records (restricted)"),
    ("finishing.manage", "quality", "Record finishing processes"),
    ("quality.manage", "quality", "Record quality inspections and complaints"),
    ("chemical.store", "inventory", "Manage chemical store inventory"),
    ("inventory.adjust", "inventory", "Approve stock adjustments"),
    ("voucher.create", "accounting", "Create draft vouchers"),
    ("voucher.post", "accounting", "Post vouchers to the ledger"),
    ("voucher.approve", "accounting", "Approve vouchers / payments"),
    ("voucher.reverse", "accounting", "Reverse posted vouchers"),
    ("period.close", "accounting", "Close / reopen accounting periods"),
    ("asset.manage", "accounting", "Manage fixed assets and depreciation"),
    ("payroll.manage", "accounting", "Manage payroll and salary structures"),
    ("bank.reconcile", "banking", "Perform bank reconciliation"),
    ("report.read", "reports", "View reports and dashboards"),
    ("report.financial", "reports", "View financial statements"),
    ("admin.manage", "admin", "Manage users, roles and configuration"),
]

# role_code -> (name, [permission codes]); "*" means all permissions.
ROLES: dict[str, tuple[str, list[str]]] = {
    "OWNER": ("Owner", ["*"]),
    "SUPER_ADMIN": ("Super Administrator", ["*"]),
    "GENERAL_MANAGER": (
        "General Manager",
        ["master.read", "report.read", "report.financial", "sales.manage", "purchase.manage"],
    ),
    "PRODUCTION_MANAGER": (
        "Production Manager",
        ["master.read", "knitting.manage", "greige.manage", "dyeing.manage", "recipe.read",
         "finishing.manage", "quality.manage", "report.read"],
    ),
    "KNITTING_SUPERVISOR": ("Knitting Supervisor", ["master.read", "knitting.manage", "greige.manage"]),
    "DYEING_SUPERVISOR": ("Dyeing Supervisor", ["master.read", "dyeing.manage", "recipe.read", "dispensing.operate"]),
    "LAB_TECHNICIAN": ("Lab Technician", ["master.read", "recipe.read", "recipe.approve", "quality.manage"]),
    "CHEMICAL_STORE_MANAGER": ("Chemical Store Manager", ["master.read", "chemical.store", "report.read"]),
    "DISPENSING_OPERATOR": ("Dispensing Operator", ["dispensing.operate"]),
    "PURCHASE_MANAGER": ("Purchase Manager", ["master.read", "master.write", "purchase.manage"]),
    "ACCOUNTANT": (
        "Accountant",
        ["master.read", "voucher.create", "voucher.post", "bank.reconcile", "report.read",
         "report.financial", "asset.manage", "payroll.manage"],
    ),
    "FINANCE_CONTROLLER": (
        "Finance Controller",
        [
            "master.read", "voucher.create", "voucher.post", "voucher.approve",
            "voucher.reverse", "period.close", "bank.reconcile", "report.read", "report.financial",
            "inventory.adjust", "asset.manage", "payroll.manage",
        ],
    ),
    "AUDITOR": ("Auditor", ["master.read", "report.read", "report.financial"]),
    "MAINTENANCE_ENGINEER": ("Maintenance Engineer", ["master.read"]),
}
