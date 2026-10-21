"""Read-only comparison of saved results, with exact-source cohort verification."""

import argparse
import ast
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from evaluation.baseline import (
    ROOT,
    BaselineError,
    digest,
    load_archived_report,
)

CORE_EVALUATION_FILES = {
    "__init__",
    "contracts",
    "dataset",
    "recorder",
    "orchestration",
    "metrics",
    "judges",
    "qualitative",
    "tool_evaluators",
    "trajectory",
}
AGGREGATES = (
    "total_cases",
    "execution_states",
    "evaluation_states",
    "final_statuses",
    "expected_status",
    "successful_runs",
    "evaluators",
    "measurements",
    "evaluator_versions_observed",
)
# Frozen reporting ASTs: collection (a417866) and approved Phase 8 repairs (ec9518c).
HISTORICAL_REPORTING = (
    "692c33422b43bfdb0b4186ef0146ce6ea2ee57423ab3b5307421cdcffdc4e90e"
)
CURRENT_REPORTING = "be7eed3c6ebb1faecfc95e727ebd74c0007cc67386832088dc7de1dde0d6d204"
# Reader versions distinguish original execution, Phase 8 archival repairs, and V2 identity support.
HISTORICAL_READERS = "7ef91544e1e6fadbf6641e6f97487b1b975c5d28764b744a0a54a636743a4a9a"
ARCHIVE_READERS = "7a4c14e7013f6d72432d714392e7a71c5fd0d10882f89440c390b370579f5a35"
CURRENT_READERS = "36150dda491618c7f5e3a4961bc150d4177c301d8a99953e89387f3f284df300"


def _canonical_ast(value):
    if isinstance(value, ast.AST):
        return [
            type(value).__name__,
            {
                name: _canonical_ast(field)
                for name, field in ast.iter_fields(value)
                if field is not None and field != []
            },
        ]
    if isinstance(value, list):
        return [_canonical_ast(item) for item in value]
    return value


