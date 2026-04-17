#!/usr/bin/env python3
"""HytaleGenerator Biome Y-Shifter (GUI)

A simple Tkinter GUI to shift height-related values in HytaleGenerator biome JSONs.
Allows specifying old base, new base, and includes a toggle to shift Props or skip them.
"""

from __future__ import annotations

import json
import math
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Number = int | float

@dataclass
class ChangeCounts:
    curve_points_shifted: int = 0
    curves_shifted: int = 0
    scanners_shifted: int = 0
    simple_horizontal_shifted: int = 0
    water_levels_shifted: int = 0
    y_sliders_shifted: int = 0
    files_modified: int = 0

def _contains_type(node: Any, *, target_type: str) -> bool:
    if isinstance(node, dict):
        if node.get("Type") == target_type:
            return True
        for v in node.values():
            if _contains_type(v, target_type=target_type):
                return True
        return False
    if isinstance(node, list):
        return any(_contains_type(i, target_type=target_type) for i in node)
    return False

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def _looks_like_absolute_height_curve(in_values: list[Number]) -> bool:
    if not in_values:
        return False

    finite = [v for v in in_values if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(finite) != len(in_values):
        return False

    min_v = min(finite)
    max_v = max(finite)
    spread = max_v - min_v

    if abs(min_v) <= 5 and abs(max_v) <= 5:
        return False

    if spread <= 2 and abs((min_v + max_v) / 2) <= 5:
        return False

    return True

def _shift_number_preserve_int(value: Number, delta: Number) -> Number:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and isinstance(delta, int):
        return value + delta
    result = float(value) + float(delta)
    if result.is_integer():
        return int(result)
    return result

def _iter_manual_curve_in_values(curve: dict[str, Any]) -> list[Number]:
    points = curve.get("Points")
    if not isinstance(points, list):
        return []
    in_values: list[Number] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        in_v = point.get("In")
        if _is_number(in_v):
            in_values.append(in_v)
    return in_values

def _contains_water_fluid(node: Any) -> bool:
    if isinstance(node, dict):
        fluid = node.get("Fluid")
        if isinstance(fluid, str) and fluid.lower().startswith("water"):
            return True
        return any(_contains_water_fluid(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_water_fluid(i) for i in node)
    return False

def _is_water_node(node: Any) -> bool:
    if not isinstance(node, dict) or node.get("Type") != "SimpleHorizontal":
        return False
    return _contains_water_fluid(node.get("Material"))

def shift_biome_json(data: Any, *, delta_y: Number, shift_props: bool, water_delta: Number = 0, shift_water: bool = True) -> tuple[Any, ChangeCounts]:
    counts = ChangeCounts()

    def walk(node: Any, *, in_metadata: bool = False, in_prop: bool = False) -> None:
        if isinstance(node, dict):
            if "$NodeEditorMetadata" in node:
                for k, v in node.items():
                    if k == "$NodeEditorMetadata":
                        continue
                    walk(v, in_metadata=in_metadata, in_prop=in_prop or k == "Props")
                return

            if in_metadata:
                return

            should_shift = True
            if in_prop and not shift_props:
                should_shift = False

            if should_shift:
                node_type = node.get("Type")

                if node_type == "Slider":
                    slide_y = node.get("SlideY")
                    if _is_number(slide_y):
                        node["SlideY"] = _shift_number_preserve_int(slide_y, delta_y)
                        counts.y_sliders_shifted += 1

                if node_type == "Linear" and node.get("Axis") == "Y":
                    range_obj = node.get("Range")
                    if isinstance(range_obj, dict):
                        min_inc = range_obj.get("MinInclusive")
                        max_exc = range_obj.get("MaxExclusive")
                        if _is_number(min_inc) and _is_number(max_exc):
                            range_obj["MinInclusive"] = _shift_number_preserve_int(min_inc, delta_y)
                            range_obj["MaxExclusive"] = _shift_number_preserve_int(max_exc, delta_y)
                            counts.scanners_shifted += 1

                if node_type == "SimpleHorizontal":
                    is_water = _is_water_node(node)

                    if is_water and not shift_water:
                        pass  # Skip processing this node completely
                    else:
                        shifted_simple = False
                        shifted_water_ref = False

                        def delta_for(base_height: Any) -> Number | None:
                            if base_height == "Water":
                                return water_delta if shift_water else None
                            if base_height in ("Base", "Bedrock", "Absolute", None):
                                return delta_y
                            return None

                        top_base = node.get("TopBaseHeight")
                        top_delta = delta_for(top_base)
                        if top_delta is not None:
                            top_y = node.get("TopY")
                            if _is_number(top_y):
                                node["TopY"] = _shift_number_preserve_int(top_y, top_delta)
                                shifted_simple = True
                                if top_base == "Water":
                                    shifted_water_ref = True

                        bottom_base = node.get("BottomBaseHeight")
                        bottom_delta = delta_for(bottom_base)
                        if bottom_delta is not None:
                            bottom_y = node.get("BottomY")
                            if _is_number(bottom_y):
                                node["BottomY"] = _shift_number_preserve_int(bottom_y, bottom_delta)
                                shifted_simple = True
                                if bottom_base == "Water":
                                    shifted_water_ref = True

                        if shifted_simple:
                            if is_water or shifted_water_ref:
                                counts.water_levels_shifted += 1
                            else:
                                counts.simple_horizontal_shifted += 1

                if node_type == "CurveMapper":
                    curve = node.get("Curve")
                    if isinstance(curve, dict) and curve.get("Type") == "Manual":
                        in_values = _iter_manual_curve_in_values(curve)
                        if _looks_like_absolute_height_curve(in_values):
                            points = curve.get("Points")
                            if isinstance(points, list):
                                shifted_any = False
                                for point in points:
                                    if not isinstance(point, dict):
                                        continue
                                    in_v = point.get("In")
                                    if _is_number(in_v):
                                        point["In"] = _shift_number_preserve_int(in_v, delta_y)
                                        counts.curve_points_shifted += 1
                                        shifted_any = True
                                if shifted_any:
                                    counts.curves_shifted += 1

            for k, v in node.items():
                walk(v, in_metadata=in_metadata, in_prop=in_prop or k == "Props")

        elif isinstance(node, list):
            for item in node:
                walk(item, in_metadata=in_metadata, in_prop=in_prop)

    walk(data)
    return data, counts

def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _dump_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent="\t")
        f.write("\n")

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert") or (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 20
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                         font=("Segoe UI", 9, "normal"))
        label.pack(ipadx=4, ipady=2)

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None

class BiomeShifterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hytale Biome Y-Shifter")
        
        # Make the window resizable so the layout system can manage size intuitively
        self.root.geometry("600x680")
        self.root.minsize(550, 600)

        frame = tk.Frame(root, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # Allow column 1 and row 10 to expand to fill extra space
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)

        # Help Button
        btn_help = tk.Button(frame, text="?", font=("Segoe UI", 10, "bold"), command=self.show_help, bg="#2196F3", fg="white", bd=0, cursor="hand2")
        btn_help.grid(row=0, column=2, sticky=tk.NE, padx=(10, 0))
        ToolTip(btn_help, "What's this?")

        # Global Vertical Shift
        lbl_shift = tk.Label(frame, text="Global Vertical Shift (blocks):", font=("Segoe UI", 10, "bold"))
        lbl_shift.grid(row=0, column=0, sticky=tk.W, pady=(0, 10))
        ToolTip(lbl_shift, "How much should the biome shift vertically?\nSets a global vertical shift (blocks) and updates 'New Base' and 'New Water'.\nWith 'Update water levels' enabled, water will move by the same shift as terrain/props.")

        shift_controls = tk.Frame(frame)
        shift_controls.grid(row=0, column=1, sticky=tk.EW, pady=(0, 10))
        shift_controls.columnconfigure(0, weight=1)

        self.shift_var = tk.IntVar(value=0)
        self.shift_slider = tk.Scale(
            shift_controls,
            from_=-256,
            to=256,
            orient="horizontal",
            variable=self.shift_var,
            command=self.on_slider_change,
            resolution=1,
            showvalue=False,
        )
        self.shift_slider.grid(row=0, column=0, sticky=tk.EW)

        self.shift_spin = tk.Spinbox(
            shift_controls,
            from_=-256,
            to=256,
            increment=1,
            textvariable=self.shift_var,
            width=6,
            font=("Segoe UI", 10),
            command=self.on_shift_spin_change,
        )
        self.shift_spin.grid(row=0, column=1, sticky=tk.E, padx=(10, 0))
        self.shift_spin.bind("<Return>", lambda e: self.on_shift_spin_change())
        self.shift_spin.bind("<FocusOut>", lambda e: self.on_shift_spin_change())

        # Baseline mappings
        lbl_old_base = tk.Label(frame, text="Old Base constant:", font=("Segoe UI", 10))
        lbl_old_base.grid(row=1, column=0, sticky=tk.W, pady=4, padx=(0, 10))
        ToolTip(lbl_old_base, "The framework base height of your original unshifted JSON (usually 50).")
        self.old_base_var = tk.StringVar(value="50")
        self.old_base_var.trace_add("write", lambda *_: self.on_base_water_manual_edit())
        tk.Entry(frame, textvariable=self.old_base_var, font=("Segoe UI", 10)).grid(row=1, column=1, sticky=tk.EW, pady=4)

        lbl_new_base = tk.Label(frame, text="New Base constant:", font=("Segoe UI", 10))
        lbl_new_base.grid(row=2, column=0, sticky=tk.W, pady=4, padx=(0, 10))
        ToolTip(lbl_new_base, "The target framework base height you are migrating to (usually 100).")
        self.new_base_var = tk.StringVar(value="100")
        tk.Entry(frame, textvariable=self.new_base_var, font=("Segoe UI", 10)).grid(row=2, column=1, sticky=tk.EW, pady=4)

        # Water level mappings
        lbl_old_water = tk.Label(frame, text="Old Water absolute height:", font=("Segoe UI", 10))
        lbl_old_water.grid(row=3, column=0, sticky=tk.W, pady=4, padx=(0, 10))
        ToolTip(lbl_old_water, "The absolute water level in the original unshifted biome (often same as Old Base, e.g. 50).")
        self.old_water_var = tk.StringVar(value="50")
        self.old_water_var.trace_add("write", lambda *_: self.on_base_water_manual_edit())
        tk.Entry(frame, textvariable=self.old_water_var, font=("Segoe UI", 10)).grid(row=3, column=1, sticky=tk.EW, pady=4)

        lbl_new_water = tk.Label(frame, text="New Water absolute height:", font=("Segoe UI", 10))
        lbl_new_water.grid(row=4, column=0, sticky=tk.W, pady=4, padx=(0, 10))
        ToolTip(lbl_new_water, "The target absolute water level for the shifted biome (often same as New Base, e.g. 100).")
        self.new_water_var = tk.StringVar(value="100")
        tk.Entry(frame, textvariable=self.new_water_var, font=("Segoe UI", 10)).grid(row=4, column=1, sticky=tk.EW, pady=4)

        # Toggles
        self.shift_props_var = tk.BooleanVar(value=True)
        chk_props = tk.Checkbutton(frame, text="Move props too", variable=self.shift_props_var, font=("Segoe UI", 10))
        chk_props.grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=4)
        ToolTip(chk_props, "Uncheck to skip shifting values inside 'Props', protecting their absolute positions.")

        self.shift_water_var = tk.BooleanVar(value=True)
        chk_water = tk.Checkbutton(frame, text="Update water levels", variable=self.shift_water_var, font=("Segoe UI", 10))
        chk_water.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=4)
        ToolTip(chk_water, "If enabled, detects SimpleHorizontal material providers dispensing Water_Source fluid to update their level.")

        self.backup_var = tk.BooleanVar(value=True)
        chk_backup = tk.Checkbutton(frame, text="Create .bak backups", variable=self.backup_var, font=("Segoe UI", 10))
        chk_backup.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=4)
        ToolTip(chk_backup, "Creates a duplicate of your json before modifying it in-place in case of errors.")

        # Button row with a little extra padding above and below
        btn_frame = tk.Frame(frame)
        btn_frame.grid(row=8, column=0, columnspan=2, pady=20)
        
        self.btn_select = tk.Button(btn_frame, text="Select JSON Files", command=self.select_files, font=("Segoe UI", 11, "bold"), bg="#2196F3", fg="white", padx=15, pady=8, cursor="hand2", borderwidth=0)
        self.btn_select.pack(side=tk.LEFT, padx=10)
        
        self.btn_run = tk.Button(btn_frame, text="Run Y-Shift", command=self.process_files, font=("Segoe UI", 11, "bold"), bg="#4CAF50", fg="white", disabledforeground="white", padx=15, pady=8, cursor="hand2", borderwidth=0)
        self.btn_run.pack(side=tk.LEFT, padx=10)
        self.btn_run.config(state=tk.DISABLED)
        
        self.selected_files = []

        # Ensure the log area correctly stretches
        self.log_area = scrolledtext.ScrolledText(frame, font=("Consolas", 10), borderwidth=1, relief=tk.SOLID)
        self.log_area.grid(row=10, column=0, columnspan=3, sticky=tk.NSEW)
        
        self.log("Welcome to Hytale Biome Y-Shifter.")
        self.log("Configure options and pick your JSON files.")
        self.log("---------------------------------------\n")
        
        # State tracking to avoid recursion in slider/entry updates
        self._updating_from_slider = False

    def on_slider_change(self, val):
        self._updating_from_slider = True
        try:
            old_b = float(self.old_base_var.get())
            old_w = float(self.old_water_var.get())
            shift = int(round(float(val)))

            new_b = old_b - shift
            new_w = old_w - shift

            # Format to remove trailing .0 if integer
            self.new_base_var.set(f"{int(new_b) if float(new_b).is_integer() else new_b}")
            self.new_water_var.set(f"{int(new_w) if float(new_w).is_integer() else new_w}")
        except ValueError:
            pass
        finally:
            self._updating_from_slider = False

    def on_shift_spin_change(self):
        # Ensure typed values update derived fields even when the scale callback is not triggered.
        try:
            self.on_slider_change(self.shift_var.get())
        except Exception:
            pass

    def on_base_water_manual_edit(self):
        if not self._updating_from_slider:
            # Re-trigger slider logic if old base/water are edited so new values stay in sync with current shift
            self.on_slider_change(self.shift_var.get())

    def show_help(self):
        help_text = (
            "PURPOSE\n"
            "This tool rewrites HytaleGenerator (WorldGen V2) biome JSON files to apply a constant vertical shift to height-related values. "
            "It is intended for moving an entire biome up or down without changing its internal shapes, and for adjusting biomes when the framework Base constant changes. "
            "When enabled, it also processes values under 'Props' and updates detected water level nodes.\n\n"
            "NODES AFFECTED\n"
            "- Slider (Type=Slider): SlideY\n"
            "- Linear scanner (Type=Linear, Axis=Y): Range.MinInclusive, Range.MaxExclusive\n"
            "- CurveMapper (Type=CurveMapper, Curve.Type=Manual): Points[].In (when treated as absolute height)\n"
            "- SimpleHorizontal (Type=SimpleHorizontal): TopY, BottomY\n"
            "- Water-referenced SimpleHorizontal (Top/BottomBaseHeight=Water): TopY, BottomY (uses water_delta)\n"
            "- Water fluid SimpleHorizontal (detected by Material.*.Fluid=Water_*): TopY, BottomY (uses water_delta)\n\n"
            "COMPUTED VALUES\n"
            "delta_y = Old_Base - New_Base\n"
            "water_delta = Old_Water - New_Water\n\n"
            "APPLICATION RULES\n"
            "For most height fields, the tool adds delta_y to preserve absolute heights after a Base change. "
            "For Water-referenced fields (Top/BottomBaseHeight=Water), it adds water_delta instead (when water updates are enabled).\n\n"
            "GLOBAL VERTICAL SHIFT\n"
            "The Global Vertical Shift control sets a shift value s (blocks) and updates the inputs so that delta_y = s and water_delta = s (by adjusting New Base and New Water together).\n"
        )
        messagebox.showinfo("Biome Y-Shifter", help_text)

    def log(self, text: str):
        self.log_area.insert(tk.END, text + "\n")
        self.log_area.see(tk.END)

    def select_files(self):
        filepaths = filedialog.askopenfilenames(
            title="Select Biome JSONs",
            filetypes=[("JSON Files", "*.json")]
        )
        if filepaths:
            self.selected_files = filepaths
            self.log(f"Selected {len(filepaths)} files.")
            self.btn_run.config(state=tk.NORMAL)

    def process_files(self):
        try:
            old_base = float(self.old_base_var.get())
            new_base = float(self.new_base_var.get())
            old_water = float(self.old_water_var.get())
            new_water = float(self.new_water_var.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Base and water values must be valid numbers.")
            return

        delta_y = old_base - new_base
        water_delta = old_water - new_water

        shift_props = self.shift_props_var.get()
        shift_water = self.shift_water_var.get()
        make_backup = self.backup_var.get()

        filepaths = self.selected_files

        if not filepaths:
            return

        self.log(f"--- Starting Processing ---")
        self.log(f"Base Delta Y = {old_base} - {new_base} = {delta_y}")
        self.log(f"Water Delta = {new_water} - {old_water} = {water_delta}")
        self.log(f"Move Props?: {'Yes' if shift_props else 'No'}")
        self.log(f"Update Water Levels?: {'Yes' if shift_water else 'No'}")

        totals = ChangeCounts()

        for p in filepaths:
            path = Path(p)
            self.log(f"\nProcessing {path.name}...")
            
            try:
                original = _load_json(path)
                updated, counts = shift_biome_json(
                    original, 
                    delta_y=delta_y, 
                    shift_props=shift_props,
                    water_delta=water_delta, 
                    shift_water=shift_water
                )
                
                modified = (
                    (counts.curve_points_shifted > 0)
                    or (counts.scanners_shifted > 0)
                    or (counts.y_sliders_shifted > 0)
                    or (counts.simple_horizontal_shifted > 0)
                    or (counts.water_levels_shifted > 0)
                )
                
                if modified:
                    totals.files_modified += 1
                    
                totals.curve_points_shifted += counts.curve_points_shifted
                totals.curves_shifted += counts.curves_shifted
                totals.scanners_shifted += counts.scanners_shifted
                totals.simple_horizontal_shifted += counts.simple_horizontal_shifted
                totals.water_levels_shifted += counts.water_levels_shifted
                totals.y_sliders_shifted += counts.y_sliders_shifted

                self.log(f"  > Curves shifted: {counts.curves_shifted}")
                self.log(f"  > Curve points shifted: {counts.curve_points_shifted}")
                self.log(f"  > Y scanners shifted: {counts.scanners_shifted}")
                self.log(f"  > SimpleHorizontal shifted: {counts.simple_horizontal_shifted}")
                self.log(f"  > Water levels shifted: {counts.water_levels_shifted}")
                self.log(f"  > YValue sliders shifted: {counts.y_sliders_shifted}")

                if modified:
                    if make_backup:
                        backup_path = path.with_suffix(path.suffix + ".bak")
                        shutil.copy2(path, backup_path)
                        self.log(f"  [+] Saved backup to {backup_path.name}")
                    _dump_json(path, updated)
                    self.log(f"  [+] Saved modifications to JSON.")
                else:
                    self.log(f"  [-] No changes were needed.")

            except Exception as e:
                self.log(f"  [!] ERROR processing {path.name}: {str(e)}")

        self.log(f"\n--- MISSION COMPLETE ---")
        self.log(f"Total files modified: {totals.files_modified}")

if __name__ == "__main__":
    root = tk.Tk()
    app = BiomeShifterApp(root)
    root.mainloop()
