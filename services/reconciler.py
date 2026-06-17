"""
Reconciliation — "close the invoice against the contract and the work".

Each invoice line bills a contracted fee against a roster count. Before it can
close we check it two ways:
  * the contract — is it in force, is the service still active, is the rate ours?
  * the work     — does the billed roster match the period's actual eligibility?

green  = bill it · yellow = bill, but flag a variance · red = hold, do not bill.
"""
from models import PEPM, PPPM, PER_TXN, FLAT

TOL = 0.01   # 1% rounding tolerance on counts


def _unit(basis):
    return {PEPM: "employees", PPPM: "participants",
            PER_TXN: "transactions", FLAT: ""}.get(basis, "members")


def reconcile(line, contract, month, billed_count, actual_count):
    """line = ContractLine. Returns dict(status, reason, charged)."""
    # 1. contract in force?
    if not contract.active_for(month):
        return _v("red",
                  f"Contract not in force for {month} "
                  f"({contract.effective_start or '?'} – {contract.effective_end or '?'}). Held.",
                  False)

    # 2. service still active under the agreement?
    if not line.active:
        return _v("red",
                  "Service discontinued under the agreement — should not be billed. Held.",
                  False)

    # 3. flat fees have no count to reconcile.
    if line.basis == FLAT:
        return _v("green", "Flat monthly fee per the contract.", True)

    unit = _unit(line.basis)

    # 4. no eligibility data for this metric.
    if actual_count is None:
        return _v("yellow",
                  f"No eligibility data this period — billed at roster ({billed_count} {unit}). Review.",
                  True)

    diff = actual_count - billed_count

    # exact / within rounding
    if diff == 0 or abs(diff) <= max(1, billed_count * TOL) and abs(diff) <= 1:
        return _v("green", f"Matches eligibility — {billed_count} {unit}.", True)

    # over-billed: roster higher than eligibility (terminations not credited)
    if diff < 0:
        return _v("red",
                  f"Over-billed: roster {billed_count} vs eligibility {actual_count} "
                  f"— {abs(diff)} {unit} no longer eligible. Held for correction.",
                  False)

    # under-billed: eligibility higher than roster (new enrollees not yet billed)
    return _v("yellow",
              f"Under-billed: eligibility {actual_count} vs roster {billed_count} "
              f"— {diff} new {unit} not yet on the invoice. Charged; true up next cycle.",
              True)


def _v(status, reason, charged):
    return {"status": status, "reason": reason, "charged": charged}
