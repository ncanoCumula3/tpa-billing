"""
Form + file helpers for the admin features: create/edit a contract and its fee
schedule, and ingest an uploaded eligibility CSV into an ActivityPeriod.
"""
import csv
import io

from models import (db, Client, Contract, ContractLine, ActivityPeriod,
                    ActivityMetric, PEPM)


def _num(v, typ=float):
    try:
        return typ(str(v).replace(",", "").replace("$", "").strip() or 0)
    except (ValueError, TypeError):
        return typ(0)


def find_or_create_client(name, industry="", broker="", ein=""):
    name = (name or "").strip()
    c = (Client.query.filter(db.func.lower(Client.name) == name.lower()).first()
         if name else None)
    if not c:
        c = Client(name=name or "Unnamed client", industry=industry, broker=broker, ein=ein)
        db.session.add(c)
        db.session.flush()
    else:
        if industry:
            c.industry = industry
        if broker:
            c.broker = broker
        if ein:
            c.ein = ein
    return c


def apply_contract_fields(c, f):
    c.name = (f.get("name") or c.name or "").strip()
    c.number = f.get("number")
    c.effective_start = f.get("effective_start")
    c.effective_end = f.get("effective_end")
    c.billing_frequency = f.get("billing_frequency") or "Monthly"
    c.funding_type = f.get("funding_type")
    c.plan_year = f.get("plan_year")
    c.renewal = f.get("renewal")
    c.payment_terms = f.get("payment_terms")
    c.late_fee = f.get("late_fee")
    c.runout = f.get("runout")
    c.sla = f.get("sla")
    c.covered_lives = _num(f.get("covered_lives"), int)
    c.primary_contact = f.get("primary_contact")
    c.contact_email = f.get("contact_email")


def set_contract_lines(c, f):
    """Rebuild the fee schedule from the repeating line_* form arrays."""
    for l in list(c.lines):
        db.session.delete(l)
    db.session.flush()
    svc = f.getlist("line_service")
    basis = f.getlist("line_basis")
    rate = f.getlist("line_rate")
    metric = f.getlist("line_metric")
    base = f.getlist("line_baseline")
    active = f.getlist("line_active")
    for i, s in enumerate(svc):
        if not (s or "").strip():
            continue
        db.session.add(ContractLine(
            contract_id=c.id, service=s.strip(),
            basis=basis[i] if i < len(basis) else PEPM,
            rate=_num(rate[i]) if i < len(rate) else 0.0,
            metric_key=((metric[i].strip() or None) if i < len(metric) else None),
            baseline_count=_num(base[i], int) if i < len(base) else 0,
            active=(active[i] == "1") if i < len(active) else True))


def save_contract(form, contract=None):
    """Create or update a contract (+ its client + fee lines) from a form."""
    if contract is None:
        client = find_or_create_client(form.get("client_name"),
                                       form.get("industry", ""),
                                       form.get("broker", ""),
                                       form.get("ein", ""))
        contract = Contract(client_id=client.id, name=form.get("name") or "New contract")
        db.session.add(contract)
        db.session.flush()
    else:
        # editing — keep the same client but let name/industry/broker be updated
        if form.get("client_name"):
            contract.client.name = form.get("client_name").strip()
        if form.get("industry"):
            contract.client.industry = form.get("industry")
        if form.get("broker"):
            contract.client.broker = form.get("broker")
    apply_contract_fields(contract, form)
    set_contract_lines(contract, form)
    db.session.commit()
    return contract


def ingest_eligibility(file_storage, client, month, label=""):
    """
    Read an uploaded eligibility CSV into a new ActivityPeriod.
    Expected columns (case-insensitive): metric_key (or metric/key), count (or value),
    and optional label. One row per metric.
    """
    raw = file_storage.read()
    text = raw.decode("utf-8-sig", errors="ignore")
    period = ActivityPeriod(client_id=client.id, month=month,
                            label=label or f"{month} eligibility",
                            source=f"Uploaded · {file_storage.filename}")
    db.session.add(period)
    db.session.flush()
    n = 0
    for row in csv.DictReader(io.StringIO(text)):
        low = {(k or "").lower().strip(): v for k, v in row.items()}
        mk = (low.get("metric_key") or low.get("metric") or low.get("key") or "").strip()
        if not mk:
            continue
        cnt = _num(low.get("count") or low.get("value") or low.get("enrolled") or 0, int)
        lbl = (low.get("label") or mk.replace("_", " ").title())
        db.session.add(ActivityMetric(period_id=period.id, metric_key=mk,
                                      label=lbl, count=cnt))
        n += 1
    db.session.commit()
    return period, n
