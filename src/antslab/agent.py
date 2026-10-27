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

Tool generations:
  v1 (default) = sql, show, diff, best, failures — the original closed set.
  v2 = v1 + check, neighbors, effects, predict, scorecard — richer read-only tools over the arm's
       own cache, lineage and surrogate.  MAX_TOOL_CALLS rises to 8: the new tools answer in one
       call what v1 needed two or three SQL queries for, so fewer round-trips, but the agent can
       now validate and predict before committing, which earns the extra budget.
"""
from __future__ import annotations

import json
import math
from statistics import median
import time

from .portfolio import LLMStrict
from .proposers import Proposal, SpecError
from .sqlview import SqlRefused, run_query
from .textsafe import clean_text

MAX_TOOL_CALLS = 6
MAX_TOOL_CALLS_V2 = 8           # v2 tools answer in one call what v1 needed multiple SQL queries for; see module docstring
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

TOOLS_V2 = {
    **TOOLS,
    "check": '{"spec": {...}}  validate a candidate exactly as the campaign would, without enqueueing: '
             'space validation, parameter count against caps (static_cost), memory against mem_cap_mb, '
             'cap bin, whether the exact key or a near-duplicate was already evaluated (with metrics), '
             'and the 3 nearest evaluated specs by Encoder distance with per-stage metrics. '
             'Use it to fix an invalid or duplicate proposal before answering.',
    "neighbors": '{"ref": <trial id or spec key prefix>, "k": 5}  the k nearest evaluated specs '
                 'to an existing trial, by Encoder distance, with metrics and the levers that differ',
    "effects": '{"lever": "<optional lever name>", "cap": <optional cap>}  a deterministic mutation-effect table '
               'from the ledger lineage columns: for each lever and (from-bin -> to-bin), n pairs, mean and median '
               'change in ' + "{primary}" + ' at the first and deepest stage reached by both, and how many improved. '
               'Counts only; observational (operators choose which levers to change). Top 25 rows by count.',
    "predict": '{"spec": {...}}  the surrogate predicted deepest-stage value and spread for a candidate, '
               'using ExtraTrees fitted on finished trials; returns "no model yet" with the row count when fewer than 150 rows exist',
    "scorecard": '{}  your track record under protocol v2: for your recorded expectations, the share that came true '
                 'by (lever, direction), counted by deterministic comparison of child value with parent value. '
                 'Only expectations whose parent and child are both evaluated count.',
}

# ---- v2 prompt rules, stated plainly as a constant for testing ----
RULES_V2 = (
    "Rules (enforced mechanically, not by trust):\n"
    "- Caps, memory limits and spec validation are checked by code. An invalid spec is rejected; use `check` first.\n"
    "- Exact duplicates and near-duplicates (Encoder distance < 0.03) are rejected.\n"
    "- You may call at most {max_tools} tools.\n"
    "- The answer must be a single JSON object with a `proposals` list.\n"
    "- Your statements are not evidence: only deterministic code over ledger rows sets status.\n"
    "- The first stage (s0) is a coarse ranker (rho ~0.85 over an unselected sample). "
    "Do not over-trust a single first-stage value; it keeps about half the true top quartile.\n"
    "- Stage s2 repeats s1 (rho >0.99); do not treat s2 as independent confirmation.\n"
    "- Each proposal may carry `parent` (trial id), `expect` (better/worse/same vs parent), "
    "and `why` (<=160 chars). `parent` must be a real trial in this arm. "
    "`expect` and `why` are recorded but never used by scoring code.\n"
    "- Use `check` to verify a candidate is valid and novel before proposing it."
)


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
        self.hist = History.for_arm(self.led, contract, arm)

    def close(self):
        try:
            self.led.close()
        except Exception:
            pass


# ---- v2 surrogate cache: one ExtraTrees per job, refit when enough new rows ----
_PREDICT_MIN_ROWS = 150
_PREDICT_REFIT_DELTA = 100


class _SurrogateCache:
    """Per-job surrogate fitted lazily from deepest-stage trials.  Cached; refit when _PREDICT_REFIT_DELTA new rows."""
    def __init__(self):
        self.model = None
        self.encoder = None
        self.n_at_fit = 0

    def predict(self, job, contract, spec):
        """(mean, spread) or ('no model yet', n_rows).  numpy imported lazily."""
        from .screen import Encoder, ExtraTrees
        try:
            import numpy as np
        except ImportError:
            return "numpy is not installed", 0

        hist = job.hist
        hist.update()

        # gather deepest-stage finished trials with first-stage metrics
        s0 = contract.stages[0]
        deep_idx = len(contract.stages) - 1
        rows_x, rows_y = [], []
        if self.encoder is None:
            self.encoder = Encoder(contract.space)
        enc = self.encoder

        for key, rec in hist.best.items():
            if rec["stage_idx"] != deep_idx:
                continue
            # first-stage metrics for this spec
            first = hist.first.get(key)
            s0_metrics = first["metrics"] if first else {}
            v = rec["value"]
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            rows_x.append(enc.vector(rec["spec"], s0_metrics))
            rows_y.append(float(v))

        n = len(rows_y)
        if n < _PREDICT_MIN_ROWS:
            return "no model yet", n

        # refit when enough new rows since last fit
        if self.model is None or n - self.n_at_fit >= _PREDICT_REFIT_DELTA:
            X = np.array(rows_x)
            y = np.array(rows_y)
            self.model = ExtraTrees(n_trees=40, seed=42).fit(X, y)
            self.n_at_fit = n

        # the candidate's first-stage metrics: use them if the candidate has a first-stage result,
        # otherwise use -1.0 (the same neutral value Encoder uses for missing/inactive parameters)
        first = hist.first.get(contract.space.key(spec))
        s0_m = first["metrics"] if first else {}
        xv = enc.vector(spec, s0_m)
        mu, sd = self.model.predict(np.atleast_2d(xv))
        return float(mu[0]), float(sd[0])


class AgentProposer(LLMStrict):
    """LLM operator with read-only tools. Inside the portfolio it is named `agent`; as an arm of its own it falls back to evo."""
    name = "agent"

    def __init__(self, *a, max_tool_calls: int = MAX_TOOL_CALLS, tools: str = "v1", **kw):
        if tools not in ("v1", "v2"):
            raise ValueError("tools must be v1 or v2")
        if tools == "v2":
            max_tool_calls = MAX_TOOL_CALLS_V2
        super().__init__(*a, **kw)
        self.max_tools = max_tool_calls
        self.tools_gen = tools
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

    def _active_tools(self) -> dict:
        return TOOLS_V2 if self.tools_gen == "v2" else TOOLS

    def _tool(self, job: "_Job", name: str, args: dict, surrogate=None) -> str:
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
            rows = ui.leaderboard(job.hist, c, max(1, min(int(args.get("top", 5)), 10)))
            if self.tools_gen == "v1":
                rows = {cap: [{k: v for k, v in row.items() if k not in ("val_mse", "rel_mae", "primary")}
                              for row in rs] for cap, rs in rows.items()}
            return _text(rows)
        if name == "failures":
            arm = job.arm.replace("'", "''")
            cols, rows = run_query(led.path, "SELECT substr(error,1,120) AS error, COUNT(*) AS n FROM trials WHERE error IS NOT NULL AND error<>'' "
                                              f"AND arm='{arm}' GROUP BY 1 ORDER BY n DESC", 15, deny=DENY_TABLES, deny_columns=DENY_COLUMNS)
            return _text({"columns": cols, "rows": rows})
        # ---- v2 tools ----
        if self.tools_gen != "v2":
            return f"unknown tool {clean_text(name, 40)!r}; tools: {', '.join(self._active_tools())}"
        if name == "check":
            return self._tool_check(job, args)
        if name == "neighbors":
            return self._tool_neighbors(job, args)
        if name == "effects":
            return self._tool_effects(job, args)
        if name == "predict":
            return self._tool_predict(job, args, surrogate)
        if name == "scorecard":
            return self._tool_scorecard(job)
        return f"unknown tool {clean_text(name, 40)!r}; tools: {', '.join(self._active_tools())}"

    # ---- v2: check ----
    def _tool_check(self, job, args) -> str:
        c = self.c
        raw_spec = args.get("spec")
        if not isinstance(raw_spec, dict):
            return "error: spec must be a JSON object"
        # 1. space validation
        try:
            spec = c.space.validate({**c.space.defaults(), **raw_spec})
        except SpecError as e:
            return _text({"valid": False, "error": str(e)})
        # 2. static cost (parameter count against caps)
        from .contract import load_static_cost
        cost_fn = load_static_cost(c.static_cost)
        cost_val = cost_fn(spec) if cost_fn else None
        cap_bin = None
        cost_over = False
        if cost_val is not None and c.caps:
            cap_bin = next((b for b in sorted(c.caps) if cost_val <= b), None)
            cost_over = cap_bin is None
        # 3. memory estimate
        mem_fn = load_static_cost(c.static_mem)
        mem_val = mem_fn(spec) if mem_fn else None
        mem_over = mem_val is not None and mem_val > c.mem_cap_mb
        # 4. duplicate / near-duplicate check
        key = c.space.key(spec)
        job.hist.update()
        exact = job.hist.best.get(key)
        exact_info = None
        if exact:
            exact_info = {"spec_key": exact["key"], "stage_idx": exact["stage_idx"],
                          "value": exact["value"], "metrics": exact["metrics"]}
        # near-duplicate: same logic as the proposer's novelty filter
        nearest_3 = self._nearest_specs(job, spec, 3)
        return _text({
            "valid": True, "static_cost": cost_val, "cap_bin": cap_bin, "cost_over_cap": cost_over,
            "mem_mb": mem_val, "mem_over_cap": mem_over,
            "already_evaluated": exact_info,
            "nearest_evaluated": nearest_3,
        })

    # ---- v2: neighbors ----
    def _tool_neighbors(self, job, args) -> str:
        c = self.c
        ref = str(args.get("ref", ""))[:80]
        try:
            k = max(1, min(int(args.get("k", 5)), 20))
        except (TypeError, ValueError, OverflowError):
            return _text({"error": "k must be a number between 1 and 20"})
        # resolve ref to a spec
        from . import ui
        d = ui.show(job.led, c, ref)
        if "not_found" in d:
            return _text({"error": "trial not found"})
        if d.get("arm") != job.arm:
            return _text({"error": "trial belongs to a different arm"})
        target_spec = d.get("spec", {})
        return _text({"neighbors": self._nearest_specs(job, target_spec, k)})

    def _nearest_specs(self, job, target, k) -> list:
        """k nearest evaluated specs to target, by Encoder distance, with metrics and differing levers."""
        c = self.c
        job.hist.update()
        ranked = []
        for key, rec in job.hist.best.items():
            dist = c.space.distance(target, rec["spec"])
            ranked.append((dist, rec))
        ranked.sort(key=lambda x: x[0])
        out = []
        for dist, rec in ranked[:k]:
            diff_levers = c.space.changed(target, rec["spec"])
            out.append({
                "spec_key": rec["key"], "distance": round(dist, 4),
                "stage_idx": rec["stage_idx"], "value": rec["value"],
                "metrics": rec["metrics"], "levers_differing": diff_levers,
            })
        return out

    # ---- v2: effects ----
    def _tool_effects(self, job, args) -> str:
        c = self.c
        lever_filter = args.get("lever")
        if lever_filter is not None:
            lever_filter = str(lever_filter)[:64]
            if lever_filter not in c.space.params:
                return _text({"error": f"unknown lever {lever_filter!r}"})
        cap_filter = args.get("cap")
        if cap_filter is not None:
            try:
                cap_filter = float(cap_filter)
                if not math.isfinite(cap_filter):
                    raise ValueError("non-finite cap")
            except (TypeError, ValueError):
                return _text({"error": "cap must be a number"})
        job.hist.update()
        # build the effect table from lineage: each child with origin_key and edit
        buckets: dict = {}   # (lever, from_bin, to_bin) -> list of (delta_s0, delta_deep)
        hist = job.hist
        for rec in hist.order:
            if not rec.get("origin_key") or not rec.get("edit"):
                continue
            parent = hist.first.get(rec["origin_key"])
            if parent is None:
                continue
            if cap_filter is not None:
                child_bin = hist.cap_bin(rec["metrics"])
                if child_bin != cap_filter:
                    continue
            pv_s0 = parent["value"]
            cv_s0 = rec["value"]
            # deepest stage both reached
            p_deep = hist.best.get(parent["key"])
            c_deep = hist.best.get(rec["key"])
            delta_s0 = cv_s0 - pv_s0
            delta_deep = None
            if p_deep and c_deep and p_deep["stage_idx"] == c_deep["stage_idx"] and p_deep["stage_idx"] >= 1:
                delta_deep = c_deep["value"] - p_deep["value"]
            for lv in rec["edit"]:
                if lever_filter and lv != lever_filter:
                    continue
                p_val = parent["spec"].get(lv)
                c_val = rec["spec"].get(lv)
                fb = hist._bin(lv, p_val)
                tb = hist._bin(lv, c_val)
                key = (lv, fb, tb)
                buckets.setdefault(key, []).append((delta_s0, delta_deep))
        # aggregate
        rows = []
        for (lv, fb, tb), deltas in buckets.items():
            n = len(deltas)
            s0_deltas = [d[0] for d in deltas]
            deep_deltas = [d[1] for d in deltas if d[1] is not None]
            improved_s0 = sum(1 for d in s0_deltas if (hist.sign * d) < 0)  # lower is better for min
            mean_s0 = sum(s0_deltas) / n
            med_s0 = median(s0_deltas)
            mean_deep = sum(deep_deltas) / len(deep_deltas) if deep_deltas else None
            med_deep = median(deep_deltas) if deep_deltas else None
            improved_deep = sum(1 for d in deep_deltas if (hist.sign * d) < 0) if deep_deltas else None
            rows.append({"lever": lv, "from_bin": fb, "to_bin": tb, "n": n,
                         "mean_s0": round(mean_s0, 6), "median_s0": round(med_s0, 6), "improved_s0": improved_s0,
                         "mean_deep": round(mean_deep, 6) if mean_deep is not None else None,
                         "median_deep": round(med_deep, 6) if med_deep is not None else None,
                         "improved_deep": improved_deep})
        # sort by |n| descending, bound to 25
        rows.sort(key=lambda r: -r["n"])
        rows = rows[:25]
        return _text({"note": "observational: operators choose which levers to change", "effects": rows})

    # ---- v2: predict ----
    def _tool_predict(self, job, args, surrogate) -> str:
        c = self.c
        raw_spec = args.get("spec")
        if not isinstance(raw_spec, dict):
            return "error: spec must be a JSON object"
        try:
            spec = c.space.validate({**c.space.defaults(), **raw_spec})
        except SpecError as e:
            return _text({"error": f"invalid spec: {e}"})
        if surrogate is None:
            return _text({"error": "surrogate not available"})
        result = surrogate.predict(job, c, spec)
        if isinstance(result, tuple) and len(result) == 2:
            a, b = result
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return _text({"predicted_value": round(a, 6), "spread": round(b, 6)})
            # string message + count
            return _text({"status": str(a), "rows": b})
        return _text({"error": "unexpected surrogate result"})

    # ---- v2: scorecard ----
    def _tool_scorecard(self, job) -> str:
        """Track record under protocol v2: compare recorded expectations with actual outcomes."""
        led = job.led
        hist = job.hist
        hist.update()
        # read all agent_expectation events
        events = led.recent_events("agent_expectation", 500)
        if not events:
            return _text({"note": "no expectations recorded yet", "total": 0})
        counts: dict = {}  # (expect, direction_label) -> {"n": 0, "correct": 0}
        total, evaluated, correct = 0, 0, 0
        for ev in events:
            p = ev["payload"]
            if p.get("arm") != job.arm or p.get("epoch") != hist.epoch:
                continue
            spec_key = p.get("spec_key")
            parent_key = p.get("parent")
            expect = p.get("expect")
            if not spec_key or not parent_key or expect not in ("better", "worse", "same"):
                continue
            total += 1
            child_rec = hist.best.get(spec_key)
            parent_rec = hist.best.get(parent_key)
            if not child_rec or not parent_rec or child_rec["stage_idx"] != parent_rec["stage_idx"]:
                continue
            evaluated += 1
            cv = child_rec["value"]
            pv = parent_rec["value"]
            # deterministic comparison: did the child actually do better/worse/same?
            # 'same' = within 0.1% relative
            threshold = 0.001 * max(abs(pv), 1e-9)
            if cv < pv - threshold:
                actual = "better" if self.c.direction == "min" else "worse"
            elif cv > pv + threshold:
                actual = "worse" if self.c.direction == "min" else "better"
            else:
                actual = "same"
            hit = (expect == actual)
            correct += hit
            key = expect
            counts.setdefault(key, {"n": 0, "correct": 0})
            counts[key]["n"] += 1
            counts[key]["correct"] += hit
        return _text({"total_expectations": total, "evaluated": evaluated, "correct": correct,
                      "by_expect": counts})

    def _hist(self):
        """the loop thread's History (used by `refill` only): jobs build their own"""
        from .history import History
        h = getattr(self, "_h", None)
        if h is None:
            h = self._h = History.for_arm(self._ledger, self.c, self._arm)
        h.update()
        return h

    # ---- prompt --------------------------------------------------------
    def build_agent_prompt(self, memory: str, hints: list, arm: str = "", epoch=None) -> list[dict]:
        c = self.c
        task = c.raw["task"].get("description", c.name)
        active_tools = self._active_tools()
        tools = "\n".join(f"- {k}: {v.replace('{primary}', c.primary)}" for k, v in active_tools.items())
        max_tools = self.max_tools
        if self.tools_gen == "v2":
            rules = RULES_V2.replace("{max_tools}", str(max_tools))
            sys = ("You are a research agent proposing candidate model specifications for an automated search. You may inspect the experiment ledger "
                   "with the tools below before you answer; look at failures and at how the best specs differ from the rest, form a hypothesis about WHY "
                   "something works, then propose. Reply with ONLY one JSON object per turn: either a tool call "
                   '{"tool": <name>, "args": {...}} or the final answer. Never include code. You cannot change how specs are scored.\n\nTools:\n'
                   + tools + "\n\n" + rules)
        else:
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
                + f"\nYou may call at most {max_tools} tools. Prefer a change of mechanism (a different core, lever combination or structure) to a small numeric tweak; "
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
        self._yield_ledger = ledger
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
        surrogate = _SurrogateCache() if self.tools_gen == "v2" else None
        while True:
            obj, meta = self.client.chat_json(msgs)
            if not isinstance(obj, dict):
                with self.lock:
                    self.stats["calls"] += 1; self.stats["failed_calls"] += 1
                self.record_yield({"operator": self.name, "failed": True, "capped": bool(meta.get("capped")), "tools": used})
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
                result, ok = self._tool(job, tool, args, surrogate), True
            except Exception as e:                                       # any failure of a tool is an answer to the model and a row in the record
                result, ok = f"error: {type(e).__name__}: {clean_text(str(e), 200)}", False
            with self.lock:
                self.stats["tool_calls"] += 1; self.stats["tool_errors"] += (not ok)
            self._log(ledger, used, tool, args, result, ok)
            msgs += [{"role": "assistant", "content": json.dumps(obj, default=str)[:1500]},
                     {"role": "user", "content": f"Result of {clean_text(tool, 40)}:\n{result}"}]
        self._used = used
        self._accept(obj, meta, seen_snapshot, seen_specs, job, ledger)

    def _accept(self, obj, meta, seen_snapshot, seen_specs, job, ledger=None):
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
            if not isinstance(raw, dict):
                inv += 1; continue
            # v2 protocol: per-proposal parent, expect, why
            prop_parent = parent
            prop_expect = None
            prop_why = None
            if self.tools_gen == "v2":
                raw = dict(raw)
                if prop_parent is not None and prop_parent.get("epoch") != job.hist.epoch:
                    prop_parent = None
                # per-proposal parent override
                pp = raw.pop("parent", None)
                if pp is not None:
                    prop_parent = None
                    try:
                        pp_trial = job.led.get(self._id(pp))
                        if pp_trial is not None and pp_trial["arm"] == job.arm and pp_trial.get("epoch") == job.hist.epoch:
                            prop_parent = pp_trial
                        # wrong parent is dropped, spec still considered
                    except (ValueError, TypeError, KeyError, OverflowError):
                        pass  # wrong parent dropped
                prop_expect = raw.pop("expect", None)
                if prop_expect not in ("better", "worse", "same"):
                    prop_expect = None
                raw_why = raw.pop("why", None)
                if raw_why is not None:
                    prop_why = clean_text(str(raw_why), 160)
            try:
                s = c.space.validate({**c.space.defaults(), **raw})      # levers left out keep their defaults
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
            if prop_parent and isinstance(prop_parent.get("params"), dict):
                origin = prop_parent.get("spec_key")
                edit = sorted(x for x in set(s) | set(prop_parent["params"]) if s.get(x) != prop_parent["params"].get(x))[:12]
            good.append(Proposal(s, f"agent:{meta.get('ep', '?')}", f"[{axis}] {hyp}"[:160], origin, edit))
            # v2: write agent_expectation event
            if self.tools_gen == "v2" and prop_expect and origin and ledger:
                try:
                    ledger.event("agent_expectation", {
                        "arm": job.arm, "epoch": job.hist.epoch,
                        "spec_key": k, "parent": origin,
                        "expect": prop_expect,
                        "why": prop_why or "",
                    })
                except Exception:
                    pass
        with self.lock:
            self.buf.extend(good)
            st = self.stats
            st["calls"] += 1; st["proposed"] += len(props); st["invalid"] += inv
            st["duplicate"] += dup; st["accepted"] += len(good)
        self.record_yield({"operator": self.name, "ep": meta.get("ep"), "proposed": len(props), "invalid": inv, "duplicate": dup, "accepted": len(good),
                           "tools": getattr(self, "_used", 0), "axis": axis})
