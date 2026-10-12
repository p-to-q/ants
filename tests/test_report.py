from antslab.report import lint_numbers, draft_narrative, tables_md, facts

F = {"arms": {"evo": {"trials": 120, "best_by_cap": {"300": {"value": 0.41234, "n_params": 288}}}}, "cpu_s": 301.5}


def test_lint_accepts_traceable_numbers_with_rounding():
    t = "evo ran 120 trials; best at 300 params reached 0.412 using 288 parameters in about 301.5 CPU seconds."
    assert lint_numbers(t, F) == []


def test_lint_flags_fabricated_numbers():
    assert lint_numbers("evo improved by 17.3% over random (0.35).", F) == ["17.3", "0.35"]


def test_lint_ignores_code_spans_headings_and_list_markers():
    t = "# Section 2\n1. first point `v0.9.3` with trial 120\n2) second"
    assert lint_numbers(t, F) == []


class C:
    def __init__(self, texts): self.texts, self.i = texts, 0
    def chat_json(self, m, temperature=0.3):
        r = self.texts[min(self.i, len(self.texts) - 1)]; self.i += 1
        return {"text": r}, {}


def test_draft_repairs_once_then_marks_unverified():
    ok, bad = draft_narrative(C(["evo made up 99.9", "evo ran 120 trials"]), F)
    assert ok == "evo ran 120 trials" and bad == []
    txt, bad = draft_narrative(C(["still invents 42.42", "and again 77.7"]), F)
    assert txt.startswith("> **UNVERIFIED**") and bad == ["77.7"]
