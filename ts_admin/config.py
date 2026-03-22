"""
Configuration loading for the ThoughtSpot Admin Toolkit.

Priority order (highest to lowest):
  1. Environment variables  (TS_ADMIN_*)
  2. OS keychain            (sensitive fields: password, secret key, token)
  3. Config file            (~/.ts-admin/config.toml)

Config file structure:
  [clusters.production]
  name    = "Production"
  url     = "https://company.thoughtspot.cloud"
  username = "admin@company.com"
  auth_type = "basic"         # basic | trusted | bearer

  [clusters.staging]
  ...

Sensitive values (passwords, secret keys, tokens) are NEVER stored in the
config file. They are stored in the OS keychain under the service name
"ts-admin-toolkit" with the account key "{cluster_id}:{field}".
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ts_admin.ts_client.auth import AuthStrategy, BasicAuth, BearerTokenAuth, TrustedAuth
from ts_admin.ts_client.exceptions import ConfigInvalidError, ConfigNotFoundError, KeyringError
from ts_admin.ts_client.models import AuthType

logger = logging.getLogger(__name__)

CONFIG_DIR  = Path.home() / ".ts-admin"
CONFIG_FILE = CONFIG_DIR / "config.toml"
KEYRING_SERVICE = "ts-admin-toolkit"


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ClusterConfig:
    """Configuration for a single ThoughtSpot cluster connection."""
    id: str                        # slug key, e.g. "production"
    name: str                      # display name, e.g. "Production"
    url: str                       # https://company.thoughtspot.cloud
    username: str
    auth_type: AuthType = AuthType.BASIC

    def build_auth_strategy(self) -> AuthStrategy:
        """
        Construct the appropriate AuthStrategy for this cluster,
        loading secrets from keychain (or env vars as fallback).
        """
        if self.auth_type == AuthType.BASIC:
            password = _load_secret(self.id, "password")
            return BasicAuth(username=self.username, password=password)

        if self.auth_type == AuthType.TRUSTED:
            secret_key = _load_secret(self.id, "secret_key")
            return TrustedAuth(username=self.username, secret_key=secret_key)

        if self.auth_type == AuthType.BEARER:
            token = _load_secret(self.id, "token")
            return BearerTokenAuth(token=token)

        raise ConfigInvalidError(f"Unknown auth_type: {self.auth_type!r}")


@dataclass
class AppConfig:
    """Full application configuration."""
    clusters: dict[str, ClusterConfig] = field(default_factory=dict)
    active_cluster_id: str | None = None

    @property
    def active_cluster(self) -> ClusterConfig:
        if not self.active_cluster_id:
            raise ConfigNotFoundError(
                "No active cluster set. Complete setup at http://localhost:8080/setup"
            )
        try:
            return self.clusters[self.active_cluster_id]
        except KeyError:
            raise ConfigNotFoundError(
                f"Active cluster {self.active_cluster_id!r} not found in config"
            )

    def has_clusters(self) -> bool:
        return bool(self.clusters)


# ── Loading ────────────────────────────────────────────────────────────────────

def load_config() -> AppConfig:
    """
    Load and return the full application configuration.

    Raises:
        ConfigNotFoundError: No config file exists (first run).
        ConfigInvalidError:  Config file is malformed.
    """
    # Check for env-var-only mode first (useful for CI/CD or testing)
    if _env_cluster_configured():
        return _load_from_env()

    if not CONFIG_FILE.exists():
        raise ConfigNotFoundError(
            f"No config found at {CONFIG_FILE}. "
            "Open the app and complete the setup screen."
        )

    return _load_from_file()


def save_cluster(cluster: ClusterConfig, *, secret: str) -> None:
    """
    Persist a cluster config to disk and its secret to the OS keychain.

    Args:
        cluster: The cluster configuration to save.
        secret:  The password, secret_key, or bearer token (stored in keychain).
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing config or start fresh
    raw = _read_toml(CONFIG_FILE) if CONFIG_FILE.exists() else {}
    raw.setdefault("clusters", {})
    raw["clusters"][cluster.id] = {
        "name": cluster.name,
        "url": cluster.url,
        "username": cluster.username,
        "auth_type": cluster.auth_type.value,
    }

    # Set as active cluster if it's the first one
    if "active_cluster" not in raw:
        raw["active_cluster"] = cluster.id

    _write_toml(CONFIG_FILE, raw)
    _save_secret(cluster.id, _secret_field_for(cluster.auth_type), secret)

    # Mirror non-sensitive fields to SQLite so FK integrity holds for cache tables
    from ts_admin.database import get_session
    from ts_admin.models.cluster import Cluster as ClusterRow
    from sqlmodel import select
    with get_session() as session:
        existing = session.exec(select(ClusterRow).where(ClusterRow.id == cluster.id)).first()
        if existing:
            existing.name = cluster.name
            existing.url = cluster.url
            existing.username = cluster.username
            existing.auth_type = cluster.auth_type.value
            session.add(existing)
        else:
            session.add(ClusterRow(
                id=cluster.id,
                name=cluster.name,
                url=cluster.url,
                username=cluster.username,
                auth_type=cluster.auth_type.value,
            ))
        session.commit()

    logger.info("Saved cluster config: %s", cluster.id)


