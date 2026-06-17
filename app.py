"""
Meridian TPA Billing — contract → eligibility → invoice → close.

A generic Third-Party Administrator billing console. Turn each client's
administrative-services agreement and monthly eligibility file into a reconciled
invoice: every fee line is checked against the contracted rate and the actual
eligibility, so you never over- or under-bill.
"""
import os

from flask import (Flask, render_template, redirect, url_for, request,
                   Response, flash, session)

import csv
import io
from datetime import datetime

from models import (db, Client, Contract, ActivityPeriod, Invoice, BASIS_LABEL,
                    PEPM, PPPM, PER_TXN, FLAT)
from services import invoicer, exporter, forms

BASE = os.path.dirname(os.path.abspath(__file__))
PRODUCT = "Oakmore Labs · TPA Billing"


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "tpa-demo-key")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE, "tpa.db"))
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()

    @app.context_processor
    def inject():
        return {"PRODUCT": PRODUCT}

    # ---------- auth (credentials come from env, never the repo) ----------
    TPA_USER = os.environ.get("TPA_USER", "")
    TPA_PASSWORD = os.environ.get("TPA_PASSWORD", "")

    @app.before_request
    def _require_login():
        if request.endpoint in ("login", "logout", "healthz", "static"):
            return
        if not session.get("user"):
            return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            if TPA_PASSWORD and email == TPA_USER and password == TPA_PASSWORD:
                session["user"] = email
                return redirect(url_for("dashboard"))
            error = "Invalid email or password."
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def dashboard():
        contracts = Contract.query.all()
        invoices = Invoice.query.order_by(Invoice.created_at.desc()).all()
        kpi = {
            "clients": Client.query.count(),
            "contracts": len(contracts),
            "active": sum(1 for c in contracts if c.status == "Active"),
            "invoices": len(invoices),
            "billed": round(sum(i.billed_total for i in invoices if i.status == "Closed"), 2),
            "held": round(sum(i.held_total for i in invoices if i.status == "Closed"), 2),
        }
        return render_template("dashboard.html", contracts=contracts,
                               invoices=invoices, kpi=kpi)

    # ---------- contracts ----------
    @app.route("/contracts")
    def contracts():
        return render_template("contracts.html", contracts=Contract.query.all())

    @app.route("/contracts/new", methods=["GET", "POST"])
    def new_contract():
        if request.method == "POST":
            c = forms.save_contract(request.form)
            flash(f"Contract {c.number or c.name} created.", "ok")
            return redirect(url_for("contract_detail", cid=c.id))
        return render_template("contract_form.html", c=None, bases=BASIS_LABEL,
                               clients=Client.query.order_by(Client.name).all())

    @app.route("/contracts/<int:cid>/edit", methods=["GET", "POST"])
    def edit_contract(cid):
        c = Contract.query.get_or_404(cid)
        if request.method == "POST":
            forms.save_contract(request.form, contract=c)
            flash("Contract updated.", "ok")
            return redirect(url_for("contract_detail", cid=c.id))
        return render_template("contract_form.html", c=c, bases=BASIS_LABEL,
                               clients=Client.query.order_by(Client.name).all())

    @app.route("/contracts/<int:cid>/upload-eligibility", methods=["POST"])
    def upload_eligibility(cid):
        c = Contract.query.get_or_404(cid)
        file = request.files.get("file")
        month = (request.form.get("month") or "").strip()
        if not file or not file.filename:
            flash("Choose an eligibility CSV to upload.", "warn")
            return redirect(url_for("contract_detail", cid=cid))
        if not month:
            flash("Enter the period month (YYYY-MM) for the eligibility file.", "warn")
            return redirect(url_for("contract_detail", cid=cid))
        period, n = forms.ingest_eligibility(file, c.client, month,
                                             request.form.get("label", ""))
        flash(f"Loaded {n} metric(s) into the {period.month} eligibility period.", "ok")
        return redirect(url_for("period_detail", pid=period.id))

    @app.route("/contracts/<int:cid>/eligibility-template.csv")
    def eligibility_template(cid):
        """A ready-to-fill eligibility CSV, pre-seeded with the exact metric
        keys this contract's fee schedule bills on. The TPA fills the `count`
        column from the client's roster/claims feed and uploads it back."""
        c = Contract.query.get_or_404(cid)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["metric_key", "count", "label"])
        seen = set()
        for l in c.lines:
            if l.basis == FLAT or not l.metric_key or l.metric_key in seen:
                continue
            seen.add(l.metric_key)
            # baseline roster count is a hint of the expected magnitude, not a bill
            w.writerow([l.metric_key, l.baseline_count or 0, l.service])
        if not seen:
            w.writerow(["enrolled_employees", 0, "Enrolled employees"])
        fname = f"eligibility-{c.number or c.id}-template.csv"
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.route("/contracts/<int:cid>")
    def contract_detail(cid):
        c = Contract.query.get_or_404(cid)
        periods = ActivityPeriod.query.filter_by(client_id=c.client_id).order_by(
            ActivityPeriod.month.desc()).all()
        invoices = Invoice.query.filter_by(contract_id=c.id).order_by(
            Invoice.created_at.desc()).all()
        return render_template("contract_detail.html", c=c, periods=periods, invoices=invoices)

    @app.route("/contracts/<int:cid>/generate-invoice", methods=["POST"])
    def generate_invoice(cid):
        c = Contract.query.get_or_404(cid)
        period = ActivityPeriod.query.get_or_404(request.form.get("period_id", type=int))
        inv = invoicer.generate(c, period)
        flash(f"Invoice {inv.number} generated and reconciled against the eligibility file.", "ok")
        return redirect(url_for("invoice_detail", iid=inv.id))

    # ---------- activity periods ----------
    @app.route("/periods/<int:pid>")
    def period_detail(pid):
        p = ActivityPeriod.query.get_or_404(pid)
        contract = Contract.query.filter_by(client_id=p.client_id).first()
        return render_template("period_detail.html", p=p, contract=contract)

    # ---------- invoices ----------
    @app.route("/invoices")
    def invoices():
        return render_template("invoices.html",
                               invoices=Invoice.query.order_by(Invoice.created_at.desc()).all())

    @app.route("/invoices/<int:iid>")
    def invoice_detail(iid):
        inv = Invoice.query.get_or_404(iid)
        return render_template("invoice_detail.html", inv=inv, client=inv.client,
                               contract=inv.contract)

    @app.route("/invoices/<int:iid>/issue", methods=["POST"])
    def issue_invoice(iid):
        invoicer.issue(Invoice.query.get_or_404(iid))
        return redirect(url_for("invoice_detail", iid=iid))

    @app.route("/invoices/<int:iid>/close", methods=["POST"])
    def close_invoice(iid):
        inv = invoicer.close(Invoice.query.get_or_404(iid))
        if inv.status == "Closed":
            flash(f"Invoice {inv.number} closed — ${inv.billed_total:,.2f} billed, "
                  f"${inv.held_total:,.2f} held for correction.", "ok")
        else:
            flash(f"Invoice {inv.number} on Hold — nothing reconciled.", "warn")
        return redirect(url_for("invoice_detail", iid=iid))

    @app.route("/invoices/<int:iid>/reopen", methods=["POST"])
    def reopen_invoice(iid):
        invoicer.reopen(Invoice.query.get_or_404(iid))
        return redirect(url_for("invoice_detail", iid=iid))

    @app.route("/invoices/<int:iid>/void", methods=["POST"])
    def void_invoice(iid):
        inv = invoicer.void(Invoice.query.get_or_404(iid),
                            (request.form.get("reason") or "").strip())
        flash(f"Invoice {inv.number} voided — nothing billed.", "warn")
        return redirect(url_for("invoice_detail", iid=iid))

    @app.route("/invoices/<int:iid>/export.xlsx")
    def export_invoice(iid):
        inv = Invoice.query.get_or_404(iid)
        data = exporter.build_xlsx(inv, inv.client, inv.contract)
        return Response(data,
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{inv.number}.xlsx"'})

    # ---------- reporting ----------
    @app.route("/reports")
    def reports():
        invoices = Invoice.query.all()
        by_status = {s: 0 for s in ("Draft", "Issued", "Closed", "Hold", "Void")}
        for i in invoices:
            by_status[i.status] = by_status.get(i.status, 0) + 1
        closed = [i for i in invoices if i.status == "Closed"]
        billed = round(sum(i.billed_total for i in closed), 2)
        held = round(sum(i.held_total for i in closed), 2)
        by_client, by_month = {}, {}
        for i in closed:
            by_client[i.client.name] = by_client.get(i.client.name, 0.0) + i.billed_total
            by_month[i.month] = by_month.get(i.month, 0.0) + i.billed_total
        contracts = Contract.query.all()
        annualized = round(sum(c.monthly_estimate * 12 for c in contracts
                               if c.status == "Active"), 2)
        return render_template(
            "reports.html", by_status=by_status, billed=billed, held=held,
            by_client=sorted(by_client.items(), key=lambda x: -x[1]),
            by_month=sorted(by_month.items()), annualized=annualized,
            n_contracts=len(contracts),
            n_active=sum(1 for c in contracts if c.status == "Active"))

    @app.route("/reports/export.csv")
    def reports_export():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Invoice", "Client", "Contract", "Period", "Status",
                    "Billed", "Held", "Created"])
        for i in Invoice.query.order_by(Invoice.created_at.desc()).all():
            w.writerow([i.number, i.client.name,
                        (i.contract.number if i.contract else ""), i.month, i.status,
                        f"{i.billed_total:.2f}", f"{i.held_total:.2f}",
                        i.created_at.strftime("%Y-%m-%d") if i.created_at else ""])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 'attachment; filename="tpa-invoices.csv"'})

    @app.route("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5090)), debug=False)
