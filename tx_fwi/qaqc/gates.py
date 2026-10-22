from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from tx_fwi.qaqc.checks import check_date_coverage, check_duplicate_keys
from tx_fwi.qaqc.models import CheckResult, ComponentResult


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_pcp_gate(
    *,
    pcp_dir,
    manifest_path,
    expected_start,
    expected_end,
    expected_watersheds,
) -> ComponentResult:
    result = ComponentResult(
        component="precipitation_pcp_gate",
        requested_start=str(pd.Timestamp(expected_start).date()),
        requested_end=str(pd.Timestamp(expected_end).date()),
        append_status="not_applicable",
    )
    pcp_dir = Path(pcp_dir)
    manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        result.add_check(CheckResult(
            check_id="pcp_manifest_exists",
            status="FAIL",
            message="PCP manifest is missing.",
            blocking=True,
        ))
        result.finalize()
        return result

    with manifest_path.open(encoding="utf-8") as file:
        manifest = json.load(file)

    task_passed = manifest.get("status") == "PASS"
    result.add_check(CheckResult(
        check_id="precipitation_task_status",
        status="PASS" if task_passed else "FAIL",
        message="Precipitation task reported PASS." if task_passed else "Precipitation task did not report PASS.",
        actual={"status": manifest.get("status")},
        blocking=True,
    ))

    expected_start_ts = pd.Timestamp(expected_start).normalize()
    expected_end_ts = pd.Timestamp(expected_end).normalize()
    manifest_start = pd.to_datetime(manifest.get("period_start"), errors="coerce")
    manifest_end = pd.to_datetime(manifest.get("period_end"), errors="coerce")
    period_passed = (
        pd.notna(manifest_start)
        and pd.notna(manifest_end)
        and manifest_start.normalize() == expected_start_ts
        and manifest_end.normalize() == expected_end_ts
    )
    result.add_check(CheckResult(
        check_id="pcp_manifest_period",
        status="PASS" if period_passed else "FAIL",
        message="PCP manifest period matches the expected period." if period_passed else "PCP manifest period does not match the expected period.",
        expected={"start": str(expected_start_ts.date()), "end": str(expected_end_ts.date())},
        actual={"start": str(manifest_start.date()) if pd.notna(manifest_start) else None, "end": str(manifest_end.date()) if pd.notna(manifest_end) else None},
        blocking=True,
    ))

    entries = manifest.get("files", [])
    expected_ids = {str(value) for value in expected_watersheds}
    actual_ids = {str(entry.get("watershed_id")) for entry in entries}
    missing_ids = sorted(expected_ids - actual_ids)
    unexpected_ids = sorted(actual_ids - expected_ids)
    result.add_check(CheckResult(
        check_id="pcp_expected_watersheds",
        status="PASS" if not missing_ids and not unexpected_ids else "FAIL",
        message="PCP watershed set matches the registry." if not missing_ids and not unexpected_ids else "PCP watershed set does not match the registry.",
        expected={"watersheds": len(expected_ids)},
        actual={"watersheds": len(actual_ids), "missing": missing_ids, "unexpected": unexpected_ids},
        blocking=True,
    ))

    failed_files = []
    for entry in entries:
        file_path = pcp_dir / entry["filename"]
        if not file_path.exists():
            failed_files.append({"filename": entry["filename"], "reason": "file_missing"})
            continue
        expected_hash = entry.get("sha256")
        if expected_hash and sha256_file(file_path) != expected_hash:
            failed_files.append({"filename": entry["filename"], "reason": "checksum_mismatch"})

    result.add_check(CheckResult(
        check_id="pcp_file_integrity",
        status="PASS" if not failed_files else "FAIL",
        message="All PCP files exist and match the manifest." if not failed_files else "One or more PCP files failed integrity checks.",
        actual={"failed_files": failed_files},
        blocking=True,
    ))

    unresolved = manifest.get("unresolved_failures", [])
    result.add_check(CheckResult(
        check_id="pcp_unresolved_failures",
        status="PASS" if not unresolved else "FAIL",
        message="PCP manifest contains no unresolved failures." if not unresolved else "PCP manifest contains unresolved failures.",
        actual={"unresolved_failures": unresolved},
        blocking=True,
    ))

    result.finalize()
    return result


