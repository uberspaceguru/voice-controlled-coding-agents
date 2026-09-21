"""Validate and describe a read-only tmux inventory, never dispatch targets.

Pane liveness, a verified agent identity, and voice enrollment are different
facts. A process-looking command alone is not evidence of an agent identity.
"""

import math
from copy import deepcopy
from dataclasses import dataclass

from spoken import spoken


class FleetReadError(RuntimeError):
    """No authoritative current fleet snapshot could be read."""


@dataclass(frozen=True)
class FleetSnapshot:
    snapshot_id: str
    captured_at: float
    panes: tuple[dict, ...]
    failed_servers: int
    warnings: tuple[str, ...]

    @property
    def partial(self) -> bool:
        return bool(self.failed_servers or self.warnings)


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _strings(value):
    return isinstance(value, list) and all(_string(item) for item in value)


def parse_fleet(data: object) -> FleetSnapshot:
    """Reject malformed/failed scans instead of turning them into empty fleets."""
    def fail():
        raise FleetReadError("Tmux fleet snapshot unavailable")

    if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int:
        fail()
    if data["schemaVersion"] != 1 or not _string(data.get("snapshotId")):
        fail()
    captured = data.get("capturedAt")
    if (type(captured) not in (int, float) or not math.isfinite(captured) or captured <= 0):
        fail()
    servers, panes = data.get("servers"), data.get("panes")
    warnings = data.get("warnings", [])
    if not isinstance(servers, list) or not isinstance(panes, list) or not _strings(warnings):
        fail()
    scanned = {}
    for server in servers:
        if not isinstance(server, dict) or not _string(server.get("socketPath")):
            fail()
        socket, status = server["socketPath"], server.get("status")
        if socket in scanned or status not in {"ok", "error", "unavailable", "skipped"}:
            fail()
        scanned[socket] = status
    failed = sum(status != "ok" for status in scanned.values())
    if scanned and failed == len(scanned):
        fail()
    if not scanned and warnings:
        fail()
    seen = set()
    clean = []
    for pane in panes:
        if not isinstance(pane, dict):
            fail()
        if any(not _string(pane.get(key)) for key in
               ("id", "socketPath", "sessionName", "windowId", "paneId")):
            fail()
        if (not isinstance(pane.get("windowName"), str)
                or not isinstance(pane.get("command"), str)
                or type(pane.get("dead")) is not bool
                or not _integer(pane.get("pid"))
                or not _integer(pane.get("attachedClientCount"))
                or not _strings(pane.get("candidateHarnesses"))):
            fail()
        socket = pane["socketPath"]
        if scanned.get(socket) != "ok":
            fail()
        # A pane may appear in linked windows; one physical pane is one count.
        identity = (socket, pane["paneId"])
        status, agents = pane.get("identityStatus"), pane.get("agents")
        if status not in {"verified", "unresolved", "none"} or not isinstance(agents, list):
            fail()
        if (status == "verified") != bool(agents):
            fail()
        if status == "none" and pane["candidateHarnesses"]:
            fail()
        for agent in agents:
            if not isinstance(agent, dict):
                fail()
            if (not _string(agent.get("sessionId")) or not _string(agent.get("harness"))
                    or not _integer(agent.get("pid"), 1)
                    or not _strings(agent.get("identityEvidence"))
                    or not agent["identityEvidence"]):
                fail()
            if any(agent.get(key) is not None and not isinstance(agent[key], str)
                   for key in ("name", "status")):
                fail()
        if identity not in seen:
            seen.add(identity)
            clean.append(deepcopy(pane))
    return FleetSnapshot(data["snapshotId"], float(captured), tuple(clean), failed, tuple(warnings))


def fleet_speech(snapshot: FleetSnapshot, targets: list[dict], *, include_names=True) -> list[str]:
    """Deterministic, bounded statements; target metadata cannot invent tmux agents."""
    known = {row["sessionId"]: row for row in targets}
    agents = {}
    locations = {}
    live = [pane for pane in snapshot.panes if not pane["dead"]]
    for pane in live:
        for agent in pane["agents"]:
            agents.setdefault(agent["sessionId"], agent)
            locations.setdefault(agent["sessionId"], pane)
    count = len(agents)
    lines = []
    if snapshot.partial:
        lines.append("The tmux inventory is partial; some sources could not be checked.")
    lines.append(f"I can see {count} live agent{'s' if count != 1 else ''} with verified identities in tmux.")
    if agents:
        status = [(known.get(sid, {}).get("status") or agent.get("status"))
                  for sid, agent in agents.items()]
        busy, idle, waiting = (status.count(value) for value in ("busy", "idle", "waiting"))
        unknown = count - busy - idle - waiting
        enrolled = sum(known.get(sid, {}).get("enrolled") is True for sid in agents)
        lines.append(f"Activity reports: {busy} busy, {idle} idle, {waiting} waiting, {unknown} unknown.")
        lines.append(f"{enrolled} enrolled for voice replies.")
    shell = [pane for pane in live if pane["identityStatus"] == "none"]
    unresolved = [pane for pane in live if pane["identityStatus"] == "unresolved"]
    ended = len(snapshot.panes) - len(live)
    lines.append(f"Tmux has {len(live)} live pane{'s' if len(live) != 1 else ''}: "
                 f"{len(shell)} ordinary shell pane{'s' if len(shell) != 1 else ''} and "
                 f"{len(unresolved)} unidentified pane{'s' if len(unresolved) != 1 else ''}, "
                 "separate from the verified agents.")
    if ended:
        lines.append(f"{ended} ended pane{'s are' if ended != 1 else ' is'} excluded from live counts.")
    # A known target missing from this snapshot might be inside an unresolved
    # pane. Do not add it to a total, or claim that it is outside tmux.
    missing = len(known.keys() - agents.keys())
    if missing:
        lines.append(f"{missing} other known live agent{'s are' if missing != 1 else ' is'} "
                     "not identified in this tmux inventory.")
    if include_names:
        for number, (sid, agent) in enumerate(agents.items(), 1):
            metadata = known.get(sid, {})
            raw = agent.get("name") or metadata.get("name") or metadata.get("project")
            if raw:
                label = spoken(raw, max_words=10)
            else:
                # A tmux location is observed evidence, not a minted agent title.
                pane = locations[sid]
                harness = spoken(agent["harness"], max_words=3)
                session = spoken(pane["sessionName"], max_words=10)
                window = spoken(pane["windowName"] or "unnamed", max_words=10)
                pane_id = spoken(pane["paneId"].removeprefix("%"), max_words=4)
                label = f"{harness} in session {session}, window {window}, pane {pane_id}"
            lines.append(f"Agent {number}: {label}.")
        for kind, rows in (("Unidentified pane", unresolved), ("Shell pane", shell)):
            for number, pane in enumerate(rows, 1):
                session = spoken(pane["sessionName"], max_words=10)
                window = spoken(pane["windowName"] or "unnamed", max_words=10)
                lines.append(f"{kind} {number}: session {session}, window {window}.")
    chunks = []
    chunk = ""
    for line in lines:
        if chunk and len((chunk + " " + line).split()) > 70:
            chunks.append(chunk)
            chunk = ""
        chunk = (chunk + " " + line).strip()
    if chunk:
        chunks.append(chunk)
    return chunks
