"""
Local App Connector
====================
Bridge between Jambubrowser and local productivity applications.
macOS-first implementation using AppleScript/Shortcuts.

Supported integrations:
- Obsidian vault (read, write, append, search)
- macOS Reminders (create, list, complete)
- Apple Notes (create, append)
- To-Do apps (Things, Todoist via URL schemes)
- Clipboard (read, write)
- Filesystem (read, write, search markdown files)
"""

import os
import re
import json
import subprocess
import platform
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass


def _is_macos() -> bool:
    return platform.system() == 'Darwin'


def _run_applescript(script: str) -> Optional[str]:
    """Run an AppleScript and return stdout, or None on failure."""
    if not _is_macos():
        return None
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception:
        return None


# ===================================================================
# OBSIDIAN INTEGRATION
# ===================================================================

class ObsidianConnector:
    """
    Read, write, and search an Obsidian vault.
    Supports:
    - Creating new notes
    - Appending to existing notes
    - Searching vault content
    - Reading notes by path/title
    """

    DEFAULT_VAULT_PATH = Path.home() / "Documents" / "Obsidian"

    def __init__(self, vault_path: str = None):
        self.vault_path = Path(vault_path) if vault_path else self.DEFAULT_VAULT_PATH
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def _find_note(self, title: str) -> Optional[Path]:
        """Find a note file by title (with or without .md extension)."""
        search_name = title if title.endswith('.md') else f"{title}.md"
        for root, dirs, files in os.walk(self.vault_path):
            if search_name in files:
                return Path(root) / search_name
        return None

    def create_note(self, title: str, content: str, folder: str = "Research") -> dict:
        """Create a new note in the vault."""
        target_dir = self.vault_path / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_title = re.sub(r'[<>:"/\\|?*]', '-', title)
        if not safe_title.endswith('.md'):
            safe_title += '.md'

        filepath = target_dir / safe_title

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        full_content = f"---\ncreated: {timestamp}\nsource: Jambubrowser\n---\n\n{content}"

        filepath.write_text(full_content)

        return {
            'success': True,
            'action': 'created',
            'path': str(filepath.relative_to(self.vault_path)),
            'title': title,
            'size': len(full_content),
        }

    def append_to_note(self, title: str, content: str) -> dict:
        """Append content to an existing note. Creates if it doesn't exist."""
        existing = self._find_note(title)
        if existing:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
            with open(existing, 'a') as f:
                f.write(f"\n\n---\n**Appended {timestamp} (Jambubrowser)**\n\n{content}")
            return {
                'success': True,
                'action': 'appended',
                'path': str(existing.relative_to(self.vault_path)),
                'title': title,
            }
        else:
            return self.create_note(title, content)

    def read_note(self, title: str) -> dict:
        """Read the contents of a note."""
        existing = self._find_note(title)
        if not existing:
            return {'success': False, 'error': f'Note not found: {title}'}

        content = existing.read_text()
        return {
            'success': True,
            'path': str(existing.relative_to(self.vault_path)),
            'title': title,
            'content': content[:50000],
            'size': len(content),
        }

    def search_vault(self, query: str, max_results: int = 10) -> dict:
        """Search vault content for matching notes."""
        results = []
        query_lower = query.lower()

        for root, dirs, files in os.walk(self.vault_path):
            # Skip hidden directories and system files
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for filename in files:
                if not filename.endswith('.md'):
                    continue

                filepath = Path(root) / filename
                if filepath.stat().st_size > 5 * 1024 * 1024:
                    continue  # Skip huge files

                try:
                    content = filepath.read_text()
                except Exception:
                    continue

                # Check title match
                title_match = query_lower in filename.lower()

                # Check content match
                content_lines = content.split('\n')
                matching_lines = []
                for i, line in enumerate(content_lines):
                    if query_lower in line.lower():
                        matching_lines.append({
                            'line': i + 1,
                            'text': line.strip()[:200],
                        })

                if title_match or matching_lines:
                    results.append({
                        'path': str(filepath.relative_to(self.vault_path)),
                        'title': filename.replace('.md', ''),
                        'title_match': title_match,
                        'matches': len(matching_lines),
                        'snippets': matching_lines[:3],
                    })

                if len(results) >= max_results:
                    break

        return {
            'success': True,
            'query': query,
            'results': results,
            'total_found': len(results),
        }

    def list_vault(self, folder: str = "") -> dict:
        """List notes in the vault or a specific folder."""
        target = self.vault_path / folder if folder else self.vault_path
        if not target.exists():
            return {'success': False, 'error': f'Folder not found: {folder}'}

        notes = []
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.endswith('.md'):
                    fpath = Path(root) / f
                    notes.append({
                        'path': str(fpath.relative_to(self.vault_path)),
                        'title': f.replace('.md', ''),
                        'size': fpath.stat().st_size,
                    })

        return {
            'success': True,
            'folder': folder or 'root',
            'count': len(notes),
            'notes': notes[:100],
        }

    def get_stats(self) -> dict:
        """Get vault statistics."""
        total_notes = 0
        total_size = 0
        for root, dirs, files in os.walk(self.vault_path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.endswith('.md'):
                    total_notes += 1
                    total_size += (Path(root) / f).stat().st_size

        return {
            'vault_path': str(self.vault_path),
            'total_notes': total_notes,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
        }


# ===================================================================
# MACOS REMINDERS INTEGRATION
# ===================================================================

class RemindersConnector:
    """Create and manage macOS Reminders."""

    def create_reminder(self, title: str, notes: str = "", 
                         due_date: str = "", list_name: str = "Jambubrowser") -> dict:
        """
        Create a reminder in macOS Reminders app.

        Args:
            title: Reminder title
            notes: Additional notes
            due_date: ISO format datetime string
            list_name: Reminders list name
        """
        if not _is_macos():
            return {'success': False, 'error': 'Reminders only available on macOS'}

        script = f'''
        tell application "Reminders"
            set targetList to list "{list_name}"
            set newReminder to make new reminder in targetList with properties {{name:"{title}", body:"{notes}"}}
        '''
        if due_date:
            script += f'\nset due date of newReminder to date "{due_date}"'
        script += '\nend tell'

        result = _run_applescript(script)
        return {'success': result is not None, 'title': title, 'list': list_name}

    def list_reminders(self, list_name: str = "Jambubrowser") -> dict:
        """List reminders in a specific list."""
        if not _is_macos():
            return {'success': False, 'error': 'Reminders only available on macOS'}

        script = f'''
        tell application "Reminders"
            set output to ""
            repeat with r in reminders of list "{list_name}"
                set output to output & name of r & "|||" & (body of r) & "|||" & (completed of r as string) & "\\n"
            end repeat
            return output
        end tell
        '''

        result = _run_applescript(script)
        if not result:
            return {'success': False}

        reminders = []
        for line in result.strip().split('\n'):
            if '|||' in line:
                parts = line.split('|||')
                reminders.append({
                    'title': parts[0],
                    'notes': parts[1] if len(parts) > 1 else '',
                    'completed': parts[2] == 'true' if len(parts) > 2 else False,
                })

        return {'success': True, 'list': list_name, 'reminders': reminders, 'count': len(reminders)}


# ===================================================================
# CLIPBOARD INTEGRATION
# ===================================================================

class ClipboardConnector:
    """Read and write system clipboard."""

    def copy(self, text: str) -> dict:
        """Copy text to clipboard."""
        if _is_macos():
            subprocess.run(['pbcopy'], input=text.encode(), timeout=5)
            return {'success': True, 'action': 'copied', 'length': len(text)}
        return {'success': False, 'error': 'Unsupported platform'}

    def paste(self) -> dict:
        """Get clipboard contents."""
        if _is_macos():
            result = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=5)
            return {'success': True, 'content': result.stdout[:50000]}
        return {'success': False, 'error': 'Unsupported platform'}


