# NAPS Scanner GUI

A small Tkinter front end for scanning with [NAPS2](https://www.naps2.com/) on Windows. It drives `NAPS2.Console.exe`, lets you pick a printer and scan profile, scans single-sided or duplex (ADF), or page by page from the flatbed, and merges the pages into one PDF. For duplex ADF scans, the back side is interleaved with the front side, so a stack scanned bottom-up comes out in the right order.

## Requirements

- Windows
- Python 3.8 or newer
- [NAPS2](https://www.naps2.com/) with at least one scan profile saved in NAPS2
- Python packages: `pypdf` (or the older `PyPDF2`)

```bash
pip install pypdf
```

`tkinter` ships with the standard Python installer on Windows.

## Setup

1. Copy `config.json.example` to `config.json`.
2. Adjust the paths and the printer name:
   - `naps2_path`: full path to `NAPS2.Console.exe`
   - `profiles_xml_path`: full path to NAPS2's `profiles.xml` (usually `%APPDATA%\NAPS2\profiles.xml`)
   - `selected_printer`: the printer name as it appears in `profiles.xml`
   - `log_file_path`: where the log is written
3. Start the app.

If `config.json` is missing, the app creates it with default values. Most options can also be changed in the app via **⚙ Configure**.

## Usage

1. Run `autoscan.pyw` (double-click, or `pythonw autoscan.pyw`). The window opens without a console.
2. Choose the printer and a scan profile. ADF profiles are listed first, flatbed profiles below them.
3. For ADF scans, tick **Scan Duplex** if you want both sides.
   - The first pass scans the front. For duplex, turn the stack over, reload it, and confirm.
4. For flatbed scans, the app asks after each page whether to scan another one.
5. Pick a file name to save the merged PDF. Temporary scan files are removed afterwards.

## Configuration options

| Key | Meaning |
| --- | --- |
| `naps2_path` | Path to `NAPS2.Console.exe`. Common install locations are searched if the path is wrong. |
| `profiles_xml_path` | Path to NAPS2's `profiles.xml`. Falls back to `%APPDATA%\NAPS2\profiles.xml`. |
| `log_file_path` | Log file location. |
| `log_level` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `selected_printer` | Printer selected at startup. |
| `default_duplex` | Duplex checkbox state at startup. |
| `always_on_top` | Keep the window above other windows. |
| `confirm_discard` | Ask before discarding unsaved scans. |
| `warn_page_mismatch` | Warn when front and back page counts differ. |
| `hide_console_window` | Hide the NAPS2 console window during scans. |
| `temp_dir` | Folder for temporary scan files. `null` uses the system temp folder. |

## Files

- `autoscan.pyw`: the application
- `scanner.ico`: window icon
- `config.json.example`: template for the local configuration
- `config.json`: your local configuration (not committed, see `.gitignore`)
