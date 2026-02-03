"""Prepare Codex CLI skills for repository-local usage."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import List, Optional


CONFIG_DIRNAME = "config"
SKILLS_DIRNAME = "skills"
CODEX_DIRNAME = ".codex"
SKILL_FILENAME = "SKILL.md"
SKILL_PACKAGE_SUFFIX = ".skill"


class CodexSkillPreparer:
    """Prepare Codex CLI skills from a config/skills source into .codex/skills.

    This class ensures the Codex CLI can discover skills by placing them in the
    expected .codex/skills folder within the project root. The source directory
    defaults to config/skills but can be overridden.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        overwrite: bool = True,
        source_dir: Optional[Path] = None,
    ) -> None:
        """Initialize a skill preparer for a given project root.

        Args:
            project_root: Root path of the project. When None, it is derived from
                the package location.
            overwrite: Whether to replace existing skills when preparing a fresh
                .codex/skills directory. When .codex/skills already exists,
                existing skills are preserved and only missing skills are added.
            source_dir: Optional override for the skills source directory.

        Raises:
            TypeError: If project_root is not a Path or overwrite is not bool.
            FileNotFoundError: If the resolved project_root does not exist.
            ValueError: If the resolved project_root is not a directory.
            TypeError: If source_dir is not a Path or None.
        """
        if project_root is None:
            resolved_root = self._default_project_root()
        elif isinstance(project_root, Path):
            resolved_root = project_root
        else:
            raise TypeError("project_root must be a pathlib.Path or None")

        if resolved_root.exists():
            if resolved_root.is_dir():
                self._project_root = resolved_root
            else:
                raise ValueError("project_root must be a directory")
        else:
            raise FileNotFoundError("project_root does not exist")

        if isinstance(overwrite, bool):
            self._overwrite = overwrite
        else:
            raise TypeError("overwrite must be a bool")

        if source_dir is None:
            resolved_source_dir = (
                self._project_root / CONFIG_DIRNAME / SKILLS_DIRNAME
            )
        elif isinstance(source_dir, Path):
            resolved_source_dir = source_dir
        else:
            raise TypeError("source_dir must be a pathlib.Path or None")

        self._source_dir = resolved_source_dir
        self._target_dir = (
            self._project_root / CODEX_DIRNAME / SKILLS_DIRNAME
        )

    def prepare(self) -> List[Path]:
        """Prepare Codex CLI skills for this project.

        Returns:
            List of prepared skill directories under .codex/skills. When the
            target directory already exists, existing skills are reused and only
            missing skills are added.

        Raises:
            FileNotFoundError: If the skills source directory does not exist.
            ValueError: If entries are invalid or missing SKILL.md.
            TypeError: If a skill package is malformed.
            zipfile.BadZipFile: If a .skill package is not a valid zip file.
        """
        source_dir = self._ensure_source_dir()
        target_dir_exists = self._target_dir.exists()
        target_dir = self._ensure_target_dir()
        prepared = self._prepare_entries(
            source_dir, target_dir, target_dir_exists
        )
        result = prepared
        return result

    def _default_project_root(self) -> Path:
        """Resolve the project root based on this module location."""
        result = Path(__file__).resolve().parent.parent
        return result

    def _ensure_source_dir(self) -> Path:
        """Validate the configured skills source directory exists."""
        source_dir = self._source_dir
        if source_dir.exists():
            if source_dir.is_dir():
                result = source_dir
            else:
                raise ValueError(
                    f"skills source must be a directory: {source_dir}"
                )
        else:
            raise FileNotFoundError(
                f"skills source directory not found: {source_dir}"
            )
        return result

    def _ensure_target_dir(self) -> Path:
        """Ensure .codex/skills directory exists."""
        target_parent = self._ensure_directory(
            self._project_root / CODEX_DIRNAME,
            ".codex",
        )
        target_dir = self._ensure_directory(
            target_parent / SKILLS_DIRNAME,
            ".codex/skills",
        )
        result = target_dir
        return result

    def _ensure_directory(self, directory: Path, context: str) -> Path:
        """Create a directory if it does not exist."""
        if directory.exists():
            if directory.is_dir():
                result = directory
            else:
                raise ValueError(f"{context} must be a directory")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            result = directory
        return result

    def _prepare_entries(
        self,
        source_dir: Path,
        target_dir: Path,
        skip_existing: bool,
    ) -> List[Path]:
        """Prepare all skills from the source directory."""
        prepared: List[Path] = []
        entries = sorted(source_dir.iterdir())
        for entry in entries:
            prepared.append(
                self._prepare_entry(entry, target_dir, skip_existing)
            )
        result = prepared
        return result

    def _prepare_entry(
        self,
        entry: Path,
        target_dir: Path,
        skip_existing: bool,
    ) -> Path:
        """Prepare a single skill entry."""
        if entry.is_dir():
            prepared = self._copy_skill_directory(
                entry, target_dir, skip_existing
            )
        elif entry.is_file():
            if entry.suffix == SKILL_PACKAGE_SUFFIX:
                prepared = self._extract_skill_package(
                    entry, target_dir, skip_existing
                )
            else:
                raise ValueError(f"Unsupported skill entry: {entry}")
        else:
            raise ValueError(f"Unsupported skill entry: {entry}")
        result = prepared
        return result

    def _copy_skill_directory(
        self,
        skill_dir: Path,
        target_dir: Path,
        skip_existing: bool,
    ) -> Path:
        """Copy a skill directory into .codex/skills."""
        self._ensure_skill_directory(skill_dir)
        target_skill_dir = target_dir / skill_dir.name
        should_skip = False
        if skip_existing:
            should_skip = self._is_skill_dir_ready(target_skill_dir)
        if should_skip:
            result = target_skill_dir
        else:
            self._replace_dir_if_needed(target_skill_dir)
            shutil.copytree(skill_dir, target_skill_dir)
            result = target_skill_dir
        return result

    def _ensure_skill_directory(self, skill_dir: Path) -> None:
        """Validate that a directory contains SKILL.md."""
        skill_file = skill_dir / SKILL_FILENAME
        if not skill_file.is_file():
            raise FileNotFoundError(f"Missing SKILL.md in {skill_dir}")

    def _is_skill_dir_ready(self, skill_dir: Path) -> bool:
        """Check whether a target skill directory already exists and is valid."""
        if skill_dir.exists():
            if skill_dir.is_dir():
                skill_file = skill_dir / SKILL_FILENAME
                if skill_file.is_file():
                    result = True
                else:
                    raise FileNotFoundError(
                        f"Missing SKILL.md in {skill_dir}"
                    )
            else:
                raise ValueError(
                    f"Target path must be a directory: {skill_dir}"
                )
        else:
            result = False
        return result

    def _extract_skill_package(
        self,
        package_path: Path,
        target_dir: Path,
        skip_existing: bool,
    ) -> Path:
        """Extract a .skill package into .codex/skills."""
        with zipfile.ZipFile(package_path, "r") as archive:
            root_dir = self._detect_single_root_dir(archive, package_path)
            self._ensure_package_skill_file(archive, root_dir, package_path)
            target_skill_dir = target_dir / root_dir
            should_skip = False
            if skip_existing:
                should_skip = self._is_skill_dir_ready(target_skill_dir)
            if should_skip:
                result = target_skill_dir
            else:
                self._replace_dir_if_needed(target_skill_dir)
                archive.extractall(target_dir)
                result = target_skill_dir
        return result

    def _detect_single_root_dir(
        self,
        archive: zipfile.ZipFile,
        package_path: Path,
    ) -> str:
        """Detect the single root directory in a skill package."""
        root_candidates = {
            Path(name).parts[0]
            for name in archive.namelist()
            if name and not name.startswith("__MACOSX/")
        }
        if len(root_candidates) == 1:
            root_dir = next(iter(root_candidates))
        else:
            raise ValueError(
                f"Skill package must contain exactly one root directory: {package_path}"
            )
        result = root_dir
        return result

    def _ensure_package_skill_file(
        self,
        archive: zipfile.ZipFile,
        root_dir: str,
        package_path: Path,
    ) -> None:
        """Ensure the package includes a SKILL.md file."""
        skill_path = f"{root_dir}/{SKILL_FILENAME}"
        if skill_path not in archive.namelist():
            raise ValueError(
                f"Skill package missing {SKILL_FILENAME}: {package_path}"
            )

    def _replace_dir_if_needed(self, target_dir: Path) -> None:
        """Replace an existing skill directory when overwrite is enabled."""
        if target_dir.exists():
            if self._overwrite:
                if target_dir.is_dir():
                    shutil.rmtree(target_dir)
                else:
                    raise ValueError(
                        f"Target path must be a directory: {target_dir}"
                    )
            else:
                raise FileExistsError(
                    f"Target skill directory already exists: {target_dir}"
                )
