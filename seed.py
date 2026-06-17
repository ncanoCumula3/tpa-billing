"""
Seed realistic generic-TPA demo data.

Hero client: Riverbend Manufacturing — a full administrative-services agreement
(medical / dental / FSA / COBRA / claims / compliance / a discontinued wellness
add-on) and a March-2026 eligibility file that deliberately drifts from the roster,
so the reconciliation shows green / yellow / red:

  medical   412 vs 412   green
  dental    roster 388, eligibility 401   yellow (under-billed, new enrollees)
  FSA       roster 180, eligibility 171   red   (over-billed, terminations)
  COBRA     14 vs 14     green
  claims    540 vs 540   green
  5500      flat          green
  wellness  discontinued  red   (should not be billed)

Run:  python seed.py
"""
from app import app
from models import (db, Client, Contract, ContractLine, ActivityPeriod,
                    ActivityMetric, PEPM, PPPM, PER_TXN, FLAT)


def seed():
    with app.app_context():
        db.drop_all()
        db.create_all()

        # ---------------- Riverbend Manufacturing (hero) ----------------
        rb = Client(name="Riverbend Manufacturing", industry="Manufacturing",
                    ein="84-1592637", broker="Keystone Benefits Group")
        db.session.add(rb)
        db.session.flush()

        c = Contract(client_id=rb.id, name="Riverbend MFG — Administrative Services Agreement",
                     number="ASA-2026-0142", effective_start="2026-01-01",
                     effective_end="2026-12-31", billing_frequency="Monthly",
                     funding_type="Self-funded", plan_year="2026 calendar year",
                     renewal="Auto-renew · 12-month term · 60-day notice",
                     payment_terms="Net 15 · ACH debit", late_fee="1.5% / mo on balances > 30 days",
                     runout="90-day claims run-out", covered_lives=412,
                     sla="Claims adjudication ≤ 10 business days · 99.5% financial accuracy · eligibility loaded within 2 business days",
                     primary_contact="Dana Holt, VP People", contact_email="dholt@riverbendmfg.com")
        db.session.add(c)
        db.session.flush()

        lines = [
            # service, basis, rate, metric_key, baseline, active
            ("Medical Plan Administration", PEPM, 5.25, "enrolled_employees", 412, True),
            ("Dental & Vision Administration", PEPM, 1.75, "dental_enrolled", 388, True),
            ("FSA Administration", PPPM, 4.00, "fsa_participants", 180, True),
            ("COBRA Administration", PPPM, 18.00, "cobra_members", 14, True),
            ("Claims Processing", PER_TXN, 1.10, "claims_processed", 540, True),
            ("ACA / Form 5500 Compliance", FLAT, 250.00, None, 0, True),
            ("Wellness Program Administration", PEPM, 0.90, "enrolled_employees", 412, False),
        ]
        for svc, basis, rate, mk, base, active in lines:
            db.session.add(ContractLine(contract_id=c.id, service=svc, basis=basis,
                                        rate=rate, metric_key=mk, baseline_count=base,
                                        active=active))

        # Four monthly eligibility files for the same contract — the roster
        # drifts month to month, so reconciliation tells a different story each
        # period (March is the one that drifts off the baseline into yellow/red).
        labels = {
            "enrolled_employees": "Enrolled employees",
            "dental_enrolled": "Dental / vision enrolled",
            "fsa_participants": "FSA participants",
            "cobra_members": "COBRA members",
            "claims_processed": "Claims processed",
        }
        eligibility_files = [
            # month,      source,                       medical dental fsa cobra claims
            ("2026-01", "Eligibility file (ADP)",        405,   380,  178, 12,   498),
            ("2026-02", "Eligibility file (ADP)",        409,   392,  175, 13,   521),
            ("2026-03", "Eligibility file (ADP)",        412,   401,  171, 14,   540),
            ("2026-04", "Eligibility file (Workday)",    418,   405,  169, 15,   562),
        ]
        for month, source, med, den, fsa, cob, clm in eligibility_files:
            period = ActivityPeriod(client_id=rb.id, month=month,
                                    label=f"{month} eligibility", source=source)
            db.session.add(period)
            db.session.flush()
            counts = {"enrolled_employees": med, "dental_enrolled": den,
                      "fsa_participants": fsa, "cobra_members": cob,
                      "claims_processed": clm}
            for mk, n in counts.items():
                db.session.add(ActivityMetric(period_id=period.id, metric_key=mk,
                                              label=labels[mk], count=n))

        # ---------------- two more clients for pipeline colour ----------------
        cascade = Client(name="Cascade Health Group", industry="Healthcare",
                         ein="91-2048817", broker="Summit Advisors")
        db.session.add(cascade)
        db.session.flush()
        cc = Contract(client_id=cascade.id, name="Cascade Health — ASA",
                      number="ASA-2026-0188", effective_start="2026-02-01",
                      effective_end="2027-01-31")
        db.session.add(cc)
        db.session.flush()
        for svc, basis, rate, mk, base in [
            ("Medical Plan Administration", PEPM, 4.90, "enrolled_employees", 256),
            ("FSA Administration", PPPM, 3.75, "fsa_participants", 120),
            ("ACA / Form 5500 Compliance", FLAT, 250.0, None, 0)]:
            db.session.add(ContractLine(contract_id=cc.id, service=svc, basis=basis,
                                        rate=rate, metric_key=mk, baseline_count=base))
        cp = ActivityPeriod(client_id=cascade.id, month="2026-03", label="March 2026 eligibility")
        db.session.add(cp)
        db.session.flush()
        for mk, lbl, n in [("enrolled_employees", "Enrolled employees", 256),
                           ("fsa_participants", "FSA participants", 120)]:
            db.session.add(ActivityMetric(period_id=cp.id, metric_key=mk, label=lbl, count=n))

        summit = Client(name="Summit Logistics Co.", industry="Transportation",
                        ein="45-7781120", broker="Keystone Benefits Group")
        db.session.add(summit)
        db.session.flush()
        sc = Contract(client_id=summit.id, name="Summit Logistics — ASA (prior term)",
                      number="ASA-2025-0097", effective_start="2025-01-01",
                      effective_end="2025-12-31")          # expired
        db.session.add(sc)
        db.session.flush()
        db.session.add(ContractLine(contract_id=sc.id, service="Medical Plan Administration",
                                    basis=PEPM, rate=5.10, metric_key="enrolled_employees",
                                    baseline_count=98))

        db.session.commit()
        print(f"Seeded {Client.query.count()} clients, {Contract.query.count()} contracts, "
              f"{ActivityPeriod.query.count()} activity periods.")


if __name__ == "__main__":
    seed()
