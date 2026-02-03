"""Workspace-local configuration helpers."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from ..skills_preparer import CodexSkillPreparer, SKILLS_DIRNAME
import defaults


class WorkspaceConfigManager:
    """Manage workspace-local configuration under .agent/config.

    This class centralizes initialization so future CLI/TUI flows can call into
    the same logic when preparing a repository workspace.
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        template_config_dir: Optional[Path] = None,
    ) -> None:
        """Initialize the manager with optional workspace and template paths.

        Args:
            workspace_root: Root directory of the workspace. Defaults to cwd.
            template_config_dir: Source config directory for seeding local config.

        Raises:
            FileNotFoundError: If workspace or template directories are missing.
            TypeError: If workspace_root/template_config_dir have invalid types.
            ValueError: If workspace_root/template_config_dir are not directories.
        """
        self._agent_dirname = defaults.DEFAULT_AGENT_DIRNAME
        self._config_dirname = defaults.DEFAULT_CONFIG_DIRECTORY
        self._workspace_root = self._resolve_workspace_root(workspace_root)
        self._template_config_dir = self._resolve_template_config_dir(
            template_config_dir
        )

    def ensure_local_config_dir(self) -> Path:
        """Ensure the .agent/config directory exists, is seeded, and prepare skills.

        Returns:
            Path to the workspace-local config directory.

        Raises:
            FileNotFoundError: If the template config directory is missing.
            FileNotFoundError: If the skills source directory is missing.
            ValueError: If the local config path exists as a non-directory.
            ValueError: If skills entries are invalid or missing SKILL.md.
            TypeError: If a skill package is malformed.
            zipfile.BadZipFile: If a .skill package is not a valid zip file.
        """
        template_dir = self._ensure_template_config_dir()
        local_dir = self._local_config_dir()
        if local_dir.exists():
            if local_dir.is_dir():
                self._copy_missing_entries(template_dir, local_dir)
                result = local_dir
            else:
                raise ValueError("local config path must be a directory")
        else:
            agent_dir = self._agent_dir()
            self._ensure_directory(agent_dir, "workspace .agent")
            shutil.copytree(template_dir, local_dir)
            result = local_dir
        self._prepare_workspace_skills(local_dir)
        return result

    def _prepare_workspace_skills(self, local_dir: Path) -> None:
        """Prepare .codex/skills from the local config/skills directory.

        Args:
            local_dir: Path to the workspace-local config directory.

        Raises:
            FileNotFoundError: If the skills source directory is missing.
            ValueError: If skills entries are invalid or missing SKILL.md.
            TypeError: If a skill package is malformed.
            zipfile.BadZipFile: If a .skill package is not a valid zip file.
        """
        skills_source_dir = local_dir / SKILLS_DIRNAME
        preparer = CodexSkillPreparer(
            project_root=self._workspace_root,
            source_dir=skills_source_dir,
        )
        preparer.prepare()

    def resolve_local_config_path(self, relative_path: str) -> Path:
        """Resolve a path under .agent/config and seed it if missing.

        Args:
            relative_path: File or directory path relative to the config dir.

        Returns:
            Resolved path inside .agent/config.

        Raises:
            FileNotFoundError: If the entry is missing from the template config.
            TypeError: If relative_path is not a string.
            ValueError: If relative_path is empty or invalid.
        """
        normalized = self._normalize_relative_path(relative_path)
        local_dir = self.ensure_local_config_dir()
        local_path = local_dir / normalized
        if local_path.exists():
            result = local_path
        else:
            result = self._copy_template_entry(normalized, local_path)
        return result

    def resolve_env_config_path(self, env_path: str) -> Path:
        """Resolve a config path from environment/config values.

        Absolute paths are returned directly; relative paths are mapped into the
        workspace-local .agent/config tree.

        Args:
            env_path: Path value read from environment or config.

        Returns:
            Resolved filesystem path.

        Raises:
            TypeError: If env_path is not a string.
            ValueError: If env_path is empty.
        """
        normalized = self._normalize_relative_path(env_path)
        candidate = Path(normalized)
        if candidate.is_absolute():
            result = candidate.resolve()
        else:
            local_dir = self.ensure_local_config_dir()
            base_dir = self._select_base_dir(candidate, local_dir)
            result = (base_dir / candidate).resolve()
        return result

    def _resolve_workspace_root(self, workspace_root: Optional[Path]) -> Path:
        """Resolve and validate the workspace root."""
        if workspace_root is None:
            resolved_root = Path.cwd()
        elif isinstance(workspace_root, Path):
            resolved_root = workspace_root
        else:
            raise TypeError("workspace_root must be a pathlib.Path or None")

        if resolved_root.exists():
            if resolved_root.is_dir():
                result = resolved_root
            else:
                raise ValueError("workspace_root must be a directory")
        else:
            raise FileNotFoundError("workspace_root does not exist")
        return result

    def _resolve_template_config_dir(
        self,
        template_config_dir: Optional[Path],
    ) -> Path:
        """Resolve and validate the template config directory."""
        if template_config_dir is None:
            resolved_dir = self._default_template_config_dir()
        elif isinstance(template_config_dir, Path):
            resolved_dir = template_config_dir
        else:
            raise TypeError("template_config_dir must be a pathlib.Path or None")

        if resolved_dir.exists():
            if resolved_dir.is_dir():
                result = resolved_dir
            else:
                raise ValueError("template_config_dir must be a directory")
        else:
            raise FileNotFoundError("template_config_dir does not exist")
        return result

    def _default_template_config_dir(self) -> Path:
        """Return the default template config directory from the package."""
        result = (
            Path(__file__).resolve().parent.parent.parent
            / defaults.DEFAULT_CONFIG_DIRECTORY
        )
        return result

    def _ensure_template_config_dir(self) -> Path:
        """Return the template config directory after validation."""
        template_dir = self._template_config_dir
        if template_dir.exists():
            if template_dir.is_dir():
                result = template_dir
            else:
                raise ValueError("template_config_dir must be a directory")
        else:
            raise FileNotFoundError("template_config_dir does not exist")
        return result

    def _agent_dir(self) -> Path:
        """Return the workspace-local .agent directory."""
        result = self._workspace_root / self._agent_dirname
        return result

    def _local_config_dir(self) -> Path:
        """Return the workspace-local config directory."""
        result = self._agent_dir() / self._config_dirname
        return result

    def _ensure_directory(self, directory: Path, context: str) -> Path:
        """Ensure a directory exists, creating it if needed."""
        if directory.exists():
            if directory.is_dir():
                result = directory
            else:
                raise ValueError(f"{context} must be a directory")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            result = directory
        return result

    def _normalize_relative_path(self, value: str) -> str:
        """Normalize a relative/absolute path string."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                result = stripped
            else:
                raise ValueError("path must not be empty")
        else:
            raise TypeError("path must be a string")
        return result

    def _select_base_dir(self, candidate: Path, local_dir: Path) -> Path:
        """Select the base directory for a relative path."""
        parts = candidate.parts
        first_part = parts[0] if parts else ""
        if first_part == self._agent_dirname:
            result = self._workspace_root
        elif first_part == self._config_dirname:
            result = local_dir.parent
        else:
            result = local_dir
        return result

    def _copy_missing_entries(self, template_dir: Path, local_dir: Path) -> None:
        """Copy missing entries from template into local config directory."""
        entries = sorted(template_dir.iterdir())
        for entry in entries:
            local_entry = local_dir / entry.name
            if local_entry.exists():
                pass
            else:
                self._copy_entry(entry, local_entry)

    def _copy_entry(self, template_entry: Path, local_entry: Path) -> None:
        """Copy a template file or directory into local config."""
        if template_entry.is_dir():
            self._ensure_directory(local_entry.parent, "local config parent")
            shutil.copytree(template_entry, local_entry)
        elif template_entry.is_file():
            self._ensure_directory(local_entry.parent, "local config parent")
            shutil.copy2(template_entry, local_entry)
        else:
            raise ValueError(f"Unsupported template entry: {template_entry}")

    def _copy_template_entry(self, relative_path: str, local_path: Path) -> Path:
        """Copy a specific template entry when missing."""
        template_dir = self._ensure_template_config_dir()
        template_path = template_dir / relative_path
        if template_path.exists():
            if template_path.is_dir():
                self._ensure_directory(local_path.parent, "local config parent")
                shutil.copytree(template_path, local_path)
            elif template_path.is_file():
                self._ensure_directory(local_path.parent, "local config parent")
                shutil.copy2(template_path, local_path)
            else:
                raise ValueError(f"Unsupported template entry: {template_path}")
        else:
            raise FileNotFoundError(f"Template config missing: {template_path}")
        result = local_path
        return result
