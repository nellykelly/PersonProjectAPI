"""Acceptance checks for the policy-number-filter ticket (what CI runs on the PR)."""
import json
import re
import shutil



def expected_ids(con, policy_numbers, state):
    """Reference answer, computed straight from the EXPORT tables."""
    st = f" and {{a}}.record_state = '{state}'" if state else ""
    if policy_numbers is None:
        sel_pp = sel_veh = sel_acc = "1=1"
    else:
        pn = ", ".join(f"'{p}'" for p in policy_numbers)
        sel_pp = f"pp.policy_number in ({pn})"
        sel_veh = f"veh.payload_id in (select pp.payload_id from export.policyperiod pp where {sel_pp})"
        sel_acc = (f"(acc.source_system, acc.account_number) in "
                   f"(select pp.source_system, pp.account_number from export.policyperiod pp where {sel_pp})")
    return {
        "policyperiod": {r[0] for r in con.execute(f"select pp.record_id from export.policyperiod pp where {sel_pp}{st.format(a='pp')}").fetchall()},
        "vehicle": {r[0] for r in con.execute(f"select veh.record_id from export.vehicle veh where {sel_veh}{st.format(a='veh')}").fetchall()},
        "account": {r[0] for r in con.execute(f"select acc.record_id from export.account acc where {sel_acc}{st.format(a='acc')}").fetchall()},
    }


def run_case(engine, dbt_vars, policy_numbers, state):
    args = ["run", "--select", "tag:views", "--target", "check"]
    if dbt_vars is not None:
        args += ["--vars", json.dumps(dbt_vars)]
    code, text = engine.run_dbt(args, warehouse=engine.B().check_warehouse)
    if code != 0:
        errors = [l.strip() for l in text.splitlines() if "rror" in l][:6]
        return False, "dbt run failed: " + " / ".join(errors)
    con = engine.connect(engine.B().check_warehouse, read_only=True)
    try:
        expected = expected_ids(con, policy_numbers, state)
        actual = {v: {r[0] for r in con.execute(f"select record_id from export_views.{v}").fetchall()}
                  for v in ("policyperiod", "vehicle", "account")}
    except Exception as err:
        return False, f"Couldn't read EXPORT_VIEWS: {err}"
    finally:
        con.close()
    problems = []
    for view in ("policyperiod", "vehicle", "account"):
        exp, act = expected[view], actual[view]
        if exp != act:
            problems.append(f"{view.upper()}: expected {len(exp)} rows, got {len(act)}"
                            f" ({len(act - exp)} extra, {len(exp - act)} missing)")
    if problems:
        return False, "; ".join(problems)
    return True, ", ".join(f"{v.upper()}={len(actual[v])}" for v in ("policyperiod", "vehicle", "account"))


CASES = [
    ("No policy filter: full population (no regression)", None, None, None),
    ("policy_numbers [PN1001, IW2002]: only those policies, their vehicles and their accounts",
     {"policy_numbers": ["PN1001", "IW2002"]}, ["PN1001", "IW2002"], None),
    ("policy_numbers [PN1003]: only its own account",
     {"policy_numbers": ["PN1003"]}, ["PN1003"], None),
    ("Combines with record_state: [PN1001, PN1002] + KY",
     {"policy_numbers": ["PN1001", "PN1002"], "record_state": "KY"}, ["PN1001", "PN1002"], "KY"),
    ("Unknown policy number: every view empty, no error",
     {"policy_numbers": ["ZZ9999"]}, ["ZZ9999"], None),
    ("Empty list: treated as no filter",
     {"policy_numbers": []}, None, None),
]


def check():
    import engine

    box = engine.B()
    if not box.warehouse.exists():
        return {"ok": False, "error": "Reset the sandbox first."}
    shutil.copyfile(box.warehouse, box.check_warehouse)
    results = []
    try:
        for i, (label, dbt_vars, pns, state) in enumerate(CASES, 1):
            ok, detail = run_case(engine, dbt_vars, pns, state)
            results.append({"id": f"AC{i}", "label": label, "ok": ok, "detail": detail, "bonus": False})

        label = "DAG passes dag_run.conf policy_numbers through to dbt --vars"
        try:
            module = engine.load_dag_module()
            with_filter = " ".join(module.build_dbt_commands({"policy_numbers": ["PN1001"], "changed_since": "2026-01-01"}))
            without = " ".join(module.build_dbt_commands({"changed_since": "2026-01-01"}))
            why = []
            if not ("policy_numbers" in with_filter and "PN1001" in with_filter):
                why.append("policy_numbers from the conf never reaches the dbt command")
            if "policy_numbers" in without:
                why.append("policy_numbers is sent even when the conf doesn't have it")
            results.append({"id": "AC7", "label": label, "ok": not why, "detail": "; ".join(why) or "conf -> --vars OK", "bonus": False})
        except Exception as err:
            results.append({"id": "AC7", "label": label, "ok": False, "detail": f"DAG file error: {type(err).__name__}: {err}", "bonus": False})

        ok, detail = run_case(engine, {"policy_numbers": "PN1001, IW2002"}, ["PN1001", "IW2002"], None)
        results.append({"id": "BONUS1", "label": "A comma-separated string 'PN1001, IW2002' also works", "ok": ok, "detail": detail, "bonus": True})

        project = (box.repo / "dbt_project.yml").read_text(encoding="utf-8")
        declared = re.search(r"^\s+policy_numbers\s*:", project, re.M) is not None
        results.append({"id": "BONUS2", "label": "policy_numbers declared with a default under vars: in dbt_project.yml",
                        "ok": declared, "detail": "declared" if declared else "not declared (works, but undocumented)", "bonus": True})
    finally:
        if box.check_warehouse.exists():
            box.check_warehouse.unlink()
    required = [r for r in results if not r["bonus"]]
    return {"ok": True, "passed": all(r["ok"] for r in required),
            "score": f"{sum(r['ok'] for r in required)}/{len(required)}", "results": results}
