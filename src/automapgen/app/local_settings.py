"""Settings of this computer, outside the repository (local/settings.toml, ignored by git)."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automapgen.app.toml_writer import TomlWriter

LOCAL_SETTINGS = Path("local") / "settings.toml"
_HEADER = "Settings of this computer (not in git)."


@dataclass(frozen=True)
class LocalSettings:
    """Where the game is installed, the player's user data folder of the game (userdata/<id>/<game id>/local) that
    receives the results, and secrets such as API keys (table [secrets], e.g. opentopography_api_key)."""

    game_folder: Path | None = None
    user_data_folder: Path | None = None
    secrets: Mapping[str, str] = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> LocalSettings:
        data = LocalSettings._read(path)
        game, user_data = data.get("game_folder"), data.get("user_data_folder")
        secrets = {str(k): str(v) for k, v in data.get("secrets", {}).items()}
        return LocalSettings(Path(game) if game else None, Path(user_data) if user_data else None, secrets)

    @staticmethod
    def save_secret(path: Path, name: str, value: str) -> None:
        """Sets (or with an empty value removes) one secret; the other settings stay (comments are lost)."""
        data = LocalSettings._read(path)
        secrets = {k: v for k, v in data.get("secrets", {}).items() if k != name}
        if value:
            secrets[name] = value
        LocalSettings._write(path, {k: v for k, v in data.items() if k != "secrets"} |
                             ({"secrets": secrets} if secrets else {}))

    @staticmethod
    def save_folders(path: Path, game_folder: Path | None, user_data_folder: Path | None) -> None:
        """Sets (or with None removes) the game folder and the user data folder; secrets stay."""
        data = {k: v for k, v in LocalSettings._read(path).items() if k not in ("game_folder", "user_data_folder")}
        folders = {k: v.as_posix() for k, v in (("game_folder", game_folder), ("user_data_folder", user_data_folder))
                   if v is not None}
        LocalSettings._write(path, folders | data)

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TomlWriter().write(data, _HEADER), encoding="utf-8")

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
