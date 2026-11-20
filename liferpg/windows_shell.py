"""Windows login registration, single instance and notification area."""

import ctypes
import hashlib
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def workspace_id(workspace):
    return hashlib.sha256(str(workspace).casefold().encode()).hexdigest()[:20]


def installed_executable():
    """Builds inside a checkout must register its permanent installed EXE."""
    current = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        for directory in current.parents:
            if (directory != current.parent
                    and current.relative_to(directory).parts[0] != "dist"):
                continue
            candidate = directory / "LifeRPG.exe"
            if ((directory / "LifeRPG.spec").is_file()
                    and candidate.is_file()):
                return candidate
    return current


def configure_startup(workspace, enabled, *, executable=None):
    name = "LifeRPG-" + workspace_id(workspace)
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access
    ) as key:
        if enabled:
            args = [str(executable or installed_executable())]
            if executable is None and not getattr(sys, "frozen", False):
                args.append(str(Path(sys.argv[0]).resolve()))
            args += ["--workspace", str(workspace), "--login"]
            command = subprocess.list2cmdline(args)
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
            if winreg.QueryValueEx(key, name)[0] != command:
                raise OSError("Проверка пути автозапуска не прошла.")
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


class Instance:
    """A per-workspace Windows mutex and an event for raising the window."""

    def __init__(self, workspace):
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, types, result in (
            ("CreateMutexW", (wintypes.LPVOID, wintypes.BOOL,
                              wintypes.LPCWSTR),
             wintypes.HANDLE),
            ("CreateEventW", (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                              wintypes.LPCWSTR), wintypes.HANDLE),
            ("SetEvent", (wintypes.HANDLE,), wintypes.BOOL),
            ("WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD),
             wintypes.DWORD),
            ("CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
        ):
            func = getattr(kernel, name)
            func.argtypes, func.restype = types, result
        prefix = r"Local\LifeRPG-" + workspace_id(workspace)
        self.raise_event = kernel.CreateEventW(
            None, False, False, prefix + "-show"
        )
        if not self.raise_event:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.set_last_error(0)
        self.mutex = kernel.CreateMutexW(None, False, prefix)
        error = ctypes.get_last_error()
        if not self.mutex:
            kernel.CloseHandle(self.raise_event)
            raise ctypes.WinError(error)
        self.first = error != 183

    def signal(self):
        self.kernel.SetEvent(self.raise_event)

    def show_requested(self):
        return self.kernel.WaitForSingleObject(self.raise_event, 0) == 0

    def close(self):
        self.kernel.CloseHandle(self.mutex)
        self.kernel.CloseHandle(self.raise_event)


class Tray:
    def __init__(self, window, instance, values, icon_file):
        self.window = window
        self.instance = instance
        self.values = values
        self.icon_file = icon_file
        self.exiting = False
        self.icon = None

    def attach(self):
        from System.Drawing import Icon
        from System.Windows.Forms import (
            ContextMenuStrip, FormWindowState, MouseButtons, NotifyIcon, Timer,
        )

        form = self.window.native
        # Native Windows chrome preserves resizing, keyboard and snap behavior.
        hwnd = int(form.Handle.ToInt64())
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = (
            wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD,
        )
        for attribute, color in ((20, 1), (35, 0x171A14), (36, 0xECF0EA)):
            value = ctypes.c_int(color)
            dwm.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
        icon = self.icon = NotifyIcon()
        menu = ContextMenuStrip()

        def show(*_):
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.Activate()
            icon.Visible = self.values["tray"]

        def exit_app(*_):
            self.exiting = True
            form.Close()

        def click(_, event):
            if event.Button == MouseButtons.Left:
                show()

        def tick(*_):
            if self.instance.show_requested():
                show()
            else:
                icon.Visible = self.values["tray"] or not form.Visible

        menu.Items.Add("Открыть").Click += show
        menu.Items.Add("Выйти").Click += exit_app
        window_icon = Icon(str(self.icon_file))
        tray_icon = Icon(window_icon, 32, 32)
        form.Icon = window_icon
        icon.Icon = tray_icon
        icon.Text = "LifeRPG"
        icon.ContextMenuStrip = menu
        icon.MouseDoubleClick += click
        timer = Timer()
        timer.Interval = 250
        timer.Tick += tick

        def dispose(*_):
            timer.Stop()
            icon.Visible = False
            icon.Dispose()
            tray_icon.Dispose()
            menu.Dispose()
            timer.Dispose()

        form.FormClosed += dispose
        tick()
        timer.Start()

    def on_closing(self):
        if not self.exiting and self.values["tray"] and self.icon is not None:
            self.icon.Visible = True
            self.window.native.Hide()
            return False
        return True
