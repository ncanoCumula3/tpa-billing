"""Generate / issue / close / reopen monthly TPA invoices."""
from datetime import datetime

from models import db, Invoice, InvoiceLine, FLAT
from services.reconciler import reconcile


def _number(month, client_id):
    seq = Invoice.query.count() + 142
    return f"INV-{month}-{seq:04d}"


def generate(contract, period):
    """Build (or replace) the draft invoice for a contract + activity period."""
    existing = Invoice.query.filter_by(contract_id=contract.id, period_id=period.id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    month = period.month
    inv = Invoice(client_id=contract.client_id, contract_id=contract.id,
                  period_id=period.id, number=_number(month, contract.client_id),
                  month=month, status="Draft", created_at=datetime.utcnow())
    db.session.add(inv)
    db.session.flush()

    for cl in contract.lines:
        billed = cl.baseline_count or 0
        actual = None if cl.basis == FLAT else period.count(cl.metric_key)
        if cl.basis == FLAT:
            amount = cl.rate
        else:
            amount = round(cl.rate * billed, 2)
        verdict = reconcile(cl, contract, month, billed, actual)
        db.session.add(InvoiceLine(
            invoice_id=inv.id, service=cl.service, basis=cl.basis,
            contract_rate=cl.rate, billed_count=billed, actual_count=actual,
            amount=amount, recon_status=verdict["status"],
            recon_reason=verdict["reason"], charged=verdict["charged"]))

    db.session.commit()
    return inv


def issue(invoice):
    invoice.status = "Issued"
    db.session.commit()
    return invoice


def close(invoice):
    """Charged (green/yellow) lines bill; red lines are held with a reason."""
    counts = invoice.counts()
    if counts["green"] == 0 and counts["yellow"] == 0:
        invoice.status = "Hold"
        invoice.closed_at = None
    else:
        invoice.status = "Closed"
        invoice.closed_at = datetime.utcnow()
    db.session.commit()
    return invoice


def reopen(invoice):
    invoice.status = "Draft"
    invoice.closed_at = None
    db.session.commit()
    return invoice


def hold(invoice, reason=""):
    """Park the invoice for correction — nothing bills until it's reopened."""
    invoice.status = "Hold"
    invoice.closed_at = None
    if reason:
        invoice.terms = (invoice.terms or "") + f" · HOLD: {reason}"
    db.session.commit()
    return invoice


def void(invoice, reason=""):
    """Cancel the invoice — nothing is billed; it stays on file for the record."""
    invoice.status = "Void"
    invoice.closed_at = datetime.utcnow()
    if reason:
        invoice.terms = (invoice.terms or "") + f" · VOID: {reason}"
    db.session.commit()
    return invoice
