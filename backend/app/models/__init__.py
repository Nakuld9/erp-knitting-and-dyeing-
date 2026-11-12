"""Model registry.

Importing this package registers every ORM model on ``Base.metadata`` so that
``Base.metadata.create_all`` and Alembic autogeneration see the full schema.
"""
from app.models.base import Base  # noqa: F401
from app.models.core import (  # noqa: F401
    Branch,
    Company,
    Department,
    Employee,
    Permission,
    Role,
    User,
    role_permissions,
    user_roles,
)
from app.models.masters import (  # noqa: F401
    BankAccount,
    Chemical,
    ChemicalCategory,
    ChemicalLot,
    CostCentre,
    Customer,
    FabricType,
    Machine,
    PaymentTerm,
    Recipe,
    RecipeMaterial,
    RecipeVersion,
    ShadeCode,
    Supplier,
    TaxCode,
    UnitOfMeasure,
    Warehouse,
    YarnMaster,
    YarnLot,
)
from app.models.accounting import (  # noqa: F401
    AccountingPeriod,
    AccountType,
    ChartOfAccount,
    JournalLine,
    PostingRule,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.models.audit import AuditLog, DocumentSequence  # noqa: F401
from app.models.inventory import (  # noqa: F401
    InventoryTransaction,
    ItemType,
    MoveDirection,
    StockCount,
    StockLayer,
    StockTransfer,
)
from app.models.commercial import (  # noqa: F401
    DocStatus,
    GoodsReceiptNote,
    GRNLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SalesInvoice,
    SalesInvoiceLine,
    SalesOrder,
    SalesOrderLine,
)
from app.models.manufacturing import (  # noqa: F401
    BatchStatus,
    DyeBatch,
    DyeBath,
    FabricRoll,
    KnittingProduction,
    LabDip,
    RollKind,
    dye_batch_rolls,
)
from app.models.dispensing import (  # noqa: F401
    DispensingCorrection,
    DispensingDevice,
    DispensingTransaction,
    DosingStatus,
    IntegrationEvent,
)
from app.models.assets import (  # noqa: F401
    AssetStatus,
    DepMethod,
    DepreciationEntry,
    DepreciationRun,
    FixedAsset,
)
from app.models.payroll import (  # noqa: F401
    Payslip,
    PayrollRun,
    PayrollStatus,
    SalaryStructure,
)
from app.models.overhead import (  # noqa: F401
    AllocationBasis,
    OverheadAllocationLine,
    OverheadAllocationRun,
    OverheadPool,
)
from app.models.approvals import (  # noqa: F401
    ApprovalAction,
    ApprovalEvent,
    ApprovalRequest,
    ApprovalRule,
    ApprovalStatus,
    PaymentApproval,
    PaymentStatus,
    PendingVoucher,
    PendingVoucherStatus,
)
from app.models.notifications import Attachment, Notification  # noqa: F401
from app.models.einvoice import EInvoice, EInvoiceStatus  # noqa: F401
from app.models.stores import StoreCategory, StoreItem  # noqa: F401
from app.models.jobwork import (  # noqa: F401
    JobWork,
    JobWorkDirection,
    JobWorkProcess,
    JobWorkStatus,
)
from app.models.ewaybill import EWayBill, EWayBillStatus  # noqa: F401
from app.models.banking import BankReconciliation, BankStatementLine  # noqa: F401
from app.models.quality import (  # noqa: F401
    ComplaintStatus,
    CustomerComplaint,
    FinishingProcess,
    FinishingTransaction,
    InspectionResult,
    QualityInspection,
)
from app.models.hardware import (  # noqa: F401
    KnittingMachineLog,
    MachineStatus,
    ScaleReading,
    WeighingScale,
)
from app.models.tenancy import (  # noqa: F401
    CompanyDatabase,
    ConsolidationElimination,
    GSTRegistration,
    IntercompanyAccountMapping,
    IntercompanyTxn,
    IntercompanyTxnStatus,
    Organization,
    UserCompanyMembership,
    UserFactoryMembership,
)
from app.models.transfers import FactoryStockTransfer, TransferStatus  # noqa: F401

# Registering the guard activates the immutable-ledger Session listeners.
from app.core import ledger_guard  # noqa: E402,F401

__all__ = ["Base"]
