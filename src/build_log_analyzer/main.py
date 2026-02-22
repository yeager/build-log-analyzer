"""Build Log Analyzer — Parse sbuild/pbuilder logs with error highlighting."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Pango

import gettext
import locale
import os
import sys
import json
import datetime
import threading
import subprocess
import re

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "po")
if not os.path.isdir(LOCALE_DIR):
    LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain("build-log-analyzer", LOCALE_DIR)
gettext.bindtextdomain("build-log-analyzer", LOCALE_DIR)
gettext.textdomain("build-log-analyzer")
_ = gettext.gettext

APP_ID = "se.danielnylander.build.log.analyzer"
SETTINGS_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "build-log-analyzer"
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {"welcome_shown": False}


def _save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)



def _analyze_log(text):
    """Analyze a build log for errors, warnings, and patterns."""
    results = {"errors": [], "warnings": [], "info": [], "summary": {}}
    lines = text.splitlines()
    results["summary"]["total_lines"] = len(lines)
    
    error_patterns = [
        (r"error:", "compiler"),
        (r"E: ", "dpkg"),
        (r"FAIL", "test"),
        (r"undefined reference", "linker"),
        (r"No such file or directory", "missing-file"),
        (r"Permission denied", "permission"),
    ]
    warning_patterns = [
        (r"warning:", "compiler"),
        (r"W: ", "dpkg"),
        (r"deprecated", "deprecation"),
    ]
    
    for i, line in enumerate(lines):
        for pat, category in error_patterns:
            if re.search(pat, line, re.IGNORECASE):
                results["errors"].append({"line": i + 1, "text": line.strip(), "category": category})
                break
        for pat, category in warning_patterns:
            if re.search(pat, line, re.IGNORECASE):
                results["warnings"].append({"line": i + 1, "text": line.strip(), "category": category})
                break
    
    results["summary"]["errors"] = len(results["errors"])
    results["summary"]["warnings"] = len(results["warnings"])
    
    # Detect FTBFS
    if any("dh_auto_build" in e["text"] for e in results["errors"]):
        results["info"].append(_("Build failure detected in dh_auto_build"))
    if any("dh_auto_test" in e["text"] for e in results["errors"]):
        results["info"].append(_("Test failure detected"))
    
    return results



class BuildLogAnalyzerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Build Log Analyzer"), default_width=1100, default_height=750)
        self.settings = _load_settings()
        self._log_text = ''

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title=_("Build Log Analyzer"), subtitle="")
        headerbar.set_title_widget(title_widget)
        self._title_widget = title_widget

        
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open build log"))
        open_btn.connect("clicked", self._on_open)
        headerbar.pack_start(open_btn)
        
        self._err_label = Gtk.Label(label="")
        self._err_label.add_css_class("error")
        headerbar.pack_end(self._err_label)
        
        self._warn_label = Gtk.Label(label="")
        self._warn_label.add_css_class("warning")
        headerbar.pack_end(self._warn_label)

        # Menu
        menu = Gio.Menu()
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Copy Debug Info"), "app.copy-debug")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About Build Log Analyzer"), "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        headerbar.pack_end(menu_btn)

        main_box.append(headerbar)

        
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_vexpand(True)
        
        # Top: issue list
        top_scroll = Gtk.ScrolledWindow(min_content_height=200)
        self._issue_list = Gtk.ListBox()
        self._issue_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._issue_list.add_css_class("boxed-list")
        self._issue_list.set_margin_start(12)
        self._issue_list.set_margin_end(12)
        self._issue_list.set_margin_top(8)
        top_scroll.set_child(self._issue_list)
        paned.set_start_child(top_scroll)
        
        # Bottom: raw log
        bottom_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._log_view = Gtk.TextView(editable=False, monospace=True)
        self._log_view.set_top_margin(8)
        self._log_view.set_left_margin(8)
        bottom_scroll.set_child(self._log_view)
        paned.set_end_child(bottom_scroll)
        paned.set_position(250)
        
        main_box.append(paned)

        # Status bar
        self._status = Gtk.Label(label=_("Ready"), xalign=0)
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self._status.set_margin_top(4)
        self._status.set_margin_bottom(4)
        self._status.add_css_class("dim-label")
        main_box.append(self._status)

        self.set_content(main_box)

        if not self.settings.get("welcome_shown"):
            GLib.idle_add(self._show_welcome)

    def _show_welcome(self):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)

        page = Adw.StatusPage()
        page.set_icon_name("utilities-terminal-symbolic")
        page.set_title(_("Welcome to Build Log Analyzer"))
        page.set_description(_("Analyze build logs easily.\n\n"
            "✓ Parse sbuild and pbuilder logs\n"
            "✓ Highlight errors and warnings\n"
            "✓ Detect common FTBFS patterns\n"
            "✓ Jump to error locations\n"
            "✓ Summary statistics"))

        btn = Gtk.Button(label=_("Get Started"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(12)
        btn.connect("clicked", self._on_welcome_close, dialog)
        page.set_child(btn)

        box = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_show_title(False)
        box.add_top_bar(hb)
        box.set_content(page)
        dialog.set_child(box)
        dialog.present(self)

    def _on_welcome_close(self, btn, dialog):
        self.settings["welcome_shown"] = True
        _save_settings(self.settings)
        dialog.close()

    
    def _on_open(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Open Build Log"))
        dialog.open(self, None, self._on_file_opened)

    def _on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            path = f.get_path()
            with open(path) as fh:
                self._log_text = fh.read()
            self._log_view.get_buffer().set_text(self._log_text)
            self._title_widget.set_subtitle(os.path.basename(path))
            threading.Thread(target=self._do_analyze, daemon=True).start()
        except:
            pass

    def _do_analyze(self):
        results = _analyze_log(self._log_text)
        GLib.idle_add(self._show_results, results)

    def _show_results(self, results):
        while True:
            row = self._issue_list.get_row_at_index(0)
            if row is None:
                break
            self._issue_list.remove(row)
        
        for info_text in results.get("info", []):
            row = Adw.ActionRow()
            row.set_title("ℹ️ " + info_text)
            self._issue_list.append(row)
        
        for err in results["errors"][:100]:
            row = Adw.ActionRow()
            row.set_title("❌ " + err["text"][:100])
            row.set_subtitle(_("Line %(line)d — %(cat)s") % {"line": err["line"], "cat": err["category"]})
            self._issue_list.append(row)
        
        for warn in results["warnings"][:50]:
            row = Adw.ActionRow()
            row.set_title("⚠️ " + warn["text"][:100])
            row.set_subtitle(_("Line %(line)d — %(cat)s") % {"line": warn["line"], "cat": warn["category"]})
            self._issue_list.append(row)
        
        s = results["summary"]
        self._err_label.set_text(_("%(count)d errors") % {"count": s["errors"]})
        self._warn_label.set_text(_("%(count)d warnings") % {"count": s["warnings"]})
        self._status.set_text(_("%(lines)d lines, %(errors)d errors, %(warnings)d warnings") %
                            {"lines": s["total_lines"], "errors": s["errors"], "warnings": s["warnings"]})


class BuildLogAnalyzerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

        for name, callback in [
            ("settings", self._on_settings),
            ("copy-debug", self._on_copy_debug),
            ("shortcuts", self._on_shortcuts),
            ("about", self._on_about),
            ("quit", self._on_quit),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.shortcuts", ["<Ctrl>slash"])

    def do_activate(self):
        if not self.window:
            self.window = BuildLogAnalyzerWindow(self)
        self.window.present()

    def _on_settings(self, *_args):
        if not self.window:
            return
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Settings"))
        page = Adw.PreferencesPage()
        
        group = Adw.PreferencesGroup(title=_("Analysis"))
        row = Adw.SwitchRow(title=_("Show info messages"))
        row.set_active(True)
        group.add(row)
        page.add(group)
        dialog.add(page)
        dialog.present(self.window)

    def _on_copy_debug(self, *_args):
        if not self.window:
            return
        from . import __version__
        info = (
            f"Build Log Analyzer {__version__}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_args):
        if self.window:
            dialog = Gtk.ShortcutsWindow(transient_for=self.window)
            section = Gtk.ShortcutsSection(visible=True)
            group = Gtk.ShortcutsGroup(title=_("General"), visible=True)
            for accel, title in [
                ("<Ctrl>q", _("Quit")),
                ("<Ctrl>slash", _("Keyboard shortcuts")),
            ]:
                group.append(Gtk.ShortcutsShortcut(accelerator=accel, title=title, visible=True))
            section.append(group)
            dialog.append(section)
            dialog.present()

    def _on_about(self, *_args):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("Build Log Analyzer"),
            application_icon="utilities-terminal-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/build-log-analyzer",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/build-log-analyzer/issues",
            comments=_("Parse sbuild and pbuilder build logs. Highlight errors, warnings, and FTBFS patterns."),
        )
        dialog.present(self.window)

    def _on_quit(self, *_args):
        self.quit()


def main():
    app = BuildLogAnalyzerApp()
    app.run(sys.argv)