def update_cluster(
    cluster_id: str,
    *,
    name: str,
    url: str,
    username: str,
    auth_type: "AuthType",
    new_secret: str | None,
) -> "ClusterConfig":
    """
    Update an existing cluster's config. If new_secret is provided, rotate the
    keychain credential (deleting the old one if auth_type changed).
    If new_secret is None, the existing keychain entry is left untouched.
    """
    from ts_admin.ts_client.exceptions import ConfigInvalidError

    config = load_config()
    existing = config.clusters.get(cluster_id)
    if not existing:
        raise ConfigInvalidError(f"Cluster {cluster_id!r} not found")

    old_auth_type = existing.auth_type

    # Update TOML
    raw = _read_toml(CONFIG_FILE)
    raw["clusters"][cluster_id].update({
        "name": name,
        "url": url,
        "username": username,
        "auth_type": auth_type.value,
    })
    _write_toml(CONFIG_FILE, raw)

    # Rotate keychain if credential or auth_type changed
    if new_secret is not None:
        if old_auth_type != auth_type:
            # Auth type changed — delete old keychain field, save under new field
            _delete_secret(cluster_id)
        _save_secret(cluster_id, _secret_field_for(auth_type), new_secret)

    # Sync to SQLite
    from ts_admin.database import get_session
    from ts_admin.models.cluster import Cluster as ClusterRow
    from sqlmodel import select
    with get_session() as session:
        row = session.exec(select(ClusterRow).where(ClusterRow.id == cluster_id)).first()
        if row:
            row.name = name
            row.url = url
            row.username = username
            row.auth_type = auth_type.value
            session.add(row)
            session.commit()

    logger.info("Updated cluster config: %s", cluster_id)
    return load_config().clusters[cluster_id]


def delete_cluster(cluster_id: str) -> None:
    """Remove a cluster from config and delete its keychain entry."""
    if not CONFIG_FILE.exists():
        return

    raw = _read_toml(CONFIG_FILE)
    raw.get("clusters", {}).pop(cluster_id, None)

    # If the active cluster was deleted, clear it
    if raw.get("active_cluster") == cluster_id:
        remaining = list(raw.get("clusters", {}).keys())
        raw["active_cluster"] = remaining[0] if remaining else None

    _write_toml(CONFIG_FILE, raw)
    _delete_secret(cluster_id)

    # Remove from SQLite
    from ts_admin.database import get_session
    from ts_admin.models.cluster import Cluster as ClusterRow
    from sqlmodel import select
    with get_session() as session:
        row = session.exec(select(ClusterRow).where(ClusterRow.id == cluster_id)).first()
        if row:
            session.delete(row)
            session.commit()

    logger.info("Deleted cluster config: %s", cluster_id)


