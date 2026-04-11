from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class LocalDirectoryPickerService:
    def select_directory(self, initial_path: str | None = None) -> str | None:
        normalized_initial_path = self._normalize_initial_path(initial_path)

        if sys.platform == "darwin":
            handled, selected_path = self._select_directory_with_osascript(
                normalized_initial_path
            )
            if handled:
                return selected_path

        return self._select_directory_with_tk(normalized_initial_path)

    def _normalize_initial_path(self, initial_path: str | None) -> Path | None:
        if not isinstance(initial_path, str) or not initial_path.strip():
            return None

        candidate = Path(initial_path).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        if candidate.exists():
            return candidate.parent.resolve()
        if candidate.parent.exists():
            return candidate.parent.resolve()
        return None

    def _select_directory_with_osascript(
        self, initial_path: Path | None
    ) -> tuple[bool, str | None]:
        script = [
            'set chosenFolder to choose folder with prompt "Select a project folder"'
        ]
        if initial_path is not None:
            escaped_path = self._escape_applescript_text(str(initial_path))
            script = [
                f'set chosenFolder to choose folder with prompt "Select a project folder" default location POSIX file "{escaped_path}"'
            ]
        script.append("POSIX path of chosenFolder")

        result = subprocess.run(
            ["osascript", *sum([["-e", line] for line in script], [])],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            selected_path = result.stdout.strip()
            if not selected_path:
                return True, None
            return True, str(Path(selected_path).expanduser().resolve())

        stderr = result.stderr.strip()
        if "User canceled" in stderr or "(-128)" in stderr:
            return True, None
        return False, None

    def _select_directory_with_tk(self, initial_path: Path | None) -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            return None

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        try:
            selected_path = filedialog.askdirectory(
                initialdir=str(initial_path) if initial_path is not None else None,
                mustexist=True,
                title="Select a project folder",
                parent=root,
            )
        finally:
            root.destroy()

        if not selected_path:
            return None
        return str(Path(selected_path).expanduser().resolve())

    def _escape_applescript_text(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
