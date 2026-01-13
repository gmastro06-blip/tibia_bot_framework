from __future__ import annotations

from typing import Iterable, List

from action.input_driver import ActionRequest


_SERVICE_KINDS = {"depot", "bank", "npc_trade", "supplies"}


def expand_service_requests(reqs: Iterable[ActionRequest]) -> List[ActionRequest]:
    """Decorate service actions with notes/beeps (assistant-only, no inputs).

    - Prepends a note for visibility (UI/telemetry) per service action.
    - Adds a beep only when the request is committed, to avoid spam.
    - Keeps original requests intact and order-stable otherwise.
    """

    out: List[ActionRequest] = []
    for r in reqs:
        if r.kind in _SERVICE_KINDS:
            label = f"{r.kind}:{r.value}" if r.value else r.kind
            out.append(ActionRequest(kind="note", value=label, note=r.note))
            if r.note == "committed":
                out.append(ActionRequest(kind="beep", value="service_flow", note=r.note))
            out.append(r)
        else:
            out.append(r)
    return out
