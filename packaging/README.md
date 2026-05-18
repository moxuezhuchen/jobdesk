# Windows GUI Packaging

Use `packaging/windows/jobdesk.exe.manifest` for Windows GUI builds. The
manifest declares `PerMonitorV2` DPI awareness so Windows does not bitmap-scale
the Qt-rendered window on high-DPI displays.

PyInstaller options:

```powershell
pyinstaller packaging\pyinstaller\jobdesk-gui.spec
```

Equivalent direct command:

```powershell
pyinstaller --noconsole --name JobDesk --paths src --manifest packaging\windows\jobdesk.exe.manifest packaging\pyinstaller\jobdesk_gui_entry.py
```

Nuitka option:

```powershell
python -m nuitka --standalone --windows-console-mode=disable --windows-manifest-file=packaging\windows\jobdesk.exe.manifest src\jobdesk_app\gui\app.py
```
