"""Local JSON file storage (no database server)."""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from app.models.session import Session
from app.utils.validation import is_safe_id


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class ConfigStore:
    def __init__(self, configs_dir: Path, templates_dir: Path):
        self.dir = configs_dir
        self.templates = templates_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def seed_defaults(self) -> None:
        """Create the default configuration(s) on first launch only."""
        if any(self.dir.glob("*.json")):
            return
        for tpl in sorted(self.templates.glob("*.json")):
            atomic_write_json(self.dir / tpl.name, read_json(tpl))

    def _path(self, cid: str) -> Path:
        if not is_safe_id(cid):
            raise KeyError(cid)
        return self.dir / f"{cid}.json"

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(read_json(p))
            except Exception:
                continue
        return out

    def get(self, cid: str) -> Optional[dict]:
        try:
            p = self._path(cid)
        except KeyError:
            return None
        return read_json(p) if p.exists() else None

    def save(self, cfg: dict) -> None:
        atomic_write_json(self._path(cfg["id"]), cfg)

    def delete(self, cid: str) -> bool:
        try:
            p = self._path(cid)
        except KeyError:
            return False
        if p.exists():
            p.unlink()
            return True
        return False


class SessionStore:
    def __init__(self, sessions_dir: Path, persist: bool = True):
        self.dir = sessions_dir
        self.persist = persist
        self.dir.mkdir(parents=True, exist_ok=True)

    def session_dir(self, sid: str) -> Path:
        if not is_safe_id(sid):
            raise KeyError(sid)
        return self.dir / sid

    def save(self, s: Session) -> None:
        if not self.persist:
            return
        atomic_write_json(self.session_dir(s.id) / "session.json", json.loads(s.model_dump_json()))

    def load(self, sid: str) -> Optional[Session]:
        try:
            p = self.session_dir(sid) / "session.json"
        except KeyError:
            return None
        if not p.exists():
            return None
        try:
            return Session.model_validate(read_json(p))
        except Exception:
            return None

    def list(self) -> list[Session]:
        out = []
        for p in self.dir.glob("*/session.json"):
            s = self.load(p.parent.name)
            if s:
                out.append(s)
        out.sort(key=lambda s: s.created_at, reverse=True)
        return out

    def delete(self, sid: str) -> bool:
        """Remove transcript, report, audio and metadata - the whole session folder."""
        try:
            d = self.session_dir(sid)
        except KeyError:
            return False
        if d.exists():
            shutil.rmtree(d, ignore_errors=False)
            return True
        return False

    def purge_older_than(self, days: float) -> int:
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        n = 0
        for d in self.dir.iterdir():
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        return n
