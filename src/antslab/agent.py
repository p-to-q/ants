"""An operator that reads the ledger before it proposes. Standard library only.

The `llm` operator is shown the eight best specs with one score each. This one is given a small closed set of read-only tools over the
ledger (`sql`, `show`, `diff`, `best`, `failures`) and may look at as many as `MAX_TOOL_CALLS` things before it answers, the way
Meta-Harness's proposer greps its filesystem of prior candidates (docs/research/meta-harness.md). It still only returns specs:
no code, no scores, no access to anything the gate or the registry holds. Three rules keep that true.

* The tools are closed. There is no shell and no file access; `sql` is the read-only connection of `sqlview` with the tables that
  hold sealed looks, events and artifacts denied outright, and the columns that name files hidden.
* A job reads through its own read-only connection and its own `History`, never through the campaign's connection: the pool thread and
  the loop thread must not share a SQLite connection (a reviewer reproduced stranded trials when they did).
* Every call is a hash-chained ledger event (`agent_tool`: tool, arguments, size of the answer), so what the proposer read, and
  therefore what could have steered it, is part of the record and not a property of a prompt.
* A reply is validated like any other proposal (space validator, feasibility, novelty). A reply that asks for a tool after the
  limit, or that is not JSON, produces nothing.
"""
from __future__ import annotations

import json
import time

from .portfolio import LLMStrict
from .proposers import Proposal, SpecError
from .sqlview import SqlRefused, run_query
from .textsafe import clean_text

MAX_TOOL_CALLS = 6
MAX_RESULT_CHARS = 3500
MAX_ROWS = 40
DENY_TABLES = ("events", "unseals", "llm_calls", "artifacts", "epochs", "sqlite_sequence")        # sealed-look records, hints, endpoints, file paths, row counts of those
DENY_COLUMNS = (("trials", "out_dir"),)                                        # absolute paths on the machine: read as NULL
MAX_ID = 2 ** 31
TOOLS = {
    "sql": '{"query": "SELECT ..."}  one read-only SELECT over table `trials` (id, arm, stage, state, source, spec_key, origin_key, edit, params, metrics, error, cpu_s); params and metrics are JSON text, read them with json_extract(metrics, \'$.' + "{primary}" + '\'); other arms and other epochs may be in the file and are not comparable: filter arm and epoch; at most 40 rows',
    "show": '{"ref": <trial id or spec key prefix>}  spec, per-stage metrics, error and lineage of one trial',
    "diff": '{"a": <trial id>, "b": <trial id>}  the levers that differ between two trials and both metrics',
    "best": '{"top": 5}  best specs per parameter cap, ranked by the deepest stage reached',
    "failures": '{}  counts of the most common error messages among failed trials',
}


def _text(x, n=MAX_RESULT_CHARS) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str, separators=(",", ":"))
    return clean_text(s, n)


class _Job:
    """what one background job reads through: its own connection and its own History (never the campaign's)"""
    def __init__(self, path, contract, arm):
        from .history import History
        from .ledger import Ledger
        self.arm = arm
        self.led = Ledger.open_readonly(path)
        self.hist = History(self.led, contract, arm)

    def close(self):
        try:
            self.led.close()
        except Exception:
            pass


