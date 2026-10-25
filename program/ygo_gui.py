"""
ygo_gui.py  --  the randomizer window (Tkinter).

The window only SHOWS the settings and the result. Everything that decides things lives elsewhere:
ygo_settings.py turns the settings into a Request, ygo_final.build_deck() builds the deck, and
ygo_update.py / ygo_formats_update.py do the "Check for New Cards" button.

THE WINDOW, TOP TO BOTTOM (one method each, in RandomizerWindow)
  _files()          Card CSV + Label CSV pickers
  _presets()        Preset dropdown + Load / Save
  _deck_options()   sizes, seed + build order, monster/spell/trap targets, era, rating, Extra Deck types
  _levels()         Main Deck Monster Levels: the Level sliders (Triple) + the Normal Monsters tick
  _format()         Forbidden List dropdown + the release-date tick
  _split()          Deck Theme / Support sliders and the four label dropdowns
  _export()         export options
  _preview()        the Preview / Log box and the three buttons
WHAT THE BUTTONS DO
  generate()        roll the Random boxes, build the deck in the background, show it (show())
  copy()            the preview text -> clipboard
  check_new_cards() ask YGOPRODeck; offer() then updates the cards, or just the Forbidden Lists

NAMES USED HERE
  V          setting name -> its live Tk variable (the same names as the settings dict in ygo_settings.py)
  r          the next free grid row in the main frame (each part adds its rows)
  engine     the loaded cards (builder + extra_builder), kept between decks so they load only once
"""
import random                                               # rolls the Random boxes
import threading                                            # builds the deck without freezing the window
import traceback                                            # turns an error into readable lines for the window
import tkinter as tk                                        # window toolkit
from tkinter import ttk, filedialog, messagebox, simpledialog

import ygo_paths as P                                       # where the data / decks / backups folders are
import ygo_settings as S                                    # settings logic
import ygo_formats_update as F                              # 'Check for New Cards': the Forbidden Lists
import ygo_update as U                                      # 'Check for New Cards': the cards
from ygo_banlist import load_banlists                       # the Forbidden List dropdown
from ygo_builder import Builder                             # main-deck engine
from ygo_data import read_csv, load_tree                    # data loading
from ygo_extra import ExtraBuilder                          # extra-deck engine
from ygo_final import build_deck, export_deck               # whole pipeline + export
from ygo_labels import load_labels                          # dropdown label lists

DATA = P.DATA                                               # program/data: cards.csv, Stats.csv, settings ...
RANDOM_PAD = dict(sticky="w", padx=(8, 0), pady=(4, 0))     # where a row's "Random" tick box sits


# ---------------------------------------------------------------- small helpers
def wire_random(var: tk.BooleanVar, widgets: list) -> None:
    """Grey out `widgets` while the Random box `var` is ticked."""
    def update(*_):
        for w in widgets:
            w.state(["disabled"] if var.get() else ["!disabled"])      # ttk widgets: reliable on/off (also sliders)
    var.trace_add("write", update)
    update()


def attach_autocomplete(cb: ttk.Combobox) -> None:
    """Match the searchable value after '=' while displaying the full label."""
    cb._all = list(cb["values"])
    skip = {"Up", "Down", "Left", "Right", "Home", "End", "Tab", "Return", "Escape",
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}

    def searchable(label: str) -> str:
        return label.split("=", 1)[-1].strip().casefold()

    def on_key(e):
        if e.keysym in skip or str(cb.cget("state")) == "disabled":
            return
        query = cb.get().strip().casefold()
        # An already selected full label must not be interpreted as a new search.
        if query in (x.casefold() for x in cb._all):
            cb["values"] = cb._all
            return
        matches = [label for label in cb._all if query in searchable(label)] if query else cb._all
        cb["values"] = matches

    def reset(_event=None):
        cb["values"] = cb._all

    cb.bind("<KeyRelease>", on_key)
    cb.bind("<<ComboboxSelected>>", reset, add="+")
    cb.bind("<FocusOut>", reset, add="+")


def banlist_names() -> list:
    """'Random', 'Unlimited', then the name of every list in the 'forbidden lists' folder (Highlander is one)."""
    return [S.RANDOM, S.UNLIMITED] + list(load_banlists(P.FORBIDDEN))