# ===================================================================
# FILESYSTEM NOTES
# ===================================================================

class FilesystemConnector:
    """Read, write, and search markdown files on the local filesystem."""

    def __init__(self, base_path: str = None):
        self.base_path = Path(base_path) if base_path else Path.home() / "JambuNotes"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_research(self, title: str, content: str, 
                       sources: List[str] = None) -> dict:
        """Save research findings to a markdown file."""
        safe_title = re.sub(r'[<>:"/\\|?*]', '-', title)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

        md = f"# {title}\n\n"
        md += f"*Researched: {timestamp} by Jambubrowser*\n\n"
        md += content

        if sources:
            md += "\n\n## Sources\n"
            for i, s in enumerate(sources, 1):
                md += f"{i}. [{s}]({s})\n"

        filepath = self.base_path / f"{safe_title}.md"
        filepath.write_text(md)

        return {
            'success': True,
            'path': str(filepath),
            'title': title,
            'size': len(md),
        }


# ---- Module-level singletons ----

_obsidian: Optional[ObsidianConnector] = None
_reminders: Optional[RemindersConnector] = None
_clipboard: Optional[ClipboardConnector] = None
_filesystem: Optional[FilesystemConnector] = None


def get_obsidian(vault_path: str = None) -> ObsidianConnector:
    global _obsidian
    if _obsidian is None:
        _obsidian = ObsidianConnector(vault_path)
    return _obsidian


def get_reminders() -> RemindersConnector:
    global _reminders
    if _reminders is None:
        _reminders = RemindersConnector()
    return _reminders


def get_clipboard() -> ClipboardConnector:
    global _clipboard
    if _clipboard is None:
        _clipboard = ClipboardConnector()
    return _clipboard


def get_filesystem(base_path: str = None) -> FilesystemConnector:
    global _filesystem
    if _filesystem is None:
        _filesystem = FilesystemConnector(base_path)
    return _filesystem
