"""Backup manager for the Backup integration."""

from __future__ import annotations

import abc
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
import tarfile
from tarfile import TarError
import time
from typing import Any, Generic, Protocol, cast

from securetar import SecureTarFile, atomic_contents_add
from typing_extensions import TypeVar

from homeassistant.backup_restore import RESTORE_BACKUP_FILE
from homeassistant.const import __version__ as HAVERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import integration_platform
from homeassistant.helpers.json import json_bytes
from homeassistant.util import dt as dt_util
from homeassistant.util.json import json_loads_object

from .const import DOMAIN, EXCLUDE_FROM_BACKUP, LOGGER
from .models import BaseBackup
from .sync_agent import BackupPlatformAgentProtocol, BackupSyncAgent

BUF_SIZE = 2**20 * 4  # 4MB

_BackupT = TypeVar("_BackupT", bound=BaseBackup, default=BaseBackup)


@dataclass(slots=True)
class Backup(BaseBackup):
    """Backup class."""

    path: Path

    def as_dict(self) -> dict:
        """Return a dict representation of this backup."""
        return {**asdict(self), "path": self.path.as_posix()}


class BackupPlatformProtocol(Protocol):
    """Define the format that backup platforms can have."""

    async def async_pre_backup(self, hass: HomeAssistant) -> None:
        """Perform operations before a backup starts."""

    async def async_post_backup(self, hass: HomeAssistant) -> None:
        """Perform operations after a backup finishes."""


class BaseBackupManager(abc.ABC, Generic[_BackupT]):
    """Define the format that backup managers can have."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the backup manager."""
        self.hass = hass
        self.backing_up = False
        self.backups: dict[str, _BackupT] = {}
        self.loaded_platforms = False
        self.platforms: dict[str, BackupPlatformProtocol] = {}
        self.sync_agents: dict[str, BackupSyncAgent] = {}
        self.syncing = False

    @callback
    def _add_platform_pre_post_handlers(
        self,
        hass: HomeAssistant,
        integration_domain: str,
        platform: BackupPlatformProtocol,
    ) -> None:
        """Add a platform to the backup manager."""
        if not hasattr(platform, "async_pre_backup") or not hasattr(
            platform, "async_post_backup"
        ):
            return

        self.platforms[integration_domain] = platform

    async def _async_add_platform_agents(
        self,
        hass: HomeAssistant,
        integration_domain: str,
        platform: BackupPlatformAgentProtocol,
    ) -> None:
        """Add a platform to the backup manager."""
        if not hasattr(platform, "async_get_backup_sync_agents"):
            return

        agents = await platform.async_get_backup_sync_agents(hass=hass)
        self.sync_agents.update(
            {f"{integration_domain}.{agent.name}": agent for agent in agents}
        )

    async def async_pre_backup_actions(self, **kwargs: Any) -> None:
        """Perform pre backup actions."""
        if not self.loaded_platforms:
            await self.load_platforms()

        pre_backup_results = await asyncio.gather(
            *(
                platform.async_pre_backup(self.hass)
                for platform in self.platforms.values()
            ),
            return_exceptions=True,
        )
        for result in pre_backup_results:
            if isinstance(result, Exception):
                raise result

    async def async_post_backup_actions(self, **kwargs: Any) -> None:
        """Perform post backup actions."""
        if not self.loaded_platforms:
            await self.load_platforms()

        post_backup_results = await asyncio.gather(
            *(
                platform.async_post_backup(self.hass)
                for platform in self.platforms.values()
            ),
            return_exceptions=True,
        )
        for result in post_backup_results:
            if isinstance(result, Exception):
                raise result

    async def load_platforms(self) -> None:
        """Load backup platforms."""
        if self.loaded_platforms:
            return
        await integration_platform.async_process_integration_platforms(
            self.hass,
            DOMAIN,
            self._add_platform_pre_post_handlers,
            wait_for_platforms=True,
        )
        await integration_platform.async_process_integration_platforms(
            self.hass,
            DOMAIN,
            self._async_add_platform_agents,
            wait_for_platforms=True,
        )
        LOGGER.debug("Loaded %s platforms", len(self.platforms))
        LOGGER.debug("Loaded %s agents", len(self.sync_agents))
        self.loaded_platforms = True

    @abc.abstractmethod
    async def async_restore_backup(self, slug: str, **kwargs: Any) -> None:
        """Restpre a backup."""

    @abc.abstractmethod
    async def async_create_backup(self, **kwargs: Any) -> _BackupT:
        """Generate a backup."""

    @abc.abstractmethod
    async def async_get_backups(self, **kwargs: Any) -> dict[str, _BackupT]:
        """Get backups.

        Return a dictionary of Backup instances keyed by their slug.
        """

    @abc.abstractmethod
    async def async_get_backup(self, *, slug: str, **kwargs: Any) -> _BackupT | None:
        """Get a backup."""

    @abc.abstractmethod
    async def async_remove_backup(self, *, slug: str, **kwargs: Any) -> None:
        """Remove a backup."""

    @abc.abstractmethod
    async def async_sync_backup(self, *, slug: str, **kwargs: Any) -> None:
        """Sync a backup."""


