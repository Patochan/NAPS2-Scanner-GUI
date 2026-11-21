import os
import io
import json
import re
import shutil
import tempfile
import logging
import subprocess
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pypdf import PdfReader, PdfWriter


def load_pdf_reader(file_path):
    """
    Reads a PDF file completely into an in-memory BytesIO buffer.
    This keeps the stream open for pypdf during page cloning and writing,
    while immediately releasing the file lock on disk.
    """
    with open(file_path, "rb") as f:
        buf = io.BytesIO(f.read())
    return PdfReader(buf)


def read_pages(file_path):
    """Reads all pages of a PDF into memory. Missing files raise an error."""
    return list(load_pdf_reader(file_path).pages)


def interleave_pages(front, back):
    """Returns f1, b1, f2, b2, ... The back stack is scanned bottom-up, so it is read in reverse."""
    back = back[::-1]
    out = []
    for i in range(max(len(front), len(back))):
        if i < len(front):
            out.append(front[i])
        if i < len(back):
            out.append(back[i])
    return out


# Script directory and configuration file path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

# Default directories and files
DEFAULT_TEMP_DIR = os.path.join(tempfile.gettempdir(), "ScannerTool")
DEFAULT_LOG_FILE = os.path.join(DEFAULT_TEMP_DIR, "duplex_scan.log")

# Default configuration settings
DEFAULT_CONFIG = {
    "naps2_path": r"C:\Program Files\NAPS2\NAPS2.Console.exe",
    "profiles_xml_path": os.path.join(os.environ.get("APPDATA", ""), "NAPS2", "profiles.xml"),
    "log_file_path": DEFAULT_LOG_FILE,
    "log_level": "INFO",
    "selected_printer": "",
    "default_duplex": True,
    "always_on_top": True,
    "confirm_discard": True,
    "warn_page_mismatch": True,
    "hide_console_window": True,
    "temp_dir": None
}


CONFIG_ERROR = None  # Set when config.json could not be read; shown in the window


def load_config():
    """Loads configuration from config.json or returns default values."""
    global CONFIG_ERROR
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            logging.error(f"Error loading {CONFIG_FILE}: {e}")
            CONFIG_ERROR = str(e)
            # Keep the broken file so the user's settings are not overwritten
            try:
                os.replace(CONFIG_FILE, CONFIG_FILE + ".bad")
            except OSError as move_error:
                logging.error(f"Could not move broken config aside: {move_error}")
    else:
        save_config(config)
    return config


def save_config(config_data):
    """Saves the configuration dictionary to config.json."""
    try:
        # Write to a temp file first, so a crash mid-save cannot leave config.json empty
        tmp_file = CONFIG_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_file, CONFIG_FILE)
        logging.info(f"Configuration saved: {CONFIG_FILE}")
        return True
    except Exception as e:
        logging.exception(f"Error saving {CONFIG_FILE}: {e}")
        return False


def setup_logger(log_file=None, level_name="INFO"):
    """Configures the logging system with the given log file and log level."""
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(level)

    # Remove existing file handlers to allow dynamic reconfiguration
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            logger.removeHandler(handler)

    # Add file handler if log_file path is configured
    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s"))
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Failed to setup file logging at {log_file}: {e}")