def validate_txrr_input_gate(*, manifest_path, expected_pcp_end) -> ComponentResult:
    result = ComponentResult(
        component="txrr_input_gate",
        requested_end=str(pd.Timestamp(expected_pcp_end).date()),
        append_status="not_applicable",
    )
    path = Path(manifest_path)
    if not path.exists():
        result.add_check(CheckResult("txrr_input_manifest_exists", "FAIL", "TxRR input manifest is missing."))
        result.finalize()
        return result
    with path.open(encoding="utf-8") as file:
        manifest = json.load(file)
    expected_end = pd.Timestamp(expected_pcp_end).normalize()
    model_end = pd.to_datetime(manifest.get("model_end_date"), errors="coerce")
    no_missing = not manifest.get("missing_pcp_files", [])
    completed = manifest.get("status") == "PASS"
    result.add_check(CheckResult("txrr_input_task_status", "PASS" if completed else "FAIL", "TxRR input update reported PASS." if completed else "TxRR input update did not report PASS."))
    result.add_check(CheckResult(
        "txrr_model_end_matches_pcp",
        "PASS" if pd.notna(model_end) and model_end.normalize() == expected_end else "FAIL",
        "TxRR model end date matches the PCP end date." if pd.notna(model_end) and model_end.normalize() == expected_end else "TxRR model end date does not match the PCP end date.",
        expected={"end": str(expected_end.date())},
        actual={"end": str(model_end.date()) if pd.notna(model_end) else None},
    ))
    result.add_check(CheckResult("txrr_required_pcp_files", "PASS" if no_missing else "FAIL", "No required PCP files are missing." if no_missing else "Required PCP files are missing.", actual={"missing": manifest.get("missing_pcp_files", [])}))
    result.finalize()
    return result


def validate_txrr_output_gate(
    *,
    manifest_path,
    expected_start,
    expected_end,
    expected_watersheds,
) -> ComponentResult:
    result = ComponentResult(
        component="txrr_output_gate",
        requested_start=str(pd.Timestamp(expected_start).date()),
        requested_end=str(pd.Timestamp(expected_end).date()),
        append_status="not_applicable",
    )
    path = Path(manifest_path)
    if not path.exists():
        result.add_check(CheckResult("txrr_output_manifest_exists", "FAIL", "TxRR output manifest is missing."))
        result.finalize()
        return result
    with path.open(encoding="utf-8") as file:
        manifest = json.load(file)

    completed = manifest.get("status") == "PASS" and manifest.get("exit_code") == 0
    result.add_check(CheckResult("txrr_runtime_status", "PASS" if completed else "FAIL", "TxRR completed without a runtime error." if completed else "TxRR did not complete successfully.", actual={"status": manifest.get("status"), "exit_code": manifest.get("exit_code")}))

    entries = manifest.get("outputs", [])
    expected_ids = {str(value) for value in expected_watersheds}
    actual_ids = {str(entry.get("watershed_id")) for entry in entries if entry.get("nonempty", False)}
    missing_ids = sorted(expected_ids - actual_ids)
    unexpected_ids = sorted(actual_ids - expected_ids)
    result.add_check(CheckResult(
        "txrr_expected_outputs",
        "PASS" if not missing_ids and not unexpected_ids else "FAIL",
        "TxRR output watershed set matches the registry." if not missing_ids and not unexpected_ids else "TxRR output watershed set does not match the registry.",
        expected={"watersheds": len(expected_ids)},
        actual={"watersheds": len(actual_ids), "missing": missing_ids, "unexpected": unexpected_ids},
    ))

    bad_files = []
    expected_start_ts = pd.Timestamp(expected_start).normalize()
    expected_end_ts = pd.Timestamp(expected_end).normalize()
    for entry in entries:
        first_date = pd.to_datetime(entry.get("first_date"), errors="coerce")
        last_date = pd.to_datetime(entry.get("last_date"), errors="coerce")
        valid = (
            entry.get("nonempty", False)
            and entry.get("created_by_current_run", False)
            and pd.notna(first_date)
            and pd.notna(last_date)
            and first_date.normalize() <= expected_start_ts
            and last_date.normalize() >= expected_end_ts
            and int(entry.get("duplicate_dates", 0)) == 0
        )
        if not valid:
            bad_files.append(entry)
    result.add_check(CheckResult("txrr_output_file_validation", "PASS" if not bad_files else "FAIL", "All TxRR output files passed validation." if not bad_files else "One or more TxRR output files failed validation.", actual={"failed_outputs": bad_files}))
    result.finalize()
    return result
