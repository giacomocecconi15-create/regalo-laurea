"""Backup dei dati personali: registro delle giocate, strategie, roadmap, impostazioni.

Dove stanno i tuoi dati (tutto dentro la cartella del progetto):
    data/gestionale.duckdb   registro delle giocate, conti e movimenti
    strategies.json          strategie salvate
    data/roadmap.json        stato della roadmap
    data/settings.json       impostazioni (aggiornamento automatico, chiavi, avvisi)
    data/trading_db.duckdb   storici scaricati: NON va salvato, si ricostruisce con un aggiornamento

Ogni backup e' un file zip in data/backup/ (uno al giorno, si tengono gli ultimi 14) e, se indicata,
viene copiato anche in una seconda cartella (per esempio iCloud Drive o una chiavetta).
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config as C  # noqa: E402

BACKUP_DIR = C.DATA_DIR / "backup"
KEEP = 14


def personal_files(root: Path | None = None) -> list[Path]:
    root = Path(root or C.ROOT)
    names = ["data/gestionale.duckdb", "strategies.json", "data/roadmap.json", "data/settings.json", "data/notified.json"]
    return [root / n for n in names if (root / n).exists()]


def run_backup(extra_dir: str | Path | None = None, root: Path | None = None, backup_dir: Path | None = None,
               today: date | None = None) -> tuple[Path | None, str]:
    """Crea (o aggiorna) il backup di oggi. Ritorna (file creato, messaggio)."""
    root = Path(root or C.ROOT)
    files = personal_files(root)
    if not files:
        return None, "Niente da salvare: non hai ancora registrato giocate, strategie o impostazioni."
    backup_dir = Path(backup_dir or BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"backup_{(today or date.today()):%Y%m%d}.zip"
    tmp = target.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(root).as_posix())
    tmp.replace(target)
    for old in sorted(backup_dir.glob("backup_*.zip"))[:-KEEP]:
        old.unlink()
    msg = f"Backup creato: {target.name} ({len(files)} file)."
    if extra_dir:
        try:
            extra = Path(extra_dir).expanduser()
            extra.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, extra / target.name)
            msg += f" Copiato anche in {extra}."
        except OSError as exc:
            msg += f" Copia nella seconda cartella non riuscita: {exc}."
    return target, msg


def last_backup(backup_dir: Path | None = None) -> Path | None:
    files = sorted(Path(backup_dir or BACKUP_DIR).glob("backup_*.zip"))
    return files[-1] if files else None


def backup_if_due(extra_dir: str | Path | None = None) -> str | None:
    """Backup automatico: al massimo uno al giorno. Ritorna il messaggio se e' stato eseguito."""
    last = last_backup()
    if last is not None and last.name == f"backup_{date.today():%Y%m%d}.zip":
        return None
    path, msg = run_backup(extra_dir)
    return msg if path else None


def restore(zip_path: str | Path, root: Path | None = None) -> list[str]:
    """Ripristina un backup sovrascrivendo i file personali. Ritorna i file ripristinati."""
    root = Path(root or C.ROOT)
    allowed = {"data/gestionale.duckdb", "strategies.json", "data/roadmap.json", "data/settings.json", "data/notified.json"}
    done = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name in allowed:                      # si estraggono solo i file attesi, mai percorsi arbitrari
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes(z.read(name))
                done.append(name)
    return done