class BackupManager(BaseBackupManager[Backup]):
    """Backup manager for the Backup integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the backup manager."""
        super().__init__(hass=hass)
        self.backup_dir = Path(hass.config.path("backups"))
        self.loaded_backups = False

    async def async_sync_backup(self, *, slug: str, **kwargs: Any) -> None:
        """Sync a backup."""
        await self.load_platforms()

        if not self.sync_agents:
            return

        if not (backup := await self.async_get_backup(slug=slug)):
            return

        self.syncing = True
        sync_backup_results = await asyncio.gather(
            *(
                agent.async_upload_backup(
                    path=backup.path,
                    metadata={
                        "homeassistant": HAVERSION,
                        "size": backup.size,
                        "date": backup.date,
                        "slug": backup.slug,
                        "name": backup.name,
                    },
                )
                for agent in self.sync_agents.values()
            ),
            return_exceptions=True,
        )
        for result in sync_backup_results:
            if isinstance(result, Exception):
                LOGGER.error("Error during backup sync - %s", result)
        self.syncing = False

    async def load_backups(self) -> None:
        """Load data of stored backup files."""
        backups = await self.hass.async_add_executor_job(self._read_backups)
        LOGGER.debug("Loaded %s local backups", len(backups))
        self.backups = backups
        self.loaded_backups = True

    def _read_backups(self) -> dict[str, Backup]:
        """Read backups from disk."""
        backups: dict[str, Backup] = {}
        for backup_path in self.backup_dir.glob("*.tar"):
            try:
                with tarfile.open(backup_path, "r:", bufsize=BUF_SIZE) as backup_file:
                    if data_file := backup_file.extractfile("./backup.json"):
                        data = json_loads_object(data_file.read())
                        backup = Backup(
                            slug=cast(str, data["slug"]),
                            name=cast(str, data["name"]),
                            date=cast(str, data["date"]),
                            path=backup_path,
                            size=round(backup_path.stat().st_size / 1_048_576, 2),
                        )
                        backups[backup.slug] = backup
            except (OSError, TarError, json.JSONDecodeError, KeyError) as err:
                LOGGER.warning("Unable to read backup %s: %s", backup_path, err)
        return backups

    async def async_get_backups(self, **kwargs: Any) -> dict[str, Backup]:
        """Return backups."""
        if not self.loaded_backups:
            await self.load_backups()

        return self.backups

    async def async_get_backup(self, *, slug: str, **kwargs: Any) -> Backup | None:
        """Return a backup."""
        if not self.loaded_backups:
            await self.load_backups()

        if not (backup := self.backups.get(slug)):
            return None

        if not backup.path.exists():
            LOGGER.debug(
                (
                    "Removing tracked backup (%s) that does not exists on the expected"
                    " path %s"
                ),
                backup.slug,
                backup.path,
            )
            self.backups.pop(slug)
            return None

        return backup

    async def async_remove_backup(self, *, slug: str, **kwargs: Any) -> None:
        """Remove a backup."""
        if (backup := await self.async_get_backup(slug=slug)) is None:
            return

        await self.hass.async_add_executor_job(backup.path.unlink, True)
        LOGGER.debug("Removed backup located at %s", backup.path)
        self.backups.pop(slug)

    async def async_create_backup(self, **kwargs: Any) -> Backup:
        """Generate a backup."""
        if self.backing_up:
            raise HomeAssistantError("Backup already in progress")

        try:
            self.backing_up = True
            await self.async_pre_backup_actions()
            backup_name = f"Core {HAVERSION}"
            date_str = dt_util.now().isoformat()
            slug = _generate_slug(date_str, backup_name)

            backup_data = {
                "slug": slug,
                "name": backup_name,
                "date": date_str,
                "type": "partial",
                "folders": ["homeassistant"],
                "homeassistant": {"version": HAVERSION},
                "compressed": True,
            }
            tar_file_path = Path(self.backup_dir, f"{backup_data['slug']}.tar")
            size_in_bytes = await self.hass.async_add_executor_job(
                self._mkdir_and_generate_backup_contents,
                tar_file_path,
                backup_data,
            )
            backup = Backup(
                slug=slug,
                name=backup_name,
                date=date_str,
                path=tar_file_path,
                size=round(size_in_bytes / 1_048_576, 2),
            )
            if self.loaded_backups:
                self.backups[slug] = backup
            LOGGER.debug("Generated new backup with slug %s", slug)
            return backup
        finally:
            self.backing_up = False
            await self.async_post_backup_actions()

    def _mkdir_and_generate_backup_contents(
        self,
        tar_file_path: Path,
        backup_data: dict[str, Any],
    ) -> int:
        """Generate backup contents and return the size."""
        if not self.backup_dir.exists():
            LOGGER.debug("Creating backup directory")
            self.backup_dir.mkdir()

        outer_secure_tarfile = SecureTarFile(
            tar_file_path, "w", gzip=False, bufsize=BUF_SIZE
        )
        with outer_secure_tarfile as outer_secure_tarfile_tarfile:
            raw_bytes = json_bytes(backup_data)
            fileobj = io.BytesIO(raw_bytes)
            tar_info = tarfile.TarInfo(name="./backup.json")
            tar_info.size = len(raw_bytes)
            tar_info.mtime = int(time.time())
            outer_secure_tarfile_tarfile.addfile(tar_info, fileobj=fileobj)
            with outer_secure_tarfile.create_inner_tar(
                "./homeassistant.tar.gz", gzip=True
            ) as core_tar:
                atomic_contents_add(
                    tar_file=core_tar,
                    origin_path=Path(self.hass.config.path()),
                    excludes=EXCLUDE_FROM_BACKUP,
                    arcname="data",
                )

        return tar_file_path.stat().st_size

    async def async_restore_backup(self, slug: str, **kwargs: Any) -> None:
        """Restore a backup.

        This will write the restore information to .HA_RESTORE which
        will be handled during startup by the restore_backup module.
        """
        if (backup := await self.async_get_backup(slug=slug)) is None:
            raise HomeAssistantError(f"Backup {slug} not found")

        def _write_restore_file() -> None:
            """Write the restore file."""
            Path(self.hass.config.path(RESTORE_BACKUP_FILE)).write_text(
                json.dumps({"path": backup.path.as_posix()}),
                encoding="utf-8",
            )

        await self.hass.async_add_executor_job(_write_restore_file)
        await self.hass.services.async_call("homeassistant", "restart", {})


def _generate_slug(date: str, name: str) -> str:
    """Generate a backup slug."""
    return hashlib.sha1(f"{date} - {name}".lower().encode()).hexdigest()[:8]
