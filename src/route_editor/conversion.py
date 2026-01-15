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
        base_extras: Dict[str, Any] = {}
        try:
            if isinstance(getattr(s, "extras", None), dict) and s.extras:
                base_extras.update(dict(s.extras))
        except Exception:
            pass

        raw_line = ""
        try:
            raw_line = str(getattr(s, "raw_line", "") or "").strip()
        except Exception:
            raw_line = ""

        if k in {"label", "action", "call", "load"}:
            name = (s.name or "").strip()
            if not name:
                continue
            item_la: Dict[str, Any] = {"type": k, "name": name}
            # Back-compat with existing route loader/StepNavigator.
            if k == "label":
                item_la["label"] = name
            elif k == "action":
                item_la["action"] = name
            else:
                item_la[k] = name
            if raw_line:
                item_la["raw_line"] = raw_line
            if s.comment:
                item_la["comment"] = s.comment
            if base_extras:
                item_la.update(base_extras)
            items.append(item_la)
            continue

        if k == "cond":
            var_name = s.name.strip()
            label_true = str(s.params.get("label_true", "")).strip()
            label_false = str(s.params.get("label_false", "")).strip()
            if not var_name or not label_true or not label_false:
                continue
            item_cond: Dict[str, Any] = {
                "type": "cond",
                "conditional_jump": {
                    "var": var_name,
                    "label_true": label_true,
                    "label_false": label_false,
                },
            }
            if raw_line:
                item_cond["raw_line"] = raw_line
            if s.comment:
                item_cond["comment"] = s.comment
            if base_extras:
                item_cond.update(base_extras)
            items.append(item_cond)
            continue

        if k in {"node", "stand", "rope", "ladder"}:
            if s.x is None or s.y is None or s.z is None:
                continue
            item_xyz: Dict[str, Any] = {"type": k, "x": int(s.x), "y": int(s.y), "z": int(s.z)}
            if s.comment:
                item_xyz["comment"] = s.comment
            if raw_line:
                item_xyz["raw_line"] = raw_line
            if base_extras:
                item_xyz.update(base_extras)
            items.append(item_xyz)
            continue

        # Fallback: preserve as raw line.
        payload = (s.name or "").strip() or k or "custom"
        item_custom: Dict[str, Any] = {"type": "custom", "raw_line": payload}
        if base_extras:
            item_custom.update(base_extras)
        items.append(item_custom)

    return items


def route_items_to_waypoints(items: Iterable[Dict[str, Any]]) -> List[WaypointStep]:
    """Convert route.json dict items into WaypointSteps."""

    out: List[WaypointStep] = []
    for it in items:
        if not isinstance(it, dict):
            continue

        known_keys = {
            "type",
            "x",
            "y",
            "z",
            "name",
            "action",
            "label",
            "call",
            "load",
            "conditional_jump",
            "comment",
            "raw_line",
        }
        extras: Dict[str, Any] = {k: v for k, v in it.items() if k not in known_keys}
        raw_line = str(it.get("raw_line") or "").strip()
        comment = str(it.get("comment") or "")

        typ_top = str(it.get("type") or "").strip().lower()
        name_top = str(it.get("name") or "").strip()

        # Prefer explicit typed format if present.
        if typ_top in {"label", "action", "call", "load"}:
            name = name_top
            if not name:
                # Back-compat: allow legacy keys.
                name = str(it.get(typ_top) or "")
            out.append(WaypointStep(kind=typ_top, name=name, comment=comment, raw_line=raw_line, extras=extras))
            continue
        if typ_top == "cond":
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
                        comment=comment,
                        raw_line=raw_line,
                        extras=extras,
                    )
                )
                continue

        if "label" in it:
            out.append(WaypointStep(kind="label", name=str(it.get("label") or ""), comment=comment, raw_line=raw_line, extras=extras))
            continue
        if "action" in it:
            out.append(WaypointStep(kind="action", name=str(it.get("action") or ""), comment=comment, raw_line=raw_line, extras=extras))
            continue
        if "call" in it:
            out.append(WaypointStep(kind="call", name=str(it.get("call") or ""), comment=comment, raw_line=raw_line, extras=extras))
            continue
        if "load" in it:
            out.append(WaypointStep(kind="load", name=str(it.get("load") or ""), comment=comment, raw_line=raw_line, extras=extras))
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
                    comment=comment,
                    raw_line=raw_line,
                    extras=extras,
                )
            )
            continue

        typ = typ_top or str(it.get("type") or "").strip().lower()
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
                        comment=comment,
                        raw_line=raw_line,
                        extras=extras,
                    )
                )
            except Exception:
                out.append(WaypointStep(kind="custom", name=str(it.get("raw_line") or ""), comment=comment, raw_line=raw_line, extras=extras))
            continue

        # If coords exist but no type, treat it as a node.
        if x is not None and y is not None and z is not None:
            try:
                out.append(WaypointStep(kind="node", x=int(x), y=int(y), z=int(z), comment=comment, raw_line=raw_line, extras=extras))
                continue
            except Exception:
                pass

        raw = it.get("raw_line")
        if raw is not None:
            out.append(WaypointStep(kind="custom", name=str(raw), comment=comment, raw_line=raw_line, extras=extras))

    return out