def resolve_naps2_path(path):
    """Verifies the configured NAPS2 path or searches common installation locations."""
    if path and os.path.isfile(path):
        return path

    candidates = [
        path,
        r"C:\Program Files\NAPS2\NAPS2.Console.exe",
        r"C:\Program Files (x86)\NAPS2\NAPS2.Console.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\NAPS2\NAPS2.Console.exe"),
        shutil.which("NAPS2.Console.exe"),
        shutil.which("naps2.console"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            logging.info(f"Found NAPS2 executable: {candidate}")
            return candidate

    return path


def parse_profiles_xml(xml_path):
    """
    Parses profiles.xml and returns scan profiles grouped by printer name.
    Falls back to AppData if the specified path does not exist.
    """
    actual_path = xml_path
    if not actual_path or not os.path.isfile(actual_path):
        appdata_path = DEFAULT_CONFIG["profiles_xml_path"]
        if os.path.isfile(appdata_path):
            actual_path = appdata_path

    if not actual_path or not os.path.isfile(actual_path):
        logging.warning(f"profiles.xml not found at: {xml_path}")
        return {}

    try:
        tree = ET.parse(actual_path)
        root = tree.getroot()
        by_printer = {}

        for profile in root.findall("ScanProfile"):
            dev_elem = profile.find("Device")
            printer_name = dev_elem.findtext("Name", "Default Scanner") if dev_elem is not None else "Default Scanner"

            display_name = profile.findtext("DisplayName", "")
            source_raw = profile.findtext("PaperSource", "Feeder")
            res_raw = profile.findtext("Resolution", "Dpi300")
            is_default = (profile.findtext("IsDefault", "false").lower() == "true")

            # Extract resolution number (e.g. "Dpi300" -> 300)
            dpi_match = re.search(r"\d+", res_raw)
            dpi = int(dpi_match.group()) if dpi_match else 300

            # Determine source type (Glass -> flatbed, Feeder -> adf)
            source_type = "flatbed" if "glass" in source_raw.lower() else "adf"

            # Dynamic user-friendly label based on DPI range
            if dpi < 300:
                label = f"{dpi} DPI (Low)"
            elif dpi < 600:
                label = f"{dpi} DPI (Standard)"
            elif dpi < 1000:
                label = f"{dpi} DPI (High Res)"
            else:
                label = f"{dpi} DPI (Ultra High Res)"

            entry = {
                "display_name": display_name,
                "source": source_raw,
                "source_type": source_type,
                "resolution_dpi": dpi,
                "label": label,
                "is_default": is_default
            }
            by_printer.setdefault(printer_name, []).append(entry)

        # Sort profiles: ADF first, then ascending by resolution DPI
        for p_name, prof_list in by_printer.items():
            prof_list.sort(key=lambda x: (0 if x["source_type"] == "adf" else 1, x["resolution_dpi"]))

        logging.info(f"profiles.xml successfully loaded from {actual_path} ({len(by_printer)} printer(s) found).")
        return by_printer

    except Exception as e:
        logging.exception(f"Error parsing {actual_path}: {e}")
        return {}


# Load configuration
CONFIG = load_config()

# Initialize temporary directory
TEMP_DIR = CONFIG.get("temp_dir")
if not TEMP_DIR:
    TEMP_DIR = DEFAULT_TEMP_DIR
os.makedirs(TEMP_DIR, exist_ok=True)

# Initialize logging
setup_logger(CONFIG.get("log_file_path", DEFAULT_LOG_FILE), CONFIG.get("log_level", "INFO"))

# Icon path and helper functions
ICON_PATH = os.path.join(SCRIPT_DIR, "scanner.ico")


def apply_window_icon(window):
    """Applies the scanner icon (shipped next to this script) to the window."""
    if os.path.isfile(ICON_PATH):
        try:
            window.iconbitmap(ICON_PATH)
        except Exception as e:
            logging.debug(f"Failed to set iconbitmap: {e}")


class ConfigDialog(tk.Toplevel):
    """Configuration / Settings dialog for paths and options."""
    def __init__(self, parent, config, on_save_callback):
        super().__init__(parent)
        self.config = dict(config)
        self.on_save_callback = on_save_callback

        self.title("Settings / Configuration")
        self.geometry("560x540")
        self.minsize(500, 480)
        self.resizable(True, True)
        apply_window_icon(self)

        if parent.attributes("-topmost"):
            self.attributes("-topmost", True)
        self.transient(parent)
        self.grab_set()

        self.setup_ui()
        self.center_window(parent)

    def center_window(self, parent):
        """Centers the dialog over the parent window."""
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w = self.winfo_width()
        h = self.winfo_height()
        x = max(0, px + (pw - w) // 2)
        y = max(0, py + (ph - h) // 2)
        self.geometry(f"+{x}+{y}")

    def setup_ui(self):
        # Action buttons (packed at the bottom FIRST so they are never compressed by content above)
        btn_frame = tk.Frame(self, padx=15, pady=12)
        btn_frame.pack(side="bottom", fill="x")

        btn_save = tk.Button(
            btn_frame,
            text="Save",
            command=self.save,
            bg="#4CAF50",
            fg="white",
            font=("Arial", 9, "bold"),
            width=12,
            pady=3
        )
        btn_save.pack(side="right", padx=(8, 0))

        btn_cancel = ttk.Button(btn_frame, text="Cancel", command=self.destroy, width=12)
        btn_cancel.pack(side="right")

        # Paths frame
        path_frame = tk.LabelFrame(self, text="File Paths", padx=12, pady=8)
        path_frame.pack(fill="x", padx=15, pady=(8, 4))

        # NAPS2 Console path
        tk.Label(path_frame, text="NAPS2 Console Path (NAPS2.Console.exe):", font=("Arial", 9, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.entry_naps2 = ttk.Entry(path_frame, width=50)
        self.entry_naps2.insert(0, self.config.get("naps2_path", ""))
        self.entry_naps2.grid(row=1, column=0, sticky="ew", padx=(0, 6))

        btn_browse_naps2 = ttk.Button(path_frame, text="Browse...", command=lambda: self._pick(
            self.entry_naps2, filedialog.askopenfilename, title="Select NAPS2.Console.exe",
            filetypes=[("NAPS2.Console", "NAPS2.Console.exe"), ("Executable Files", "*.exe"), ("All Files", "*.*")]))
        btn_browse_naps2.grid(row=1, column=1)

        # profiles.xml path
        tk.Label(path_frame, text="profiles.xml Path:", font=("Arial", 9, "bold")).grid(row=2, column=0, sticky="w", pady=(8, 2))
        self.entry_profiles = ttk.Entry(path_frame, width=50)
        self.entry_profiles.insert(0, self.config.get("profiles_xml_path", ""))
        self.entry_profiles.grid(row=3, column=0, sticky="ew", padx=(0, 6))

        btn_browse_profiles = ttk.Button(path_frame, text="Browse...", command=lambda: self._pick(
            self.entry_profiles, filedialog.askopenfilename, title="Select profiles.xml",
            filetypes=[("XML Files", "*.xml"), ("All Files", "*.*")]))
        btn_browse_profiles.grid(row=3, column=1)

        # Log file path
        tk.Label(path_frame, text="Log File Path:", font=("Arial", 9, "bold")).grid(row=4, column=0, sticky="w", pady=(8, 2))
        self.entry_log = ttk.Entry(path_frame, width=50)
        self.entry_log.insert(0, self.config.get("log_file_path", DEFAULT_LOG_FILE))
        self.entry_log.grid(row=5, column=0, sticky="ew", padx=(0, 6))

        btn_browse_log = ttk.Button(path_frame, text="Browse...", command=lambda: self._pick(
            self.entry_log, filedialog.asksaveasfilename, title="Select Log File Location",
            defaultextension=".log", filetypes=[("Log Files", "*.log"), ("All Files", "*.*")]))
        btn_browse_log.grid(row=5, column=1)

        path_frame.columnconfigure(0, weight=1)

        # Options frame
        opt_frame = tk.LabelFrame(self, text="Options", padx=12, pady=6)
        opt_frame.pack(fill="x", padx=15, pady=4)

        # Log level row
        log_level_row = tk.Frame(opt_frame)
        log_level_row.pack(fill="x", pady=2)
        tk.Label(log_level_row, text="Log Level:").pack(side="left", padx=(0, 8))
        self.combo_log_level = ttk.Combobox(
            log_level_row,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            state="readonly",
            width=12
        )
        self.combo_log_level.set(self.config.get("log_level", "INFO"))
        self.combo_log_level.pack(side="left")

        self.var_topmost = tk.BooleanVar(value=self.config.get("always_on_top", True))
        ttk.Checkbutton(opt_frame, text="Window Always on Top", variable=self.var_topmost).pack(anchor="w", pady=2)

        self.var_duplex = tk.BooleanVar(value=self.config.get("default_duplex", True))
        ttk.Checkbutton(opt_frame, text="Enable Duplex by default", variable=self.var_duplex).pack(anchor="w", pady=2)

        self.var_confirm_discard = tk.BooleanVar(value=self.config.get("confirm_discard", True))
        ttk.Checkbutton(opt_frame, text="Confirm before discarding unsaved scans", variable=self.var_confirm_discard).pack(anchor="w", pady=2)

        self.var_warn_mismatch = tk.BooleanVar(value=self.config.get("warn_page_mismatch", True))
        ttk.Checkbutton(opt_frame, text="Warn on front/back page count mismatch (Duplex)", variable=self.var_warn_mismatch).pack(anchor="w", pady=2)

        self.var_hide_console = tk.BooleanVar(value=self.config.get("hide_console_window", True))
        ttk.Checkbutton(opt_frame, text="Hide console window during scan", variable=self.var_hide_console).pack(anchor="w", pady=2)

    def _pick(self, entry, ask, **kwargs):
        """Opens a file dialog (ask) and puts the chosen path into the entry."""
        f = ask(parent=self, **kwargs)
        if f:
            entry.delete(0, tk.END)
            entry.insert(0, f)

    def save(self):
        naps2_val = self.entry_naps2.get().strip()
        profiles_val = self.entry_profiles.get().strip()
        log_val = self.entry_log.get().strip()

        if naps2_val and not os.path.isfile(naps2_val):
            if not messagebox.askyesno(
                "Check NAPS2 Path",
                f"The file NAPS2.Console.exe was not found at:\n'{naps2_val}'\n\nSave anyway?",
                parent=self
            ):
                return

        if profiles_val and not os.path.isfile(profiles_val):
            if not messagebox.askyesno(
                "Check profiles.xml Path",
                f"The file profiles.xml was not found at:\n'{profiles_val}'\n\nSave anyway?",
                parent=self
            ):
                return

        self.config["naps2_path"] = naps2_val
        self.config["profiles_xml_path"] = profiles_val
        self.config["log_file_path"] = log_val
        self.config["log_level"] = self.combo_log_level.get()
        self.config["always_on_top"] = self.var_topmost.get()
        self.config["default_duplex"] = self.var_duplex.get()
        self.config["confirm_discard"] = self.var_confirm_discard.get()
        self.config["warn_page_mismatch"] = self.var_warn_mismatch.get()
        self.config["hide_console_window"] = self.var_hide_console.get()

        if save_config(self.config):
            self.on_save_callback(self.config)
            messagebox.showinfo("Saved", "Configuration successfully saved to config.json!", parent=self)
            self.destroy()
        else:
            messagebox.showerror("Error", "Could not save configuration.", parent=self)


class ScanApp:
    def __init__(self):
        logging.info("Initializing NAPS2 Scanner GUI...")
        self.config = load_config()
        self.naps2_path = resolve_naps2_path(self.config.get("naps2_path", ""))

        self.root = tk.Tk()
        self.root.title("NAPS2 Scanner GUI")
        self.root.geometry("460x490")
        apply_window_icon(self.root)
        if self.config.get("always_on_top", True):
            self.root.attributes("-topmost", True)
        self.root.eval("tk::PlaceWindow . center")

        # Load scan profiles from XML
        xml_path = self.config.get("profiles_xml_path", "")
        self.profiles_by_printer = parse_profiles_xml(xml_path)

        # UI state variables
        self.printer_var = tk.StringVar()
        self.profile_var = tk.StringVar()
        self.duplex_var = tk.BooleanVar(value=self.config.get("default_duplex", True))
        self.status_var = tk.StringVar(value="Ready")

        # Temporary scan files
        # One folder per window, so two open windows never overwrite each other's scans
        self.temp_dir = tempfile.mkdtemp(prefix="scan_", dir=TEMP_DIR)
        self.f_tmp = os.path.join(self.temp_dir, "front_tmp.pdf")
        self.b_tmp = os.path.join(self.temp_dir, "back_tmp.pdf")
        self.flatbed_tmps = []

        self.setup_ui()

    @property
    def is_flatbed_selected(self):
        """Returns True if the currently selected profile is for the flatbed scanner."""
        current_profile = self.profile_var.get()
        current_printer = self.printer_var.get()
        profiles = self.profiles_by_printer.get(current_printer, [])
        for p in profiles:
            if p["display_name"] == current_profile:
                return p["source_type"] == "flatbed"
        return "flatbed" in current_profile.lower() or "glass" in current_profile.lower()

    def setup_ui(self):
        tk.Label(self.root, text="Scanner Control", font=("Arial", 12, "bold"), pady=8).pack()

        # Printer selection bar & Configure button
        printer_bar = tk.Frame(self.root, padx=20, pady=4)
        printer_bar.pack(fill="x")

        tk.Label(printer_bar, text="Printer:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 6))

        self.printer_dropdown = ttk.Combobox(
            printer_bar,
            textvariable=self.printer_var,
            state="readonly"
        )
        self.printer_dropdown.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.printer_dropdown.bind("<<ComboboxSelected>>", self.on_printer_changed)

        self.btn_config = ttk.Button(printer_bar, text="⚙ Configure", command=self.open_config_dialog)
        self.btn_config.pack(side="right")

        # Settings frame for profiles
        self.opt_frame = tk.LabelFrame(self.root, text="Settings", padx=15, pady=8)
        self.opt_frame.pack(padx=20, pady=6, fill="x")

        # Dynamic profile radio buttons container
        self.profiles_container = tk.Frame(self.opt_frame)
        self.profiles_container.pack(fill="x")

        # Separator line
        tk.Frame(self.opt_frame, height=1, bg="grey").pack(fill="x", pady=6)

        # Duplex checkbox
        self.chk_duplex = tk.Checkbutton(
            self.opt_frame,
            text="Scan Duplex (both sides)",
            variable=self.duplex_var
        )
        self.chk_duplex.pack(anchor="w")

        # Start button
        self.btn_start = tk.Button(
            self.root,
            text="Start Scanning",
            command=self.start_workflow,
            bg="#4CAF50",
            fg="white",
            font=("Arial", 10, "bold"),
            height=2
        )
        self.btn_start.pack(pady=12, padx=20, fill="x")

        # Status label
        tk.Label(self.root, textvariable=self.status_var, fg="blue").pack()

        # Populate printer dropdown and radio buttons
        self.update_printer_dropdown()
        self.update_profiles_ui()
        logging.info("UI successfully initialized.")

        if CONFIG_ERROR:
            messagebox.showwarning("Config Error", f"config.json could not be read and was renamed to config.json.bad. Default settings are in use.\n\n{CONFIG_ERROR}", parent=self.root)

    def update_printer_dropdown(self):
        """Updates the printer list in the dropdown and manages its active/disabled state."""
        printer_names = list(self.profiles_by_printer.keys())
        self.printer_dropdown["values"] = printer_names

        if not printer_names:
            self.printer_var.set("No printer found in profiles.xml")
            self.printer_dropdown.config(state="disabled")
            return

        # Preselect saved printer or first available printer
        saved_printer = self.config.get("selected_printer", "")
        if saved_printer in printer_names:
            selected = saved_printer
        else:
            selected = printer_names[0]
            self.config["selected_printer"] = selected
            save_config(self.config)

        self.printer_var.set(selected)

        # Dropdown is active (readonly) if multiple printers exist; disabled if only one
        if len(printer_names) > 1:
            self.printer_dropdown.config(state="readonly")
        else:
            self.printer_dropdown.config(state="disabled")

    def on_printer_changed(self, event=None):
        """Triggered when the user selects a different printer in the dropdown."""
        selected = self.printer_var.get()
        logging.info(f"Printer changed to: {selected}")
        self.config["selected_printer"] = selected
        save_config(self.config)
        self.update_profiles_ui()

    def update_profiles_ui(self):
        """Dynamically generates the radio buttons for the selected printer's profiles."""
        for widget in self.profiles_container.winfo_children():
            widget.destroy()

        current_printer = self.printer_var.get()
        profiles = self.profiles_by_printer.get(current_printer, [])

        if not profiles:
            tk.Label(
                self.profiles_container,
                text="No profiles available for this printer.",
                fg="red"
            ).pack(anchor="w", pady=4)
            self.profile_var.set("")
            self.toggle_duplex_state()
            return

        adf_profiles = [p for p in profiles if p["source_type"] == "adf"]
        flatbed_profiles = [p for p in profiles if p["source_type"] == "flatbed"]

        # ADF Profiles
        if adf_profiles:
            tk.Label(self.profiles_container, text="Feeder (ADF):", font=("Arial", 9, "bold")).pack(anchor="w", pady=(2, 0))
            for p in adf_profiles:
                tk.Radiobutton(
                    self.profiles_container,
                    text=p["label"],
                    variable=self.profile_var,
                    value=p["display_name"],
                    command=self.toggle_duplex_state
                ).pack(anchor="w")

        # Flatbed Profiles
        if flatbed_profiles:
            tk.Label(self.profiles_container, text="Flatbed:", font=("Arial", 9, "bold"), pady=2).pack(anchor="w", pady=(4, 0))
            for p in flatbed_profiles:
                tk.Radiobutton(
                    self.profiles_container,
                    text=p["label"],
                    variable=self.profile_var,
                    value=p["display_name"],
                    command=self.toggle_duplex_state
                ).pack(anchor="w")

        # Default profile preselection
        all_display_names = [p["display_name"] for p in profiles]
        current_val = self.profile_var.get()

        if current_val not in all_display_names:
            # 1. Prefer ADF 300 DPI for standard scans
            adf_300 = next((p for p in adf_profiles if p["resolution_dpi"] == 300), None)
            if adf_300:
                self.profile_var.set(adf_300["display_name"])
            elif adf_profiles:
                self.profile_var.set(adf_profiles[0]["display_name"])
            else:
                default_p = next((p for p in profiles if p.get("is_default")), None)
                if default_p:
                    self.profile_var.set(default_p["display_name"])
                elif profiles:
                    self.profile_var.set(profiles[0]["display_name"])

        self.toggle_duplex_state()

    def toggle_duplex_state(self):
        """Disables the duplex checkbox when a flatbed profile is active."""
        if self.is_flatbed_selected:
            self.chk_duplex.config(state="disabled")
            self.duplex_var.set(False)
        else:
            self.chk_duplex.config(state="normal")

    def open_config_dialog(self):
        """Opens the configuration / settings dialog."""
        ConfigDialog(self.root, self.config, self.on_config_saved)

    def on_config_saved(self, new_config):
        """Callback executed when settings are saved in ConfigDialog."""
        self.config = new_config
        self.naps2_path = resolve_naps2_path(self.config.get("naps2_path", ""))

        # Re-configure logging with new log path & level
        setup_logger(self.config.get("log_file_path", DEFAULT_LOG_FILE), self.config.get("log_level", "INFO"))

        # Update Always on Top window attribute
        self.root.attributes("-topmost", self.config.get("always_on_top", True))

        # Reload profiles from XML
        xml_path = self.config.get("profiles_xml_path", "")
        self.profiles_by_printer = parse_profiles_xml(xml_path)

        # Refresh UI
        self.update_printer_dropdown()
        self.update_profiles_ui()
        self.status_var.set("Settings applied.")
        logging.info("Configuration updated and reloaded.")

    def scan(self, filename):
        """Executes a scan operation using NAPS2.Console."""
        self.status_var.set("Scanner is busy...")
        self.root.config(cursor="watch")
        self.root.update()

        try:
            cmd = [self.naps2_path, "-o", filename, "-p", self.profile_var.get(), "--force"]
            creationflags = subprocess.CREATE_NO_WINDOW if (os.name == "nt" and self.config.get("hide_console_window", True)) else 0

            logging.info(f"NAPS2 Command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                creationflags=creationflags
            )

            if result.returncode == 0:
                logging.info(f"Scan succeeded: {filename}")
                return True
            else:
                stdout_text = result.stdout if result.stdout else ""
                stderr_text = result.stderr if result.stderr else ""
                logging.warning(f"NAPS2 error (Exit code {result.returncode}): {stdout_text} {stderr_text}")

                if "In der Zuführung sind keine Seiten" in stdout_text or "No scanned pages" in stdout_text or "feeder is empty" in stdout_text.lower():
                    messagebox.showwarning("Scanner Empty", "Please place documents in the feeder or on the flatbed.")
                else:
                    messagebox.showerror("Scan Error", f"NAPS2 Error:\n{stdout_text}\n{stderr_text}")
                return False
        except Exception as e:
            logging.exception("Exception during scan operation:")
            messagebox.showerror("System Error", str(e))
            return False
        finally:
            self.root.config(cursor="")
            self.root.update()

    def start_workflow(self):
        """Initiates the scanning workflow based on selected mode (Flatbed or ADF)."""
        if not self.profile_var.get():
            messagebox.showwarning("No Profile", "No scan profile is selected. Check profiles.xml.")
            return
        self.btn_start.config(state="disabled")
        self.btn_config.config(state="disabled")

        try:
            if self.is_flatbed_selected:
                # Flatbed: Sequential multi-page scan loop
                self.flatbed_tmps = []
                page_counter = 1

                while True:
                    current_tmp = os.path.join(self.temp_dir, f"flatbed_tmp_{page_counter}.pdf")
                    logging.info(f"Scanning page {page_counter} on flatbed...")

                    if self.scan(current_tmp):
                        self.flatbed_tmps.append(current_tmp)
                        ans = messagebox.askyesno(
                            "Next Page?",
                            "Would you like to place another page on the flatbed and scan?"
                        )
                        if not ans:
                            break
                        page_counter += 1
                    else:
                        retry = messagebox.askretrycancel("Scan Error", "Scan failed. Retry?")
                        if not retry:
                            break

                if self.flatbed_tmps:
                    self._save_pdf(lambda: [p for f in self.flatbed_tmps for p in read_pages(f)], self.flatbed_tmps)

            else:
                # ADF: Front side scan, then optional back side scan
                if self.scan(self.f_tmp):
                    has_back = False

                    if self.duplex_var.get():
                        if messagebox.askokcancel("Step 2: Duplex", "Turn the stack OVER and reload into the feeder.\nThen click OK."):
                            while True:
                                if self.scan(self.b_tmp):
                                    has_back = True
                                    break
                                else:
                                    retry = messagebox.askretrycancel(
                                        "Scan Failed",
                                        "The back-side scan was not successful.\n\n"
                                        "Please check the scanner and click 'Retry' or 'Cancel'."
                                    )
                                    if not retry:
                                        logging.info("Back-side scan cancelled by user.")
                                        break

                    self.process_adf_files(has_back)

        finally:
            self.reset()

    def process_adf_files(self, has_back):
        """Merges ADF front and back pages with duplex interleaving."""
        logging.info(f"Processing ADF files (Back pages present: {has_back})...")

        def build_pages():
            pages_f = read_pages(self.f_tmp)
            pages_b = read_pages(self.b_tmp) if has_back else []

            # Plausibility check for mismatched page counts
            if has_back and self.config.get("warn_page_mismatch", True) and len(pages_f) != len(pages_b):
                logging.warning(f"Page count mismatch: {len(pages_f)} front vs. {len(pages_b)} back pages.")
                messagebox.showwarning(
                    "Page Count Notice",
                    f"Notice: {len(pages_f)} front page(s) and {len(pages_b)} back page(s) were scanned.\n\n"
                    "Please verify the generated PDF for missing or double-fed pages."
                )
            return interleave_pages(pages_f, pages_b)

        return self._save_pdf(build_pages, [self.f_tmp, self.b_tmp])

    def _save_pdf(self, build_pages, temp_files):
        """Asks for a file name, writes the pages from build_pages(), then deletes temp_files."""
        while True:
            save_path = filedialog.asksaveasfilename(
                defaultextension=".pdf",
                filetypes=[("PDF Document", "*.pdf")]
            )

            if not save_path:
                if self.config.get("confirm_discard", True) and not messagebox.askyesno("Discard Scan?", "Do you really want to discard the scanned pages?"):
                    logging.info("Save cancelled; asking for a file name again.")
                    continue
                self._cleanup_files(temp_files)
                return False

            try:
                writer = PdfWriter()
                for page in build_pages():
                    writer.add_page(page)
                with open(save_path, "wb") as f_out:
                    writer.write(f_out)

                logging.info(f"PDF successfully saved: {save_path}")
                messagebox.showinfo("Success", "PDF created successfully!")
                self._cleanup_files(temp_files)
                return True
            except Exception as e:
                logging.exception("Error merging PDF files:")
                messagebox.showerror("Merge Error", f"Error merging PDF files:\n{str(e)}\n\nPlease choose another file name.")

    def _cleanup_files(self, file_paths):
        """Safely deletes temporary scan files."""
        for path in file_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    logging.debug(f"Temporary file removed: {path}")
                except Exception as e:
                    logging.warning(f"Could not remove {path}: {e}")

    def reset(self):
        """Resets the UI back to ready state."""
        logging.info("Application state reset.\n" + "-" * 50)
        self.status_var.set("Ready")
        self.btn_start.config(state="normal")
        self.btn_config.config(state="normal")


if __name__ == "__main__":
    app = ScanApp()
    app.root.mainloop()
    shutil.rmtree(app.temp_dir, ignore_errors=True)
