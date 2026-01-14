from __future__ import annotations

from typing import Any, Dict, Iterable, List

from route_editor.models import WaypointStep


def waypoints_to_route_items(steps: Iterable[WaypointStep]) -> List[Dict[str, Any]]:
    """Convert WaypointSteps (waypoints.in) into route.json item dicts.

    Notes:
    - MOVE steps should be expanded before calling this.
    - Unknown/custom steps are preserved as {"raw_line": ...}.
    """

    items: List[Dict[str, Any]] = []
    for s in steps:
        k = (s.kind or "").strip().lower()
        if k in {"label", "action", "call", "load"}:
            if not s.name.strip():
                continue
            items.append({k: s.name.strip()})
            continue

        if k == "cond":
            var_name = s.name.strip()
            label_true = str(s.params.get("label_true", "")).strip()
            label_false = str(s.params.get("label_false", "")).strip()
            if not var_name or not label_true or not label_false:
                continue
            items.append(
                {
                    "conditional_jump": {
                        "var": var_name,
                        "label_true": label_true,
                        "label_false": label_false,
                    }
                }
            )
            continue

        if k in {"node", "stand", "rope", "ladder"}:
            if s.x is None or s.y is None or s.z is None:
                continue
            d: Dict[str, Any] = {"type": k, "x": int(s.x), "y": int(s.y), "z": int(s.z)}
            if s.comment:
                d["comment"] = s.comment
            items.append(d)
            continue

        # Fallback: preserve as raw line.
        payload = s.name.strip() or k or "custom"
        items.append({"raw_line": payload})

    return items


def route_items_to_waypoints(items: Iterable[Dict[str, Any]]) -> List[WaypointStep]:
    """Convert route.json dict items into WaypointSteps."""

    out: List[WaypointStep] = []
    for it in items:
        if not isinstance(it, dict):
            continue

        if "label" in it:
            out.append(WaypointStep(kind="label", name=str(it.get("label") or "")))
            continue
        if "action" in it:
            out.append(WaypointStep(kind="action", name=str(it.get("action") or "")))
            continue
        if "call" in it:
            out.append(WaypointStep(kind="call", name=str(it.get("call") or "")))
            continue
        if "load" in it:
            out.append(WaypointStep(kind="load", name=str(it.get("load") or "")))
            continue

        cj = it.get("conditional_jump")
        if isinstance(cj, dict):
            var_name = str(cj.get("var") or "").strip()
            label_true = str(cj.get("label_true") or "").strip()
            label_false = str(cj.get("label_false") or "").strip()
            out.append(
                WaypointStep(
                    kind="cond",
                    name=var_name,
                    params={"label_true": label_true, "label_false": label_false},
                )
            )
            continue

        typ = str(it.get("type") or "").strip().lower()
        x = it.get("x")
        y = it.get("y")
        z = it.get("z")

        if typ in {"node", "stand", "rope", "ladder"}:
            try:
                out.append(
                    WaypointStep(
                        kind=typ,
                        x=int(x) if x is not None else None,
                        y=int(y) if y is not None else None,
                        z=int(z) if z is not None else None,
                        comment=str(it.get("comment") or ""),
                    )
                )
            except Exception:
                out.append(WaypointStep(kind="custom", name=str(it.get("raw_line") or "")))
            continue

        # If coords exist but no type, treat it as a node.
        if x is not None and y is not None and z is not None:
            try:
                out.append(WaypointStep(kind="node", x=int(x), y=int(y), z=int(z)))
                continue
            except Exception:
                pass

        raw = it.get("raw_line")
        if raw is not None:
            out.append(WaypointStep(kind="custom", name=str(raw)))

    return out
