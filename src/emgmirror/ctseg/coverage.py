"""Per-anatomy model coverage derived from the canonical catalog and task classes."""

from __future__ import annotations

from . import allowlist, ontology


def anatomy_coverage(plan_sources: list[str], runs: list[dict], present_ids: set[int], partial_ids: set[int], build: str) -> list[dict]:
    """Report what each catalog anatomy did without treating an omitted label as absent."""
    providers: dict[str, list[str]] = {}
    for source, classes in ontology.task_classes().items():
        for name in classes.values():
            providers.setdefault(name, []).append(source)

    run_status = {run["source"]: run["status"] for run in runs if "source" in run and "status" in run}
    out = []
    for anatomy_id, label in sorted(ontology.table().items(), key=lambda item: item[1].id):
        all_models = providers.get(anatomy_id, [])
        models = []
        for model in all_models:
            try:
                allowlist.allowed(model, build)
                models.append(model)
            except allowlist.LicenceError:
                pass
        scheduled = [model for model in models if model in plan_sources]
        completed = [model for model in scheduled if run_status.get(model) == "ok"]
        failed = [model for model in scheduled if run_status.get(model) == "failed"]
        unavailable = [model for model in scheduled if run_status.get(model) == "unavailable"]
        if label.id in present_ids:
            status = "partial" if label.id in partial_ids else "segmented"
        elif not models:
            status = "unavailable"
        elif failed:
            status = "failed"
        elif unavailable:
            status = "unavailable"
        elif completed:
            status = "not_detected"
        else:
            status = "not_scheduled"
        laterality = "left" if anatomy_id.endswith("_left") else "right" if anatomy_id.endswith("_right") else "not_applicable"
        out.append({
            "anatomy_id": anatomy_id,
            "label_id": label.id,
            "name": label.display,
            "laterality": laterality,
            "status": status,
            "capable_models": models,
            "unavailable_models": sorted(
                {model for model in all_models if model not in models} | set(unavailable)
            ),
            "scheduled_models": scheduled,
            "completed_models": completed,
            "failed_models": failed,
            # A missing segmentation does not prove the structure is outside the scan.
            # FOV assessment needs an explicit, validated anatomical locator.
            "field_of_view_status": "not_assessed",
        })
    return out