class Split:
    """Two percentages that always add up to 100: two sliders, two boxes, two % labels, a '~N cards' label."""
    def __init__(self, total_fn):
        self.a, self.b = tk.IntVar(value=100), tk.IntVar(value=0)             # the two shares
        self.la, self.lb = tk.StringVar(), tk.StringVar()                      # "60%" labels
        self.ea, self.eb = tk.StringVar(), tk.StringVar()                      # text boxes
        self.cards = tk.StringVar()                                            # "~24 cards"
        self.total_fn = total_fn                                               # how many cards the pair shares out

    def set(self, v, from_b: bool = False) -> None:
        """Set one side (clamped 0-100); the other side becomes the rest."""
        try:
            v = max(0, min(100, int(round(float(v)))))
        except ValueError:
            v = self.b.get() if from_b else self.a.get()                       # bad text -> keep the old value
        (self.b if from_b else self.a).set(v)
        (self.a if from_b else self.b).set(100 - v)
        self.refresh()

    def refresh(self) -> None:
        """Update every label / box from the two values."""
        a, b = self.a.get(), self.b.get()
        self.la.set(f"{a}%"); self.lb.set(f"{b}%")
        self.ea.set(str(a)); self.eb.set(str(b))
        self.cards.set(f"~{self.total_fn()} cards")

    def row(self, frame, r: int, side_b: bool):
        """Draw one slider + % label + box on grid row r of `frame`. Returns (slider, box) so Random can grey them."""
        var, lab, ent = (self.b, self.lb, self.eb) if side_b else (self.a, self.la, self.ea)
        slider = ttk.Scale(frame, from_=0, to=100, orient="horizontal", variable=var,
                           command=lambda v: self.set(v, side_b))
        slider.grid(row=r, column=1, sticky="we", padx=(4, 4))
        ttk.Label(frame, textvariable=lab, width=6).grid(row=r, column=2, sticky="w")
        box = ttk.Entry(frame, textvariable=ent, width=4)
        box.grid(row=r, column=3, sticky="w")
        for ev in ("<Return>", "<FocusOut>"):                                  # typing a number applies it
            box.bind(ev, lambda _e, e=ent, sb=side_b: self.set(e.get(), sb))
        return slider, box


class Triple:
    """Three percentages that always add up to 100 (the Level 1-4 / 5-6 / 7+ sliders). Moving one shares the rest
       between the other two IN PROPORTION (60/30/10 -> Level 1-4 to 80 gives 80/15/5); if both others are 0 they split it evenly."""
    def __init__(self, variables: list, total_fn):
        self.v = variables                                                     # the three IntVars (the settings variables themselves)
        self.labels = [tk.StringVar() for _ in variables]                      # "60%" labels
        self.boxes = [tk.StringVar() for _ in variables]                       # text boxes
        self.counts = [tk.StringVar() for _ in variables]                      # "~12 monsters"
        self.total_fn = total_fn                                               # how many monsters the shares are of

    def set(self, i: int, value) -> None:
        """Set slider i (clamped 0-100); the other two share what is left, keeping their proportions."""
        try:
            value = max(0, min(100, int(round(float(value)))))
        except ValueError:
            value = self.v[i].get()                                            # bad text -> keep the old value
        others = [j for j in range(3) if j != i]                               # the two sliders that make room
        old = [self.v[j].get() for j in others]
        rest = 100 - value
        if sum(old) == 0:                                                      # both at 0: split the rest evenly
            first = rest - rest // 2
        else:
            first = round(rest * old[0] / sum(old))                            # in proportion to what they had
        self.v[i].set(value)
        self.v[others[0]].set(first)
        self.v[others[1]].set(rest - first)                                    # (so the three always make exactly 100)
        self.refresh()

    def refresh(self) -> None:
        """Update the % labels, the boxes and the '~N monsters' counts from the three values."""
        total = self.total_fn()
        for var, lab, box, cnt in zip(self.v, self.labels, self.boxes, self.counts):
            lab.set(f"{var.get()}%")
            box.set(str(var.get()))
            cnt.set(f"~{round(total * var.get() / 100)} cards")

    def row(self, frame, r: int, i: int, text: str) -> list:
        """Draw slider i on grid row r: name, slider, % label, box, count. Returns [slider, box] (to grey them out)."""
        ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w")
        slider = ttk.Scale(frame, from_=0, to=100, orient="horizontal", variable=self.v[i],
                           command=lambda val: self.set(i, val))
        slider.grid(row=r, column=1, sticky="we", padx=(4, 4))
        ttk.Label(frame, textvariable=self.labels[i], width=6).grid(row=r, column=2, sticky="w")
        box = ttk.Entry(frame, textvariable=self.boxes[i], width=4)
        box.grid(row=r, column=3, sticky="w")
        for ev in ("<Return>", "<FocusOut>"):                                  # typing a number applies it
            box.bind(ev, lambda _e: self.set(i, self.boxes[i].get()))
        ttk.Label(frame, textvariable=self.counts[i], width=13).grid(row=r, column=4, sticky="w", padx=(6, 0))
        return [slider, box]


