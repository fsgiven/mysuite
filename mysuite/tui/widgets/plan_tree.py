from __future__ import annotations

from textual.widgets import Tree

from mysuite.export.models import ExportPlan
from mysuite.export.units import Size


def _populate_file_node(node, plan: ExportPlan) -> None:
    by_format: dict[str, dict[str, list[Size]]] = {}
    for job in plan.jobs:
        by_format.setdefault(job.format, {}).setdefault(job.colorspace, []).append(job.size)

    for fmt in sorted(by_format):
        fmt_node = node.add(fmt, expand=True)
        for colorspace in sorted(by_format[fmt]):
            sizes = sorted(by_format[fmt][colorspace], key=lambda s: s.pixels)
            sizes_str = ", ".join(s.label for s in sizes)
            fmt_node.add_leaf(f"{colorspace}  ({sizes_str})")

    by_bundle_format: dict[str, dict[str, tuple]] = {}
    for bundle_job in plan.bundle_jobs:
        by_bundle_format.setdefault(bundle_job.format, {})[bundle_job.colorspace] = bundle_job.sizes

    for fmt in sorted(by_bundle_format):
        fmt_node = node.add(f"{fmt} (bundle — 1 file)", expand=True)
        for colorspace in sorted(by_bundle_format[fmt]):
            sizes = sorted(by_bundle_format[fmt][colorspace], key=lambda s: s.pixels)
            sizes_str = ", ".join(s.label for s in sizes)
            fmt_node.add_leaf(f"{colorspace}  (contains {sizes_str})")

    for skip in plan.skips:
        node.add_leaf(f"⚠ skipped {skip.format}/{skip.colorspace} — {skip.reason}")


def populate_plan_tree(tree: Tree, plans: list[tuple[str, ExportPlan]]) -> None:
    """plans is a list of (name, plan) pairs — one per input file. A single-file
    run just passes a one-element list."""
    tree.clear()

    if len(plans) == 1:
        name, plan = plans[0]
        tree.root.label = name
        _populate_file_node(tree.root, plan)
    else:
        tree.root.label = f"{len(plans)} files"
        for name, plan in plans:
            file_node = tree.root.add(name, expand=True)
            _populate_file_node(file_node, plan)

    tree.root.expand()
