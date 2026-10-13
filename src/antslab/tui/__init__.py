"""The terminal interface: `ants` on a terminal opens it. See docs/tui.md for the design.

    pixel.py   the ant, the word and the small glyphs (bars, sparklines) it is drawn with
    model.py   read-only data access: one Source per workdir, one function per screen
    views.py   pure functions from data to styled lines (no curses): testable, and the same lines serve `ants home` in a pipe
    app.py     the curses loop: keys, background refresh, pager, command palette
"""