def reporting_fingerprint(content: bytes) -> str:
    tree = ast.parse(content)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or getattr(node, "name", None)
        in {"_measurement", "aggregate_records", "build_summary"}
    ]
    for node in nodes:
        if getattr(node, "name", None) == "build_summary":
            for item in ast.walk(node):
                if isinstance(item, ast.Dict):
                    for index, key in enumerate(item.keys):
                        if isinstance(key, ast.Constant) and key.value == "kind":
                            item.values[index] = ast.Constant(
                                value="<experiment summary kind>"
                            )
    # Empty optional AST fields differ across supported Python versions.
    encoded = json.dumps(
        _canonical_ast(ast.Module(body=nodes, type_ignores=[])), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def reader_fingerprint(content: bytes) -> str:
    names = {
        "BaselineError",
        "BaselineConfig",
        "BaselineManifest",
        "CaseExecution",
        "digest",
        "source_digest",
        "runtime_versions",
        "_read_manifest",
        "load_manifest",
        "_read_snapshot",
        "load_snapshot",
        "_read_bundle",
        "load_bundle",
        "load_archived_report",
    }
    constants = {"ROOT", "DATASET", "BASELINES", "EXPERIMENT_KINDS"}
    nodes = [
        node
        for node in ast.parse(content).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or getattr(node, "name", None) in names
        or isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets)
    ]
    encoded = json.dumps(
        _canonical_ast(ast.Module(body=nodes, type_ignores=[])), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_files(ref: str | None = None) -> dict[str, bytes]:
    """Read a Git tree or working sources without checkout, imports, or execution."""
    if ref is None:
        paths = [
            str(path.relative_to(ROOT))
            for folder in ("agent_service", "sandbox_api", "evaluation")
            for path in (ROOT / folder).rglob("*.py")
            if "artifacts" not in path.relative_to(ROOT / folder).parts
        ]
        read = lambda name: (ROOT / name).read_bytes()
    else:
        # Resolve first so revision syntax cannot be interpreted as Git options.
        ref = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        paths = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", ref],
            cwd=ROOT,
            text=True,
        ).splitlines()
        paths = [
            name
            for name in paths
            if name.endswith(".py")
            and name.split("/")[0] in {"agent_service", "sandbox_api", "evaluation"}
            and "artifacts" not in Path(name).parts
        ]
        read = lambda name: subprocess.check_output(
            ["git", "show", f"{ref}:{name}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    return {
        name: read(name) for name in [*paths, "pyproject.toml", "constraints-dev.txt"]
    }


def source_checksum(files: dict[str, bytes]) -> str:
    checksum = hashlib.sha256()
    for folder in ("agent_service", "sandbox_api", "evaluation"):
        for name in sorted(name for name in files if name.startswith(folder + "/")):
            checksum.update(name.encode())
            checksum.update(files[name])
    for name in ("pyproject.toml", "constraints-dev.txt"):
        checksum.update(files[name])
    return checksum.hexdigest()


def controlled_definitions(files: dict[str, bytes]) -> tuple[str, str]:
    """Freeze runtime/evaluator definitions; exempt only the instruction constant."""
    definitions = {
        name: content.hex()
        for name, content in files.items()
        if name.startswith(("agent_service/", "sandbox_api/"))
        or name in {f"evaluation/{stem}.py" for stem in CORE_EVALUATION_FILES}
        or name in {"pyproject.toml", "constraints-dev.txt"}
    }
    name = "agent_service/agent/openai_provider.py"
    tree = ast.parse(files[name])
    instruction = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "SYSTEM_INSTRUCTIONS"
            for t in node.targets
        )
    )
    prompt = ast.literal_eval(instruction.value)
    if not isinstance(prompt, str):
        raise BaselineError("INVALID_INSTRUCTION_CONSTANT")
    instruction.value = ast.Constant(value="<controlled prompt intervention>")
    definitions[name] = ast.dump(tree, include_attributes=False)
    # Infrastructure may evolve; fixture/setup execution and metric formulae may not.
    for name, functions in {
        "evaluation/baseline.py": {
            "cases",
            "_fixture",
            "collect_runs",
            "evaluate_saved",
            "comparison_context",
            "evaluation_metadata",
            "evaluation_directory",
            "case_directory",
        },
        "evaluation/baseline_report.py": {"_measurement"},
    }.items():
        module = ast.parse(files[name])
        nodes = [
            node for node in module.body if getattr(node, "name", None) in functions
        ]
        if {node.name for node in nodes} != functions:
            raise BaselineError("MISSING_CONTROLLED_DEFINITION")
        definitions[name] = "\n".join(
            ast.dump(node, include_attributes=False) for node in nodes
        )
        if name == "evaluation/baseline.py":
            used = {
                item.id
                for node in nodes
                for item in ast.walk(node)
                if isinstance(item, ast.Name)
            }
            bindings = []
            for node in module.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        if (alias.asname or alias.name.split(".")[0]) in used:
                            bindings.append(
                                (
                                    getattr(node, "module", None),
                                    alias.name,
                                    alias.asname,
                                )
                            )
                elif isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in used | {"ROOT", "DATASET"}
                    for t in node.targets
                ):
                    bindings.append(ast.dump(node, include_attributes=False))
            definitions["runner_bindings"] = json.dumps(bindings)
    encoded = json.dumps(definitions, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest(), prompt


def check_cohort(
    v1: dict, v2: dict, *, v1_source_ref: str, v2_source_ref: str | None = None
) -> dict:
    """Check definitions without rerunning evaluators or changing saved judgments."""
    left, right = v1["experiment"], v2["experiment"]
    if (
        left["kind"] != "apilens-v1-baseline"
        or right["kind"] != "apilens-v2-experiment"
    ):
        raise BaselineError("V1_V2_EXPERIMENTS_REQUIRED")
    if left["experiment_id"] == right["experiment_id"]:
        raise BaselineError("DISTINCT_EXPERIMENTS_REQUIRED")
    for key in ("dataset_sha256", "case_ids", "fixture_id", "config", "mode"):
        if left[key] != right[key]:
            raise BaselineError(f"COMPARISON_{key.upper()}_MISMATCH")
    if v1["evaluation_revision"] != "initial" or v2["evaluation_revision"] != "initial":
        raise BaselineError("INITIAL_EVALUATIONS_REQUIRED")
    definitions, prompts, readers_valid = [], [], []
    for manifest, ref in ((left, v1_source_ref), (right, v2_source_ref)):
        files = source_files(ref)
        if source_checksum(files) != manifest["source_sha256"]:
            raise BaselineError("COMPARISON_SOURCE_MISMATCH")
        approved_readers = {CURRENT_READERS}
        if manifest["kind"] == "apilens-v1-baseline":
            approved_readers.update({HISTORICAL_READERS, ARCHIVE_READERS})
        readers_valid.append(
            reader_fingerprint(files["evaluation/baseline.py"]) in approved_readers
        )
        approved = {CURRENT_REPORTING}
        if manifest["kind"] == "apilens-v1-baseline":
            approved.add(HISTORICAL_REPORTING)
        if (
            reporting_fingerprint(files["evaluation/baseline_report.py"])
            not in approved
        ):
            raise BaselineError("REPORTING_DEFINITIONS_MISMATCH")
        definition, prompt = controlled_definitions(files)
        definitions.append(definition)
        prompts.append(prompt)
    if definitions[0] != definitions[1]:
        raise BaselineError("CONTROLLED_DEFINITIONS_MISMATCH")
    if not all(readers_valid):
        raise BaselineError("READER_DEFINITIONS_MISMATCH")
    if prompts[0] == prompts[1]:
        raise BaselineError("PROMPT_INTERVENTION_REQUIRED")
    for name, versions in v2.get("evaluator_versions_observed", {}).items():
        if versions and versions != v1["evaluator_versions_observed"].get(name):
            raise BaselineError("EVALUATOR_VERSIONS_MISMATCH")
    runtime_differences = {
        name: {
            "v1": left["runtime_versions"].get(name),
            "v2": right["runtime_versions"].get(name),
        }
        for name in sorted(
            left["runtime_versions"].keys() | right["runtime_versions"].keys()
        )
        if left["runtime_versions"].get(name) != right["runtime_versions"].get(name)
    }
    return {
        "comparable": not runtime_differences,
        "experiment_ids": {"v1": left["experiment_id"], "v2": right["experiment_id"]},
        "controlled_definitions_sha256": definitions[0],
        "source_sha256": {"v1": left["source_sha256"], "v2": right["source_sha256"]},
        "runtime_differences": runtime_differences,
        "assessment": (
            "Runtime/dependency drift is a confound; do not attribute differences solely to the prompt."
            if runtime_differences
            else "Same controlled definitions and recorded runtime versions."
        ),
    }


def reference_check(
    directory: Path, report_id: str, source_ref: str, manifest: dict
) -> dict:
    reference = load_archived_report(directory, report_id)
    return check_cohort(
        reference,
        {"experiment": manifest, "evaluation_revision": "initial"},
        v1_source_ref=source_ref,
    )


def validate_reference(directory: Path, manifest: dict) -> dict:
    """Bind V2's immutable preflight reference to the original archived V1 report."""
    binding = _reference_binding(directory, manifest)
    reference = Path(binding["v1_directory"])
    report = reference / "reports" / binding["v1_report_id"] / "summary.json"
    if (
        binding["v2_experiment_id"] != manifest["experiment_id"]
        or digest(report) != binding["v1_report_sha256"]
    ):
        raise BaselineError("COMPARISON_REFERENCE_MISMATCH")
    check = reference_check(
        reference, binding["v1_report_id"], binding["v1_source_ref"], manifest
    )
    if not check["comparable"]:
        raise BaselineError("COMPARISON_RUNTIME_MISMATCH")
    return check


def _reference_binding(directory: Path, manifest: dict) -> dict:
    preflight = json.loads((directory / "preflight.json").read_text())
    if (
        preflight["experiment_id"] != manifest["experiment_id"]
        or preflight["source_sha256"] != manifest["source_sha256"]
        or preflight.get("comparison_sha256") != digest(directory / "comparison.json")
    ):
        raise BaselineError("COMPARISON_REFERENCE_MISMATCH")
    binding = json.loads((directory / "comparison.json").read_text())
    if binding["v2_experiment_id"] != manifest["experiment_id"]:
        raise BaselineError("COMPARISON_REFERENCE_MISMATCH")
    return binding


def _delta(left, right):
    return right - left if left is not None and right is not None else None


def _errors(summary: dict) -> dict:
    observed = [
        r["system_metrics"] for r in summary["cases"] if r["system_metrics"] is not None
    ]
    counts = Counter()
    for metrics in observed:
        counts.update(metrics["error_counts"])
    return {
        "observed_cases": len(observed),
        "missing_cases": len(summary["cases"]) - len(observed),
        "counts_observed": dict(counts),
    }


def compare_saved(
    v1_directory: Path,
    v1_report_id: str,
    v2_directory: Path,
    v2_report_id: str,
    *,
    v1_source_ref: str,
    v2_source_ref: str | None = None,
) -> dict:
    left = load_archived_report(v1_directory, v1_report_id)
    right = load_archived_report(v2_directory, v2_report_id)
    if right["experiment"]["mode"] == "live":
        binding = _reference_binding(v2_directory, right["experiment"])
        if (
            v1_directory.resolve() != Path(binding["v1_directory"]).resolve()
            or v1_report_id != binding["v1_report_id"]
            or left["experiment"]["experiment_id"] != binding["v1_experiment_id"]
            or digest(v1_directory / "reports" / v1_report_id / "summary.json")
            != binding["v1_report_sha256"]
        ):
            raise BaselineError("COMPARISON_REFERENCE_MISMATCH")
    cohort = check_cohort(
        left, right, v1_source_ref=v1_source_ref, v2_source_ref=v2_source_ref
    )
    if not cohort["comparable"]:
        return {
            "kind": "apilens-v1-v2-comparison",
            "cohort": cohort,
            "cases": [],
            "aggregates": None,
        }
    records = []
    for a, b in zip(left["cases"], right["cases"], strict=True):
        if (
            a["case_id"] != b["case_id"]
            or a["expected_final_status"] != b["expected_final_status"]
        ):
            raise BaselineError("COMPARISON_CASE_MISMATCH")
        complete = all(
            r["execution_state"] == "RETURNED"
            and r["evaluation_state"] == "COMPLETE"
            and all(not unit["missing_units"] for unit in r["evaluators"].values())
            and r["success_eligibility"] is not None
            and not r["success_eligibility"]["reason"].endswith("_UNDETERMINED")
            for r in (a, b)
        )
        outcome = "UNDETERMINED"
        if complete:
            success_a, success_b = (
                bool(r["success_eligibility"] and r["success_eligibility"]["eligible"])
                for r in (a, b)
            )
            if success_a != success_b:
                outcome = "IMPROVED" if success_b else "REGRESSED"
            else:
                outcome_keys = ("actual_final_status", "success_eligibility")
                labels = lambda r: {
                    name: unit["labels"] for name, unit in r["evaluators"].items()
                }
                outcome = (
                    "UNCHANGED"
                    if all(a[k] == b[k] for k in outcome_keys)
                    and labels(a) == labels(b)
                    else "CHANGED"
                )
        records.append(
            {
                "case_id": a["case_id"],
                "outcome_change": outcome,
                "v1": a,
                "v2": b,
                "measurement_deltas": {
                    key: _delta(
                        (a["system_metrics"] or {}).get(key),
                        (b["system_metrics"] or {}).get(key),
                    )
                    for key in ("latency_seconds", "model_calls", "tool_calls")
                }
                | {
                    key: _delta(
                        ((a["system_metrics"] or {}).get("token_usage") or {}).get(key),
                        ((b["system_metrics"] or {}).get("token_usage") or {}).get(key),
                    )
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                }
                | {
                    key: _delta(a[key], b[key])
                    for key in ("decision_attempts", "path_length")
                },
            }
        )
    return {
        "kind": "apilens-v1-v2-comparison",
        "cohort": cohort,
        "experiments": {
            label: {"experiment": summary["experiment"], "report_id": report_id}
            for label, summary, report_id in (
                ("v1", left, v1_report_id),
                ("v2", right, v2_report_id),
            )
        },
        "aggregates": {
            label: {key: summary[key] for key in AGGREGATES}
            for label, summary in (("v1", left), ("v2", right))
        },
        "cases": records,
        "outcome_changes": dict(Counter(r["outcome_change"] for r in records)),
        "strict_success_delta": (
            right["successful_runs"] - left["successful_runs"]
            if all(r["outcome_change"] != "UNDETERMINED" for r in records)
            else None
        ),
        "measurement_deltas": {
            name: _delta(
                left["measurements"][name]["total_all_cases"],
                right["measurements"][name]["total_all_cases"],
            )
            for name in left["measurements"]
        },
        "structured_errors": {"v1": _errors(left), "v2": _errors(right)},
        "limitations": [
            "Offline experiments measure plumbing only, not model quality.",
            "Outcome changes use existing strict eligibility, not a new quality score.",
            "Missing, interrupted, or evaluator-error cases have undetermined outcome changes.",
            "Convergence ratios remain within-experiment observed-cohort measurements, not a pooled comparison.",
            "One observation per case does not establish statistical reliability or causal efficacy.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("v1_directory", type=Path)
    parser.add_argument("v2_directory", type=Path)
    parser.add_argument("--v1-report-id", required=True)
    parser.add_argument("--v2-report-id", required=True)
    parser.add_argument("--v1-source-ref", required=True)
    parser.add_argument("--v2-source-ref")
    args = parser.parse_args()
    try:
        result = compare_saved(
            args.v1_directory,
            args.v1_report_id,
            args.v2_directory,
            args.v2_report_id,
            v1_source_ref=args.v1_source_ref,
            v2_source_ref=args.v2_source_ref,
        )
    except Exception as error:  # noqa: BLE001 -- raw artifacts and exception bodies stay private
        code = str(error) if isinstance(error, BaselineError) else type(error).__name__
        parser.exit(1, f"Comparison failed: {code}\n")
    print(json.dumps(result, indent=2))
    if not result["cohort"]["comparable"]:
        parser.exit(
            1, "Comparison blocked by runtime/dependency drift; see assessment.\n"
        )


if __name__ == "__main__":
    main()