# ---------------------------------------------------------------- the window
class RandomizerWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Yu-Gi-Oh! Deck Randomizer")
        self.root.columnconfigure(0, weight=1); self.root.rowconfigure(0, weight=1)
        self.f = self._scroll_area()                            # the frame every setting goes in
        self.d = S.defaults()
        self.V: dict = {}                                       # settings key -> tk variable (the settings dict, but live)
        for k, val in self.d.items():                           # one variable per setting, of the matching kind
            self.V[k] = (tk.BooleanVar(value=val) if isinstance(val, bool) else
                         tk.IntVar(value=val) if isinstance(val, int) else tk.StringVar(value=val))
        self.engine = {"path": None, "builder": None, "extra_builder": None}   # loaded card data, reused between builds
        self.r = 0                                              # next free grid row in self.f
        self._files()
        self._presets()
        self._deck_options()
        self._format()
        self._split()
        self._export()
        self._preview()
        self._start_up()

    # ---- the parts of the window, top to bottom ----------------------------
    def _scroll_area(self):
        """A scrollable frame filling the window, so it works on small screens. Returns the frame."""
        box = ttk.Frame(self.root); box.grid(sticky="nsew"); box.rowconfigure(0, weight=1); box.columnconfigure(0, weight=1)
        canvas = tk.Canvas(box, highlightthickness=0)
        self.bar = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=self.bar.set)
        canvas.grid(row=0, column=0, sticky="nsew"); self.bar.grid(row=0, column=1, sticky="ns")
        f = ttk.Frame(canvas, padding=10)
        win = canvas.create_window((0, 0), window=f, anchor="nw")
        f.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units") if e.delta else None)
        for c in range(3):
            f.columnconfigure(c, weight=1)
        return f

    def _files(self) -> None:
        """Card CSV and Label CSV: a box + Browse button each."""
        f, V = self.f, self.V
        for text, key in (("Card CSV (filled master):", "csv_path"), ("Label CSV (stats / archetypes):", "label_csv_path")):
            ttk.Label(f, text=text).grid(row=self.r, column=0, sticky="w")
            ttk.Entry(f, textvariable=V[key], width=50).grid(row=self.r, column=1, sticky="we", padx=4)
            ttk.Button(f, text="Browse...", command=lambda k=key: self.browse(V[k])).grid(row=self.r, column=2, sticky="w")
            self.r += 1

    def _presets(self) -> None:
        """Preset dropdown + Load / Save buttons."""
        f = self.f
        self.presets, self.last_name = S.load_presets(DATA)
        self.preset_name = tk.StringVar()
        ttk.Label(f, text="Preset:").grid(row=self.r, column=0, sticky="w", pady=(6, 0))
        self.preset_cb = ttk.Combobox(f, textvariable=self.preset_name, state="readonly", width=28,
                                      values=sorted(n for n in self.presets if not n.startswith("_")))
        self.preset_cb.grid(row=self.r, column=1, sticky="w", pady=(6, 0))
        pb = ttk.Frame(f); pb.grid(row=self.r, column=2, sticky="w", pady=(6, 0))
        ttk.Button(pb, text="Load", command=self.load_preset).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(pb, text="Save", command=self.save_preset).grid(row=0, column=1)
        self.r += 1

    def _random_tick(self, rand_key: str, widgets: list, **grid) -> None:
        """A row's 'Random' tick box; while ticked, `widgets` are greyed out."""
        ttk.Checkbutton(self.f, text="Random", variable=self.V[rand_key]).grid(row=self.r, column=2, **(grid or RANDOM_PAD))
        wire_random(self.V[rand_key], widgets)

    def _boxes_row(self, label: str, keys: tuple, rand_key: str, width: int = 5) -> None:
        """One label, a few small boxes side by side, and a Random tick (composition, rating)."""
        ttk.Label(self.f, text=label).grid(row=self.r, column=0, sticky="w", pady=(4, 0))
        holder = ttk.Frame(self.f); holder.grid(row=self.r, column=1, sticky="w", pady=(4, 0))
        boxes = [ttk.Entry(holder, textvariable=self.V[k], width=width) for k in keys]
        for e in boxes:
            e.pack(side="left", padx=(0, 6))
        self._random_tick(rand_key, boxes)
        self.r += 1

    def _deck_options(self) -> None:
        """Sizes, seed + build order, monster/spell/trap targets, era, rating, Extra Deck types."""
        f, V = self.f, self.V
        for label, key, rand_key in (("Main Deck Size (40-60):", "main_size", "rand_main_size"),
                                     ("Extra Deck Size (0-15):", "extra_size", "rand_extra_size")):
            ttk.Label(f, text=label).grid(row=self.r, column=0, sticky="w", pady=(4, 0))
            e = ttk.Entry(f, textvariable=V[key], width=8); e.grid(row=self.r, column=1, sticky="w", pady=(4, 0))
            self._random_tick(rand_key, [e])
            self.r += 1

        # seed + build order share a row; the seed's Random tick sits under the build order
        ttk.Label(f, text="Seed:").grid(row=self.r, column=0, sticky="w")
        seed_e = ttk.Entry(f, textvariable=V["seed"], width=16); seed_e.grid(row=self.r, column=1, sticky="w")
        ttk.Label(f, text="Build Order:").grid(row=self.r, column=1, sticky="e")
        ttk.Combobox(f, textvariable=V["build_order"], values=S.BUILD_ORDERS, state="readonly", width=12).grid(row=self.r, column=2, sticky="w")
        ttk.Checkbutton(f, text="Random seed", variable=V["rand_seed"]).grid(row=self.r + 1, column=2, sticky="w", padx=(8, 0))
        wire_random(V["rand_seed"], [seed_e]); self.r += 2

        self._boxes_row("Main composition (M/S/T):", ("monster_target", "spell_target", "trap_target"), "rand_comp")
        self._levels()

        ttk.Label(f, text="Max Era:").grid(row=self.r, column=0, sticky="w", pady=(4, 0))
        era_cb = ttk.Combobox(f, textvariable=V["max_era"], values=S.ERAS, state="readonly", width=12)
        era_cb.grid(row=self.r, column=1, sticky="w", pady=(4, 0))
        self._random_tick("rand_era", [era_cb])
        self.r += 1

        self._boxes_row("Rating min/max (0-5):", ("min_rating", "max_rating"), "rand_rating")

        xf = ttk.Frame(f); xf.grid(row=self.r, column=0, columnspan=3, sticky="w", pady=(6, 0))   # Extra Deck types
        tcbs = [ttk.Checkbutton(xf, text=t.replace("allow_", "").capitalize(), variable=V[t]) for t in S.EXTRA_TYPES]
        for i, cb in enumerate(tcbs):
            cb.grid(row=0, column=i + 1, sticky="w")
        ttk.Checkbutton(xf, text="Random", variable=V["rand_extra_types"]).grid(row=0, column=6, padx=(10, 0))
        wire_random(V["rand_extra_types"], tcbs); self.r += 1

    def monster_count(self) -> int:
        """The Monster target typed in (for the '~N monsters' counts), or 20 if the box holds something else."""
        try:
            return max(0, int(self.V["monster_target"].get()))
        except (ValueError, tk.TclError):
            return 20

    def _levels(self) -> None:
        """The 'Main Deck Monster Levels' box: the on/off tick, the three Level sliders (always 100% together) with a
           Random tick, and the Normal Monsters tick. The sliders are greyed out while the tick is off or Random is on."""
        V = self.V
        lf = ttk.LabelFrame(self.f, text="Main Deck Monster Levels")
        lf.grid(row=self.r, column=0, columnspan=3, sticky="nsew", pady=(8, 0)); self.r += 1
        lf.columnconfigure(1, weight=1)                                         # the sliders stretch with the window
        ttk.Checkbutton(lf, text="Use Level sliders  (unticked = the usual mix: low Levels favoured 4 / 2 / 1)",
                        variable=V["level_sliders"]).grid(row=0, column=0, columnspan=6, sticky="w")
        self.levels = Triple([V["level_low_pct"], V["level_mid_pct"], V["level_high_pct"]], self.monster_count)
        widgets = []                                                            # every slider + box (to grey out)
        for i, text in enumerate(("Level 1-4:", "Level 5-6:", "Level 7+:")):
            widgets += self.levels.row(lf, i + 1, i, text)
        rand_cb = ttk.Checkbutton(lf, text="Random", variable=V["rand_levels"])
        rand_cb.grid(row=1, column=5, rowspan=3, padx=(8, 0))
        ttk.Checkbutton(lf, text="Normal Monsters  (unticked = none, except ones that ARE the theme)",
                        variable=V["allow_normals"]).grid(row=4, column=0, columnspan=6, sticky="w", pady=(6, 0))

        def levels_toggled(*_):
            """Sliders usable only while 'Use Level sliders' is ticked AND Random is not; Random itself only while it is ticked."""
            on = V["level_sliders"].get()
            for w in widgets:
                w.state(["!disabled"] if on and not V["rand_levels"].get() else ["disabled"])
            rand_cb.state(["!disabled"] if on else ["disabled"])
        V["level_sliders"].trace_add("write", levels_toggled)
        V["rand_levels"].trace_add("write", levels_toggled)
        V["monster_target"].trace_add("write", lambda *_: self.levels.refresh())   # new Monster target -> new '~N monsters'
        levels_toggled()

    def _format(self) -> None:
        """Forbidden List dropdown (every list in the 'forbidden lists' folder; 'Random' is a choice in the list
           itself, like the archetype boxes) and the release-date tick, greyed out for Unlimited."""
        f, V = self.f, self.V
        ttk.Label(f, text="Forbidden List:").grid(row=self.r, column=0, sticky="w", pady=(6, 0))
        self.ban_cb = ttk.Combobox(f, textvariable=V["banlist"], values=banlist_names(), state="readonly", width=30)
        self.ban_cb.grid(row=self.r, column=1, sticky="w", pady=(6, 0))
        self.r += 1
        date_cb = ttk.Checkbutton(f, text="Only cards released by the list's date", variable=V["date_pool"])
        date_cb.grid(row=self.r, column=1, columnspan=2, sticky="w"); self.r += 1

        def banlist_changed(*_):
            """The release-date tick means nothing for Unlimited: grey it out then."""
            date_cb.state(["disabled"] if V["banlist"].get() in ("", S.UNLIMITED) else ["!disabled"])
        V["banlist"].trace_add("write", banlist_changed)
        banlist_changed()

    def main_size(self) -> int:
        """The main deck size typed in (40-60), or 40 if the box holds something else."""
        try:
            return max(40, min(60, int(self.V["main_size"].get())))
        except (ValueError, tk.TclError):
            return 40

    def _split(self) -> None:
        """The 'Deck Theme / Support Split' box: the theme-vs-support sliders, then Deck Type 1/2 and Support 1/2
           (a dropdown + slider each), and the Support on/off tick."""
        V, d = self.V, self.d
        df = ttk.LabelFrame(self.f, text="Deck Theme / Support Split")
        df.grid(row=self.r, column=0, columnspan=3, sticky="nsew", pady=(8, 0)); self.r += 1
        df.columnconfigure(1, weight=1)

        theme_cards = lambda: int(round(self.main_size() * V["deck_type_overall_pct"].get() / 100.0))
        support_cards = lambda: int(round(self.main_size() * V["support_overall_pct"].get() / 100.0))
        self.overall = Triple([V["deck_type_overall_pct"], V["support_overall_pct"], V["generic_overall_pct"]], self.main_size)
        self.deck12 = Split(theme_cards)
        self.supp12 = Split(support_cards)
        V["deck1_pct"], V["deck2_pct"] = self.deck12.a, self.deck12.b
        V["support1_pct"], V["support2_pct"] = self.supp12.a, self.supp12.b
        self.deck12.a.set(d["deck1_pct"]); self.deck12.b.set(d["deck2_pct"])
        self.supp12.a.set(d["support1_pct"]); self.supp12.b.set(d["support2_pct"])
        V["main_size"].trace_add("write", lambda *_: self.sync_all())
        overall_widgets = []
        for row, title in enumerate(("Deck Theme", "Support", "Generic")):
            overall_widgets += self.overall.row(df, row, row, title + " %:")
        ttk.Checkbutton(df, text="Random", variable=V["rand_theme_support"]).grid(row=0, column=5, rowspan=3, padx=(8, 0))
        wire_random(V["rand_theme_support"], overall_widgets)
        for var in self.overall.v:
            var.trace_add("write", lambda *_: (self.deck12.refresh(), self.supp12.refresh()))

        self.labels = load_labels(P.data_file(V["label_csv_path"].get()), DATA / "Mechanics.csv")   # a bare name = the data folder
        self.combos, _ = self._label_block(df, 3, "Deck Type", self.deck12, "deck1_label", "deck2_label", "rand_deck12")
        # Support toggle: OFF = labels ignored, support slots filled with staples + cards that help >= 1/3 of the deck
        ttk.Checkbutton(df, text="Use Support labels  (unticked = Auto: staples + cards that help at least 1/3 of the deck)",
                        variable=V["support_on"]).grid(row=7, column=0, columnspan=6, sticky="w", pady=(10, 0))
        sup_combos, sup_sliders = self._label_block(df, 8, "Support", self.supp12, "support1_label", "support2_label", "rand_support12")
        self.combos += sup_combos

        def support_toggled(*_):
            """Grey out the Support 1/2 dropdowns + split while the toggle is OFF (the Random tick re-greys sliders itself)."""
            on = V["support_on"].get()
            for w in sup_combos:
                w.state(["!disabled"] if on else ["disabled"])
            for w in sup_sliders:
                w.state(["!disabled"] if on and not V["rand_support12"].get() else ["disabled"])
        V["support_on"].trace_add("write", support_toggled)
        V["rand_support12"].trace_add("write", support_toggled)
        support_toggled()

    def _label_block(self, df, row0: int, name: str, sp: Split, key1: str, key2: str, rand_key: str) -> tuple:
        """Two label dropdowns, each over its slider row, plus card count + Random tick. Returns (dropdowns, sliders)."""
        combos = []
        for i, key in enumerate((key1, key2)):
            ttk.Label(df, text=f"{name} {i + 1}:").grid(row=row0 + 2 * i, column=0, sticky="w", pady=(6, 0))
            cb = ttk.Combobox(df, textvariable=self.V[key], width=34, values=self.labels)
            cb.grid(row=row0 + 2 * i, column=1, sticky="w", pady=(6, 0))
            attach_autocomplete(cb); combos.append(cb)
        sa, ba = sp.row(df, row0 + 1, False)
        sb, bb = sp.row(df, row0 + 3, True)
        ttk.Label(df, textvariable=sp.cards).grid(row=row0 + 1, column=4, rowspan=3, sticky="w")
        ttk.Checkbutton(df, text="Random", variable=self.V[rand_key]).grid(row=row0 + 1, column=5, rowspan=3, padx=(8, 0))
        wire_random(self.V[rand_key], [sa, ba, sb, bb])
        return combos, [sa, ba, sb, bb]

    def _export(self) -> None:
        """Export tick, verbose-reasons tick, and the export file name."""
        V = self.V
        ef = ttk.Frame(self.f); ef.grid(row=self.r, column=0, columnspan=3, sticky="we", pady=(8, 4)); self.r += 1
        ttk.Checkbutton(ef, text="Export CSV/TXT/YDK", variable=V["do_export"]).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(ef, text="Verbose reasons (preview + export)", variable=V["verbose_export"]).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Label(ef, text="Export file name (saved in decks):").grid(row=1, column=1, sticky="e", pady=(4, 0))
        ttk.Entry(ef, textvariable=V["export_prefix"], width=25).grid(row=1, column=2, sticky="w", pady=(4, 0))

    def _preview(self) -> None:
        """The three buttons, then the Preview / Log box under them."""
        f = self.f
        bf = ttk.Frame(f); bf.grid(row=self.r, column=0, columnspan=3, sticky="we", pady=(4, 4)); self.r += 1
        ttk.Label(f, text="Preview / Log", font=("Segoe UI", 10, "bold")).grid(row=self.r, column=0, columnspan=3, sticky="w", pady=(8, 2)); self.r += 1
        self.preview = tk.Text(f, width=80, height=22, wrap="word")
        self.preview.grid(row=self.r, column=0, columnspan=3, sticky="nsew"); f.rowconfigure(self.r, weight=1)
        ttk.Button(bf, text="Generate Deck", command=self.generate).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(bf, text="Copy Decklist", command=self.copy).grid(row=0, column=1, padx=(0, 8))
        self.update_btn = ttk.Button(bf, text="Check for New Cards", command=self.check_new_cards)
        self.update_btn.grid(row=0, column=2)

    def _start_up(self) -> None:
        """Load the last used preset, draw the labels, size the window to fit (never bigger than the screen), run."""
        chosen = self.last_name if self.last_name in self.presets else "_last" if "_last" in self.presets else None
        if chosen:
            self.apply(self.presets[chosen])
            if not chosen.startswith("_"):
                self.preset_name.set(chosen)
        self.sync_all()
        root = self.root
        root.update_idletasks()                                              # let Tk work out how big the content wants to be
        want_w = self.f.winfo_reqwidth() + self.bar.winfo_reqwidth() + 20    # content + scrollbar + margin
        want_h = self.f.winfo_reqheight() + 20
        w = min(want_w, root.winfo_screenwidth() - 60)
        h = min(want_h, root.winfo_screenheight() - 100)                     # leave room for the taskbar / title bar
        x, y = (root.winfo_screenwidth() - w) // 2, max(0, (root.winfo_screenheight() - h) // 2 - 20)
        root.geometry(f"{w}x{h}+{x}+{y}")                                    # size + centred position
        root.minsize(700, 500)
        root.mainloop()

    # ---- settings in and out --------------------------------------------------
    def current(self) -> dict:
        """Read every widget variable into a plain settings dict."""
        return {k: v.get() for k, v in self.V.items()}

    def apply(self, vals: dict) -> None:
        """Push a settings dict into the widgets, then redraw the slider labels."""
        for k, val in S.merge_preset(self.d, vals).items():
            if k not in S.PATH_KEYS:
                self.V[k].set(val)
        self.sync_all()

    def sync_all(self) -> None:
        """Redraw every slider label + card count (after loading a preset or changing sizes)."""
        for sp in (self.overall, self.deck12, self.supp12):
            sp.refresh()
        self.levels.refresh()                                   # the Level sliders' labels and counts too

    def browse(self, var) -> None:
        """Pick a CSV file for this box; a new label CSV refills the dropdowns."""
        p = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if p:
            var.set(p)
            if var is self.V["label_csv_path"]:
                self.reload_labels()

    def reload_labels(self) -> None:
        """Re-read the label CSV and refill all four dropdowns."""
        self.labels = load_labels(P.data_file(self.V["label_csv_path"].get()), DATA / "Mechanics.csv")
        for cb in self.combos:
            cb["values"] = self.labels; cb._all = list(self.labels)

    def save_preset(self) -> None:
        name = simpledialog.askstring("Save Preset", "Preset name:")
        if name:
            self.presets[name] = self.current(); self.preset_name.set(name)
            self.preset_cb["values"] = sorted(n for n in self.presets if not n.startswith("_"))
            S.save_presets(DATA, self.presets, name, self.current())

    def load_preset(self):
        name = self.preset_name.get().strip()
        if name not in self.presets:
            return messagebox.showwarning("Load Preset", "Select a preset from the list first.")
        self.apply(self.presets[name]); S.save_presets(DATA, self.presets, name, self.current())

    # ---- the buttons ------------------------------------------------------------
    def generate(self):
        """Roll the Random boxes, build the Request, build the deck in a background thread, show it."""
        vals = self.current()
        S.roll(vals, random.Random())                           # rolls happen here...
        for k, val in vals.items():                             # ...and the window shows the rolled values (like the old GUI)
            if k not in S.PATH_KEYS and self.V[k].get() != val:
                self.V[k].set(val)
        self.sync_all()
        path = P.data_file(vals["csv_path"])                    # a bare name like 'cards.csv' = the data folder
        if not path.is_file():
            return messagebox.showerror("Error", f"Card CSV does not exist:\n{path}")
        req = S.make_request(vals, banlists=list(self.ban_cb["values"])[2:])   # (Random picks from these, or Unlimited)
        self.preview.delete("1.0", tk.END); self.preview.insert(tk.END, "Building...\n")

        def work():
            try:
                if self.engine["path"] != str(path):            # load cards only when the file changed
                    labels = load_labels(P.data_file(vals["label_csv_path"]), DATA / "Mechanics.csv",)
                    builder = Builder(read_csv(path),load_tree(DATA / "game_mechanics_tree.csv"),labels=self.labels)
                    self.engine.update(path=str(path), builder=builder, extra_builder=ExtraBuilder(builder))
                deck = build_deck(self.engine["builder"], self.engine["extra_builder"], req)
                self.root.after(0, lambda: self.show(deck, req, vals))
            except Exception as e:                              # show any error instead of dying silently
                msg = repr(e)                                   # (keep the text now: Python forgets 'e' when this block ends)
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to generate deck:\n{msg}"))
        threading.Thread(target=work, daemon=True).start()
        try:                                                    # remember the settings used, like the old GUI
            S.save_presets(DATA, self.presets, self.preset_name.get().strip() or "_last", vals)
        except OSError:
            pass

    def show(self, deck, req, vals) -> None:
        """Print the breakdown, both decks and the debug info; export if ticked."""
        preview, verbose = self.preview, vals["verbose_export"]
        preview.delete("1.0", tk.END)
        preview.insert(tk.END, S.breakdown_text(req))
        # Show the actual theme/support choices above the decklist, not buried in Debug.
        selected = deck.debug.get("selected_categories", {})
        preview.insert(tk.END, "Selected categories:\\n")
        for key, title in (("theme1", "Theme 1"), ("theme2", "Theme 2"),
                           ("support1", "Support 1"), ("support2", "Support 2")):
            label = selected.get(key, getattr(req, {"theme1": "deck_type_1_label",
                                                    "theme2": "deck_type_2_label",
                                                    "support1": "support_1_label",
                                                    "support2": "support_2_label"}[key], ""))
            detail = deck.debug.get(key, "")
            preview.insert(tk.END, f"  {title}: {label or 'None'}" +
                           (f" — {detail}" if detail else "") + "\\n")
        preview.insert(tk.END, "\\n")
        if "genesys_points" in deck.debug:                      # Genesys: how much of the point budget the deck uses
            used, cap = deck.debug["genesys_points"]
            preview.insert(tk.END, f"Genesys points: {used} / {cap}\n")
        preview.insert(tk.END, "\n")
        for title, entries in (("Main Deck", deck.main), ("Extra Deck", deck.extra)):
            preview.insert(tk.END, f"{title} ({len(entries)}):\n")
            for e in entries:
                preview.insert(tk.END, f" - {e.info.name} [{e.origin}]" + (f" :: {e.reason}" if verbose else "") + "\n")
            preview.insert(tk.END, "\n")
        preview.insert(tk.END, "Debug:\n" + "".join(f"{k}: {v}\n" for k, v in deck.debug.items()))
        if vals["do_export"]:
            try:
                name = vals["export_prefix"].strip() or "random_deck"                  # the file name you typed (or the default)
                paths = export_deck(deck, str(P.DECKS / name), verbose=bool(verbose))   # ...inside the decks folder
                preview.insert(tk.END, "\nExported: " + ", ".join(paths) + "\n")
            except OSError as e:
                messagebox.showwarning("Export", f"Could not export:\n{e}")

    def copy(self) -> None:
        """The preview text -> clipboard."""
        self.root.clipboard_clear(); self.root.clipboard_append(self.preview.get("1.0", tk.END)); self.root.update()
        messagebox.showinfo("Copied", "Decklist copied to clipboard.")

    def log(self, line: str) -> None:
        """Add one line to the preview box. Safe to call from the background thread (it hands the work to Tk)."""
        def add():
            self.preview.insert(tk.END, line + "\n")
            self.preview.see(tk.END)                            # keep the newest line in view
        self.root.after(0, add)

    def check_new_cards(self) -> None:
        """Ask YGOPRODeck for its database version (in the background); offer() decides what happens next."""
        self.update_btn.state(["disabled"])                     # one check / update at a time
        self.preview.delete("1.0", tk.END); self.preview.insert(tk.END, "Checking YGOPRODeck for new cards...\n")

        def work():
            try:
                latest, have, changed = U.check()               # about a second
            except Exception as e:
                msg = repr(e)                                   # keep the text now: Python forgets 'e' when this block ends, and the
                self.root.after(0, lambda: (messagebox.showerror("Check for New Cards", f"The check failed:\n{msg}"),   # message box runs later
                                            self.update_btn.state(["!disabled"])))
                return
            self.root.after(0, lambda: self.offer(latest, have, changed))
        threading.Thread(target=work, daemon=True).start()

    def offer(self, latest: str, have, changed: bool = False) -> None:
        """Nothing new: refresh just the Forbidden Lists. Newer cards, or a changed program: ask, then run the full update."""
        if latest == have and not changed:                      # cards and program are current: still refresh the Forbidden Lists
            self.log(f"Cards are up to date (card database version {latest}). Checking the Forbidden Lists...")

            def lists_work():
                F.refresh_banlists(P.data_file(self.V["csv_path"].get()), log=self.log)

                def finish():
                    self.engine["path"] = None                  # the next Generate re-reads the banlists
                    self.ban_cb["values"] = banlist_names()     # new formats in the dropdown
                    self.update_btn.state(["!disabled"])
                self.root.after(0, finish)
            threading.Thread(target=lists_work, daemon=True).start()
            return
        if latest != have:                                      # YGOPRODeck has new cards
            reason = f"YGOPRODeck has a newer card database (version {latest}; yours: {have or 'never updated'})."
        else:                                                   # same cards, but the program that builds cards.csv changed
            reason = ("The program has changed since your last update (for example new parser rules), "
                      "so cards.csv can be rebuilt with the changes.")
        ok = messagebox.askyesno("Check for New Cards",
                                 reason + "\n\nUpdate now? It takes about 5-10 minutes. Your current files are backed up first, "
                                 "and nothing changes if any step fails.")
        if not ok:
            self.log("Update skipped.")
            self.update_btn.state(["!disabled"])
            return

        def work():
            self.log("Starting the update ...")                 # the first line, so you can see it began
            try:
                done = U.run_update(P.data_file(self.V["csv_path"].get()), P.data_file(self.V["label_csv_path"].get()), log=self.log)
            except Exception:                                   # anything unexpected: show it in the window (a background
                for line in traceback.format_exc().splitlines():   # thread's error would otherwise vanish without a trace)
                    self.log("    " + line)
                self.log("!!! The update stopped. Your cards.csv and Stats.csv were NOT changed.")
                done = False

            def finish():
                if done:
                    self.engine["path"] = None                  # the next Generate re-reads the new cards (and banlists)
                    self.reload_labels()                        # new archetypes / series in the dropdowns
                    self.ban_cb["values"] = banlist_names()     # refreshed banlists
                    messagebox.showinfo("Check for New Cards", "Cards updated!")
                self.update_btn.state(["!disabled"])
            self.root.after(0, finish)
        threading.Thread(target=work, daemon=True).start()


def build_gui() -> None:
    """Open the window (the launcher, 'Yu-Gi-Oh Randomizer.pyw', calls this)."""
    RandomizerWindow()


if __name__ == "__main__":
    build_gui()