def set_active_cluster(cluster_id: str) -> None:
    """Switch the active cluster."""
    raw = _read_toml(CONFIG_FILE)
    if cluster_id not in raw.get("clusters", {}):
        raise ConfigInvalidError(f"Cluster {cluster_id!r} not found in config")
    raw["active_cluster"] = cluster_id
    _write_toml(CONFIG_FILE, raw)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _load_from_file() -> AppConfig:
    try:
        raw = _read_toml(CONFIG_FILE)
    except Exception as exc:
        raise ConfigInvalidError(f"Failed to read {CONFIG_FILE}: {exc}") from exc

    clusters: dict[str, ClusterConfig] = {}
    for cid, cdata in raw.get("clusters", {}).items():
        try:
            clusters[cid] = ClusterConfig(
                id=cid,
                name=cdata["name"],
                url=cdata["url"],
                username=cdata["username"],
                auth_type=AuthType(cdata.get("auth_type", "basic")),
            )
        except (KeyError, ValueError) as exc:
            raise ConfigInvalidError(
                f"Invalid config for cluster {cid!r}: {exc}"
            ) from exc

    return AppConfig(
        clusters=clusters,
        active_cluster_id=raw.get("active_cluster"),
    )


def _env_cluster_configured() -> bool:
    return bool(os.environ.get("TS_ADMIN_URL"))


def _load_from_env() -> AppConfig:
    """Load a single cluster config from environment variables."""
    url      = os.environ["TS_ADMIN_URL"]
    username = os.environ.get("TS_ADMIN_USERNAME", "")
    auth_type = AuthType(os.environ.get("TS_ADMIN_AUTH_TYPE", "basic"))

    cluster = ClusterConfig(
        id="env",
        name="Environment",
        url=url,
        username=username,
        auth_type=auth_type,
    )
    return AppConfig(clusters={"env": cluster}, active_cluster_id="env")


def _secret_field_for(auth_type: AuthType) -> str:
    return {
        AuthType.BASIC:   "password",
        AuthType.TRUSTED: "secret_key",
        AuthType.BEARER:  "token",
    }[auth_type]


def _load_secret(cluster_id: str, field: str) -> str:
    """Load a secret from keychain, falling back to env var."""
    # Env var takes precedence (useful for CI / testing)
    env_map = {
        "password":   "TS_ADMIN_PASSWORD",
        "secret_key": "TS_ADMIN_SECRET_KEY",
        "token":      "TS_ADMIN_TOKEN",
    }
    if env_val := os.environ.get(env_map.get(field, "")):
        return env_val

    try:
        import keyring
        secret = keyring.get_password(KEYRING_SERVICE, f"{cluster_id}:{field}")
        if secret is None:
            raise KeyringError(
                f"No {field} found for cluster {cluster_id!r}. "
                "Re-enter credentials in Settings → Connections."
            )
        return secret
    except ImportError:
        raise KeyringError(
            "keyring package is not available. "
            "Set credentials via environment variables instead."
        )
    except Exception as exc:
        raise KeyringError(f"Failed to read keychain: {exc}") from exc


def _save_secret(cluster_id: str, field: str, value: str) -> None:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, f"{cluster_id}:{field}", value)
    except Exception as exc:
        raise KeyringError(f"Failed to save to keychain: {exc}") from exc


def _delete_secret(cluster_id: str) -> None:
    try:
        import keyring
        for f in ("password", "secret_key", "token"):
            try:
                keyring.delete_password(KEYRING_SERVICE, f"{cluster_id}:{f}")
            except Exception:
                pass
    except ImportError:
        pass


def _read_toml(path: Path) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    else:
        import tomli
        with open(path, "rb") as f:
            return tomli.load(f)


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    # Use tomli_w if available, otherwise format manually for simple structures
    try:
        import tomli_w
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
    except ImportError:
        # Fallback: write minimal TOML manually
        lines = []
        if "active_cluster" in data:
            lines.append(f'active_cluster = "{data["active_cluster"]}"\n')
        for cid, cdata in data.get("clusters", {}).items():
            lines.append(f'\n[clusters.{cid}]')
            for k, v in cdata.items():
                lines.append(f'{k} = "{v}"')
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