class AgentProposer(LLMStrict):
    """LLM operator with read-only tools. Inside the portfolio it is named `agent`; as an arm of its own it falls back to evo."""
    name = "agent"

    def __init__(self, *a, max_tool_calls: int = MAX_TOOL_CALLS, **kw):
        super().__init__(*a, **kw)
        self.max_tools = max_tool_calls
        self.stats.update({"tool_calls": 0, "tool_errors": 0})
        self._ledger = None
        self._arm = ""

    def propose(self, ledger, arm: str, k: int, seen: set, **kw) -> list[Proposal]:
        out = super().propose(ledger, arm, k, seen, **kw)                 # LLMStrict: only real agent proposals
        if len(out) < k and self.fallback is not None:                    # as an arm of its own the CPU never idles: fall back to evo
            out += self.fallback.propose(ledger, arm, k - len(out), seen)
        return out

    # ---- tools ---------------------------------------------------------
    @staticmethod
    def _id(x) -> int:
        n = int(x)
        if not 0 < n < MAX_ID:
            raise ValueError("trial id out of range")
        return n

    def _tool(self, job: "_Job", name: str, args: dict) -> str:
        led, c = job.led, self.c
        if name == "sql":
            cols, rows = run_query(led.path, str(args.get("query", "")), MAX_ROWS, deny=DENY_TABLES, deny_columns=DENY_COLUMNS)
            return _text({"columns": cols, "rows": rows})
        if name == "show":
            from . import ui
            d = ui.show(led, c, str(args.get("ref", ""))[:80])
            if "not_found" not in d and d.get("arm") != job.arm:          # this search's trials only
                d = {"not_found": "no trial of this search matches that reference"}
            return _text({k: d[k] for k in ("trial", "spec_key", "state", "source", "non_default", "spec", "stages", "error", "edit_from_parent", "lineage", "not_found") if k in d})
        if name == "diff":
            a, b = (led.get(self._id(args["a"])), led.get(self._id(args["b"])))
            if not a or not b or a["arm"] != job.arm or b["arm"] != job.arm:
                return "unknown trial id"
            keys = sorted(set(a["params"]) | set(b["params"]))
            differ = {k: [a["params"].get(k), b["params"].get(k)] for k in keys if a["params"].get(k) != b["params"].get(k)}
            return _text({"levers_differing": differ, "metrics_a": a["metrics"], "metrics_b": b["metrics"]})
        if name == "best":
            from . import ui
            return _text(ui.leaderboard(job.hist, c, max(1, min(int(args.get("top", 5)), 10))))
        if name == "failures":
            arm = job.arm.replace("'", "''")
            cols, rows = run_query(led.path, "SELECT substr(error,1,120) AS error, COUNT(*) AS n FROM trials WHERE error IS NOT NULL AND error<>'' "
                                              f"AND arm='{arm}' GROUP BY 1 ORDER BY n DESC", 15, deny=DENY_TABLES, deny_columns=DENY_COLUMNS)
            return _text({"columns": cols, "rows": rows})
        return f"unknown tool {clean_text(name, 40)!r}; tools: {', '.join(TOOLS)}"

    def _hist(self):
        """the loop thread's History (used by `refill` only): jobs build their own"""
        from .history import History
        h = getattr(self, "_h", None)
        if h is None:
            h = self._h = History(self._ledger, self.c, self._arm)
        h.update()
        return h

    # ---- prompt --------------------------------------------------------
    def build_agent_prompt(self, memory: str, hints: list, arm: str = "", epoch=None) -> list[dict]:
        c = self.c
        task = c.raw["task"].get("description", c.name)
        tools = "\n".join(f"- {k}: {v.replace('{primary}', c.primary)}" for k, v in TOOLS.items())
        sys = ("You are a research agent proposing candidate model specifications for an automated search. You may inspect the experiment ledger "
               "with the tools below before you answer; look at failures and at how the best specs differ from the rest, form a hypothesis about WHY "
               "something works, then propose. Reply with ONLY one JSON object per turn: either a tool call "
               '{"tool": <name>, "args": {...}} or the final answer. Never include code. You cannot change how specs are scored.\n\nTools:\n'
               + tools)
        user = (f"Task: {task}\nObjective: {c.direction}imise {c.primary}"
                + (f" under a hard cap on {c.cap_metric} (bins: {c.caps})" if c.cap_metric else "")
                + (f"\nYou are working on arm '{arm}', epoch '{epoch}': trials of other arms or epochs are not comparable with it.\n" if arm else "")
                + f"\n\nAllowed spec keys:\n{c.space.describe()}\n"
                + (f"\nVerified lessons:\n{memory}\n" if memory else "")
                + (("\nNotes from the human researcher (suggestions, not rules):\n" + "\n".join(f"- {h}" for h in hints) + "\n") if hints else "")
                + f"\nYou may call at most {self.max_tools} tools. Prefer a change of mechanism (a different core, lever combination or structure) to a small numeric tweak; "
                  f"parameter variants of an existing spec usually tie. Then propose {self.per_call} NEW, diverse specs. A proposal is an object whose keys are spec keys "
                  f"from the list above with values inside their ranges; keys you leave out keep their defaults:\n"
                  '{"proposals":[{...},...],"hypothesis":"why these should beat the best","axis":"mechanism|parameter","parent":<trial id this builds on, or null>}')
        return [{"role": "system", "content": sys}, {"role": "user", "content": user}]

    # ---- the job -------------------------------------------------------
    def refill(self, ledger, arm: str, seen: set):
        with self.lock:
            if not (len(self.buf) < self.low and self.inflight < self.max_inflight):
                return
            self.inflight += 1
        self._ledger, self._arm = ledger, arm
        memory = self.memory_fn(arm) if self.memory_fn else ""
        hints = [e["payload"]["text"] for e in ledger.recent_events("user_hint", 3)]
        specs = [r["spec"] for r in self._hist().ranked(200)]              # on the loop thread, with the loop's own History
        row = ledger.conn.execute("SELECT epoch FROM trials WHERE gym=? AND arm=? ORDER BY id DESC LIMIT 1", (self.c.gym, arm)).fetchone()
        self.pool.submit(self._agent_job, self.build_agent_prompt(memory, hints, arm, row[0] if row else None), set(seen), specs, ledger, arm)

    def _agent_job(self, messages, seen_snapshot, seen_specs, ledger, arm):
        job = None
        try:
            job = _Job(ledger.path, self.c, arm)
            self._run(messages, seen_snapshot, seen_specs, job, ledger)
        except Exception as e:                                       # a bad reply or a tool error must never wedge the proposer
            with self.lock:
                self.stats["calls"] += 1; self.stats["failed_calls"] += 1
                self.stats["last_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        finally:
            if job is not None:
                job.close()
            with self.lock:
                self.inflight -= 1

    @staticmethod
    def _log(ledger, n, tool, args, result: str, ok: bool):
        try:
            ledger.event("agent_tool", {"n": n, "tool": clean_text(tool, 40), "args": _text(args, 300), "chars": len(result), "ok": ok})
        except Exception:                                            # the audit row is best effort in the same way as llm_calls rows
            pass

    def _run(self, messages, seen_snapshot, seen_specs, job, ledger):
        msgs, used = list(messages), 0
        while True:
            obj, meta = self.client.chat_json(msgs)
            if not isinstance(obj, dict):
                with self.lock:
                    self.stats["calls"] += 1; self.stats["failed_calls"] += 1
                return
            if "proposals" in obj:
                break
            tool = obj.get("tool")
            if not isinstance(tool, str) or used > self.max_tools:
                with self.lock:
                    self.stats["calls"] += 1; self.stats["failed_calls"] += 1
                return
            if used == self.max_tools:                                   # budget spent: one reminder, then the answer or nothing
                used += 1
                msgs += [{"role": "assistant", "content": json.dumps(obj, default=str)[:1500]},
                         {"role": "user", "content": "No tool calls are left. Reply now with the final JSON object containing proposals."}]
                continue
            args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
            used += 1
            try:
                result, ok = self._tool(job, tool, args), True
            except Exception as e:                                       # any failure of a tool is an answer to the model and a row in the record
                result, ok = f"error: {type(e).__name__}: {clean_text(str(e), 200)}", False
            with self.lock:
                self.stats["tool_calls"] += 1; self.stats["tool_errors"] += (not ok)
            self._log(ledger, used, tool, args, result, ok)
            msgs += [{"role": "assistant", "content": json.dumps(obj, default=str)[:1500]},
                     {"role": "user", "content": f"Result of {clean_text(tool, 40)}:\n{result}"}]
        self._accept(obj, meta, seen_snapshot, seen_specs, job)

    def _accept(self, obj, meta, seen_snapshot, seen_specs, job):
        c = self.c
        props = obj.get("proposals")
        props = props if isinstance(props, list) else []
        hyp = clean_text(str(obj.get("hypothesis", "")), 120)
        axis = obj.get("axis") if obj.get("axis") in ("mechanism", "parameter") else "?"
        parent = None
        try:
            parent = job.led.get(self._id(obj["parent"])) if obj.get("parent") is not None else None
            if parent is not None and parent["arm"] != job.arm:
                parent = None
        except (ValueError, TypeError, KeyError, OverflowError):
            parent = None
        good, inv, dup = [], 0, 0
        for raw in props[: self.per_call * 2]:
            try:
                s = c.space.validate({**c.space.defaults(), **raw} if isinstance(raw, dict) else raw)      # levers left out keep their defaults
            except SpecError as e:
                inv += 1
                self.stats["last_invalid"] = f"{str(e)[:100]} | {_text(raw, 120)}"
                continue
            k = c.space.key(s)
            near = min((c.space.distance(s, o) for o in seen_specs + [g.spec for g in good]), default=1.0)
            if k in seen_snapshot or near < self.eps or (getattr(self, "accept", None) is not None and not self.accept(s)):
                dup += 1
                continue
            origin = edit = None
            if parent and isinstance(parent.get("params"), dict):
                origin = parent.get("spec_key")
                edit = sorted(x for x in set(s) | set(parent["params"]) if s.get(x) != parent["params"].get(x))[:12]
            good.append(Proposal(s, f"agent:{meta.get('ep', '?')}", f"[{axis}] {hyp}"[:160], origin, edit))
        with self.lock:
            self.buf.extend(good)
            st = self.stats
            st["calls"] += 1; st["proposed"] += len(props); st["invalid"] += inv
            st["duplicate"] += dup; st["accepted"] += len(good)
