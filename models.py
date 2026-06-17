"""
Data model for a generic TPA (Third-Party Administrator) billing platform.

A TPA administers benefit plans for employer clients under an Administrative
Services Agreement (the **Contract**) and bills monthly fees — typically PEPM
(per-employee-per-month), per-participant, per-transaction, or flat.

Flow:  Contract (fee schedule)  ->  Eligibility/Activity (the month's actuals)
       ->  Invoice  ->  Close (reconcile every line vs the contract rate and the
       actual eligibility, so the client is never over- or under-billed).
"""
from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# fee bases
PEPM = "PEPM"            # per employee per month
PPPM = "PPPM"            # per participant per month
PER_TXN = "PER_TXN"      # per transaction (e.g. claims processed)
FLAT = "FLAT"            # flat monthly fee
BASIS_LABEL = {PEPM: "PEPM", PPPM: "Per participant",
               PER_TXN: "Per transaction", FLAT: "Flat / month"}


class Client(db.Model):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    industry = db.Column(db.String)
    ein = db.Column(db.String)
    broker = db.Column(db.String)

    contracts = db.relationship("Contract", backref="client", lazy=True)
    periods = db.relationship("ActivityPeriod", backref="client", lazy=True)
    invoices = db.relationship("Invoice", backref="client", lazy=True)


class Contract(db.Model):
    """Administrative Services Agreement + its fee schedule."""
    __tablename__ = "contracts"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    name = db.Column(db.String, nullable=False)
    number = db.Column(db.String)                 # ASA-2026-0142
    effective_start = db.Column(db.String)        # "2026-01-01"
    effective_end = db.Column(db.String)          # "2026-12-31"
    billing_frequency = db.Column(db.String, default="Monthly")
    # --- terms (the "meat") ---
    funding_type = db.Column(db.String)           # Self-funded / Level-funded / Fully-insured
    plan_year = db.Column(db.String)              # "2026 calendar year"
    renewal = db.Column(db.String)                # "Auto-renew · 12-month term · 60-day notice"
    payment_terms = db.Column(db.String, default="Net 15 · ACH")
    late_fee = db.Column(db.String)               # "1.5% / mo on balances > 30 days"
    runout = db.Column(db.String)                 # "90-day claims run-out"
    sla = db.Column(db.String)                    # service-level commitment
    covered_lives = db.Column(db.Integer, default=0)
    primary_contact = db.Column(db.String)
    contact_email = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lines = db.relationship("ContractLine", backref="contract", lazy=True,
                            cascade="all, delete-orphan")
    invoices = db.relationship("Invoice", backref="contract", lazy=True)

    def active_for(self, month):
        """month = 'YYYY-MM' -> is the contract in force that month?"""
        try:
            s = self.effective_start[:7] if self.effective_start else "0000-00"
            e = self.effective_end[:7] if self.effective_end else "9999-99"
            return s <= month <= e
        except Exception:
            return True

    @property
    def status(self):
        today = date.today().isoformat()
        if self.effective_start and today < self.effective_start:
            return "Pending"
        if self.effective_end and today > self.effective_end:
            return "Expired"
        return "Active"

    @property
    def monthly_estimate(self):
        return round(sum(l.estimated_amount for l in self.lines if l.active), 2)


class ContractLine(db.Model):
    """One fee in the schedule."""
    __tablename__ = "contract_lines"
    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"), nullable=False)
    service = db.Column(db.String, nullable=False)
    basis = db.Column(db.String, default=PEPM)
    rate = db.Column(db.Float, default=0.0)
    metric_key = db.Column(db.String)             # links to ActivityMetric.metric_key
    baseline_count = db.Column(db.Integer, default=0)   # roster count on file
    active = db.Column(db.Boolean, default=True)        # discontinued services = False

    @property
    def basis_label(self):
        return BASIS_LABEL.get(self.basis, self.basis)

    @property
    def estimated_amount(self):
        if self.basis == FLAT:
            return round(self.rate, 2)
        return round(self.rate * (self.baseline_count or 0), 2)


class ActivityPeriod(db.Model):
    """A month's eligibility / activity snapshot for a client."""
    __tablename__ = "activity_periods"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    month = db.Column(db.String)                  # "2026-03"
    label = db.Column(db.String)
    source = db.Column(db.String, default="Eligibility file")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    metrics = db.relationship("ActivityMetric", backref="period", lazy=True,
                              cascade="all, delete-orphan")

    def count(self, metric_key):
        for m in self.metrics:
            if m.metric_key == metric_key:
                return m.count
        return None


class ActivityMetric(db.Model):
    __tablename__ = "activity_metrics"
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("activity_periods.id"), nullable=False)
    metric_key = db.Column(db.String)
    label = db.Column(db.String)
    count = db.Column(db.Integer, default=0)


class Invoice(db.Model):
    __tablename__ = "invoices"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey("contracts.id"))
    period_id = db.Column(db.Integer, db.ForeignKey("activity_periods.id"))
    number = db.Column(db.String)                 # INV-2026-03-0142
    month = db.Column(db.String)
    terms = db.Column(db.String, default="Net 15 · ACH")
    status = db.Column(db.String, default="Draft")    # Draft, Issued, Closed, Hold
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    lines = db.relationship("InvoiceLine", backref="invoice", lazy=True,
                            cascade="all, delete-orphan")
    period = db.relationship("ActivityPeriod")

    @property
    def billed_total(self):
        return round(sum(l.amount for l in self.lines if l.charged), 2)

    @property
    def held_total(self):
        return round(sum(l.amount for l in self.lines if not l.charged), 2)

    @property
    def gross_total(self):
        return round(sum(l.amount for l in self.lines), 2)

    def counts(self):
        c = {"green": 0, "yellow": 0, "red": 0}
        for l in self.lines:
            c[l.recon_status] = c.get(l.recon_status, 0) + 1
        return c


class InvoiceLine(db.Model):
    __tablename__ = "invoice_lines"
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    service = db.Column(db.String)
    basis = db.Column(db.String)
    contract_rate = db.Column(db.Float, default=0.0)
    billed_count = db.Column(db.Integer, default=0)     # what we billed (roster)
    actual_count = db.Column(db.Integer)                # eligibility this period
    amount = db.Column(db.Float, default=0.0)
    recon_status = db.Column(db.String, default="green")
    recon_reason = db.Column(db.String, default="")
    charged = db.Column(db.Boolean, default=True)

    @property
    def basis_label(self):
        return BASIS_LABEL.get(self.basis, self.basis)
