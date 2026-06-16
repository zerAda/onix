"""db — Couche d'accès base de données factorisée (onix-actions).

WS-CW1 — Rendre onix-actions STATELESS pour le multi-réplica.

Tout l'état persistant de onix-actions (kill-switch & flags `admin_state`,
événements d'usage `usage_tracker`, journal d'audit chaîné `audit_log`, tâches
`tasks`) transitait par SQLite local — un point unique de défaillance qui
EMPÊCHE le scale-out : deux répliques avec deux SQLite ne voient pas le même
état. Ce module introduit une couche d'accès paramétrée :

  * **SQLite par défaut** (`ONIX_DB_BACKEND` absent ou `=sqlite`) — comportement
    historique mono-poste **strictement inchangé** ;
  * **Postgres opt-in** (`ONIX_DB_BACKEND=postgres` + `ONIX_DB_URL=...`) — toutes
    les répliques partagent le MÊME socle (cohérence HA). On peut aussi composer
    l'URL depuis les variables du chart (`POSTGRES_HOST`, `POSTGRES_USER`,
    `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`) si `ONIX_DB_URL` est vide.

Conception (compatibilité maximale, surface de changement minimale) :

  * `connect()` renvoie un **context manager** qui se comporte comme une
    `sqlite3.Connection` : `with connect() as conn:` ouvre une connexion,
    `conn.execute(sql, params)` exécute, `conn.commit()` valide, et la sortie du
    `with` **commit** (sqlite) / **commit ou rollback** (postgres) puis ferme —
    exactement la sémantique attendue par le code appelant existant.
  * Les requêtes SQL restent écrites en **dialecte SQLite** (placeholders `?` et
    `:nom`, `INSERT OR REPLACE`, `ON CONFLICT(col) DO UPDATE`, `PRAGMA
    table_info`, `sqlite_master`). Un **adaptateur** traduit à la volée vers
    Postgres (placeholders `%s` / `%(nom)s`, `INSERT ... ON CONFLICT DO UPDATE`,
    `information_schema`). Les modules métier n'ont donc **rien** à réécrire.
  * Les lignes renvoyées sont indexables par **nom de colonne** (`row["x"]`),
    comme `sqlite3.Row`, dans les deux backends.

La chaîne d'audit HMAC (`audit_log`) reste vérifiable en Postgres : chaque
`append_audit` lit le dernier maillon puis insère le suivant DANS LA MÊME
connexion/transaction (verrou applicatif `_lock` côté process + commit atomique).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import Any, Iterable, List, Optional, Tuple

# Verrou applicatif partagé. En SQLite mono-process il sérialise les écritures
# (SQLite n'aime pas l'écriture concurrente). En Postgres il reste utile pour
# rendre ATOMIQUE la séquence lecture-du-dernier-maillon + insertion de la chaîne
# d'audit AU SEIN d'un même process ; la cohérence INTER-process/réplique est, elle,
# garantie par la transaction Postgres (voir append_audit). Réentrant car certains
# chemins (init_db -> ensure_schema) ré-acquièrent le verrou.
_lock = threading.RLock()

_BACKEND_ENV = "ONIX_DB_BACKEND"
_URL_ENV = "ONIX_DB_URL"
_DB_PATH_ENV = "ONIX_ACTIONS_DB"
_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "data", "onix_actions.db")


def backend() -> str:
    """Backend actif : 'postgres' si `ONIX_DB_BACKEND=postgres`, sinon 'sqlite'.

    Lu À CHAQUE APPEL (pas mis en cache) pour rester compatible avec la suite de
    tests qui recharge les modules après avoir muté l'environnement."""
    raw = (os.environ.get(_BACKEND_ENV) or "sqlite").strip().lower()
    return "postgres" if raw in ("postgres", "postgresql", "pg") else "sqlite"


def is_postgres() -> bool:
    return backend() == "postgres"


# ---------------------------------------------------------------------------
# SQLite (chemin par défaut, inchangé)
# ---------------------------------------------------------------------------
def sqlite_path() -> str:
    return os.path.abspath(os.environ.get(_DB_PATH_ENV) or _DEFAULT_DB)


def _connect_sqlite() -> sqlite3.Connection:
    path = sqlite_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Postgres (opt-in) — URL résolue depuis ONIX_DB_URL ou variables du chart
# ---------------------------------------------------------------------------
def postgres_dsn() -> str:
    """DSN Postgres. Priorité à `ONIX_DB_URL` ; à défaut, composé depuis les
    variables du chart Helm (`POSTGRES_HOST`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/
    `POSTGRES_DB`/`POSTGRES_PORT`). Lève si rien n'est exploitable."""
    url = (os.environ.get(_URL_ENV) or "").strip()
    if url:
        return url
    host = (os.environ.get("POSTGRES_HOST") or "").strip()
    if not host:
        raise RuntimeError(
            "ONIX_DB_BACKEND=postgres mais ni ONIX_DB_URL ni POSTGRES_HOST ne sont "
            "définis. Fournir ONIX_DB_URL (ex: postgresql://user:pass@host:5432/db) "
            "ou les variables POSTGRES_*."
        )
    user = (os.environ.get("POSTGRES_USER") or "postgres").strip()
    password = os.environ.get("POSTGRES_PASSWORD") or ""
    port = (os.environ.get("POSTGRES_PORT") or "5432").strip()
    db = (os.environ.get("POSTGRES_DB") or "onyx").strip()
    # Schéma dédié optionnel (isolation de la base Onyx). Câblé via search_path.
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _pg_connect_kwargs() -> dict:
    """Options de connexion psycopg : `dict_row` (accès par nom), `autocommit=False`
    (transactions explicites), et `options=-c search_path=...` si un schéma dédié
    est demandé (`ONIX_DB_SCHEMA`, défaut `public`)."""
    from psycopg.rows import dict_row  # import paresseux (psycopg optionnel)

    kwargs: dict[str, Any] = {"row_factory": dict_row, "autocommit": False}
    schema = (os.environ.get("ONIX_DB_SCHEMA") or "").strip()
    if schema:
        kwargs["options"] = f"-c search_path={schema}"
    return kwargs


# ===========================================================================
# Adaptateur SQL SQLite -> Postgres
# ===========================================================================
# Réécritures de DDL/DML propres au dialecte. Les modules métier écrivent en
# SQLite ; on traduit pour Postgres sans toucher au code appelant.
_TYPE_REWRITES: Tuple[Tuple[str, str], ...] = (
    # `TEXT PRIMARY KEY` reste valide en PG. INTEGER/REAL/TEXT existent aussi.
    # Pas de réécriture de type nécessaire (types communs SQL).
)


def _translate_create_table(sql: str) -> str:
    """CREATE TABLE IF NOT EXISTS reste valide en Postgres. INTEGER/REAL/TEXT y
    existent. Aucune réécriture nécessaire au-delà des placeholders."""
    return sql


def _translate_upsert(sql: str) -> str:
    """Traduit les idiomes d'upsert SQLite vers Postgres.

      * `INSERT OR REPLACE INTO t(...)`  -> `INSERT INTO t(...) ON CONFLICT (<pk>)
        DO UPDATE SET col=EXCLUDED.col, ...` (sur la clé primaire). Utilisé par
        usage_tracker.emit_usage_event (réémission idempotente d'un event_id).
      * `ON CONFLICT(col) DO UPDATE SET x=excluded.x` -> `excluded` reste valide en
        PG, mais PG exige un ESPACE : `ON CONFLICT (col)`. On normalise.
    """
    out = sql
    # `excluded.` (sqlite) == `EXCLUDED.` (pg) — insensible à la casse, on garde tel quel.
    # PG accepte `ON CONFLICT(col)` SANS espace ? Non : il EXIGE `ON CONFLICT (col)`.
    out = re.sub(r"ON\s+CONFLICT\s*\(", "ON CONFLICT (", out, flags=re.IGNORECASE)

    m = re.match(r"\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]*)\)", out, flags=re.IGNORECASE)
    if m:
        table, cols_blob = m.group(1), m.group(2)
        cols = [c.strip() for c in cols_blob.split(",") if c.strip()]
        # Sécurité : ces "identifiants" proviennent de SQL INTERNE (jamais d'entrée
        # utilisateur), mais on les VALIDE quand même contre une allowlist stricte
        # d'identifiants SQL (anti-injection défensif) avant toute interpolation.
        if not all(_IDENTIFIER_RE.match(c) for c in cols):
            raise ValueError(f"Identifiant de colonne non valide dans : {cols_blob!r}")
        # PK connue par table (les schémas onix-actions ont une PK explicite).
        pk = _PRIMARY_KEYS.get(table.lower(), cols[0] if cols else "id")
        if not _IDENTIFIER_RE.match(pk):
            raise ValueError(f"Clé primaire non valide : {pk!r}")
        set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != pk)
        head = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", out[: m.end()], flags=re.IGNORECASE)
        tail = out[m.end():]
        # nosec B608 : pas d'injection — `pk`/colonnes validés contre _IDENTIFIER_RE
        # (allowlist d'identifiants), issus de SQL interne, pas d'entrée utilisateur.
        out = f"{head}{tail} ON CONFLICT ({pk}) DO UPDATE SET {set_clause}"  # nosec B608
    return out


# Allowlist d'identifiants SQL (colonnes / clés) pour la traduction d'upsert.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# Clés primaires par table (pour traduire INSERT OR REPLACE -> ON CONFLICT).
_PRIMARY_KEYS = {
    "admin_state": "key",
    "admin_audit": "action_id",
    "tasks": "task_id",
    "usage_events": "event_id",
}


def _translate_introspection(sql: str) -> Optional[Tuple[str, Optional[tuple]]]:
    """Traduit les requêtes d'introspection SQLite. Renvoie `(sql_pg, params)` ou
    None si la requête n'est pas une introspection.

    Le nom de table est TOUJOURS passé en **paramètre lié** (jamais interpolé dans
    la chaîne SQL) — pas de construction de requête par concaténation (anti
    SQL-injection ; le nom vient certes de SQL interne, mais on reste propre).

      * `PRAGMA table_info(t)` -> colonnes via information_schema.columns ;
      * `SELECT name FROM sqlite_master WHERE type='table' AND name=?`
        -> information_schema.tables.
    """
    m = re.match(r"\s*PRAGMA\s+table_info\(\s*(\w+)\s*\)\s*;?\s*$", sql, flags=re.IGNORECASE)
    if m:
        table = m.group(1)
        # Colonne renvoyée nommée `name` (comme PRAGMA) -> compat avec
        # `{r["name"] for r in ...}` côté audit_log.ensure_schema.
        pg = (
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_name=%s AND table_schema=current_schema()"
        )
        return pg, (table,)
    if re.search(r"FROM\s+sqlite_master", sql, flags=re.IGNORECASE):
        # `SELECT name FROM sqlite_master WHERE type='table' AND name=?` : le nom de
        # table est déjà fourni en paramètre par l'appelant (placeholder conservé).
        pg = (
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=%s"
        )
        return pg, None
    return None


_NAMED_PARAM_RE = re.compile(r":(\w+)")


def _translate_params(sql: str) -> str:
    """Placeholders SQLite -> psycopg :
      * positionnels `?`      -> `%s`
      * nommés       `:nom`   -> `%(nom)s`
    (en évitant les `::cast` Postgres — non utilisés ici de toute façon)."""
    # Nommés d'abord (sinon le `?` regex ne les touche pas, mais l'ordre est sûr).
    sql = _NAMED_PARAM_RE.sub(lambda m: f"%({m.group(1)})s", sql)
    # `%` littéraux éventuels devraient être doublés ; le code onix-actions n'en
    # contient pas dans le SQL (les LIKE 'blocked_user:%' SONT des littéraux dans
    # la chaîne, pas des placeholders psycopg -> il FAUT les protéger).
    sql = sql.replace("?", "%s")
    return sql


def translate_sql(sql: str) -> Tuple[str, Optional[tuple]]:
    """Traduit une requête écrite en dialecte SQLite vers Postgres.

    Renvoie `(sql_pg, params_override)`. `params_override` n'est non-None que pour
    une introspection dont le paramètre est extrait de la requête (ex. nom de
    table de `PRAGMA table_info`) — il REMPLACE alors les params d'origine.
    Dans tous les autres cas il vaut None (on garde les params de l'appelant)."""
    intro = _translate_introspection(sql)
    if intro is not None:
        return intro
    out = _translate_create_table(sql)
    out = _translate_upsert(out)
    # Les LIKE '...%' contiennent des `%` littéraux : psycopg (paramstyle pyformat)
    # interprète `%` -> il faut les doubler pour qu'ils restent littéraux.
    # On protège les `%` existants AVANT d'injecter nos `%s`/`%(x)s`.
    out = out.replace("%", "%%")
    out = _translate_params(out)
    return out, None


# ===========================================================================
# Connexion unifiée (context manager type sqlite3.Connection)
# ===========================================================================
class _PgCursorResult:
    """Enveloppe le résultat d'un `execute` Postgres pour offrir l'API attendue
    par le code SQLite : `.fetchone()`, `.fetchall()`, `.rowcount`."""

    __slots__ = ("_cur",)

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> List[Any]:
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class _PgConnection:
    """Adaptateur de connexion Postgres exposant l'interface `sqlite3.Connection`
    utilisée par onix-actions : `execute(sql, params)`, `commit()`, `rollback()`,
    `close()`, et le protocole context manager.

    Traduit chaque requête (dialecte + placeholders) à la volée. Les lignes sont
    des `dict` (via psycopg `dict_row`), donc `row["col"]` fonctionne comme
    `sqlite3.Row` ; `dict(row)` aussi.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None) -> _PgCursorResult:
        cur = self._conn.cursor()
        translated, params_override = translate_sql(sql)
        # Un override (introspection) REMPLACE les params d'origine (ex. nom de
        # table extrait de PRAGMA). Sinon on garde ceux de l'appelant.
        effective = params_override if params_override is not None else params
        if effective is None:
            cur.execute(translated)
        else:
            # psycopg accepte une séquence (pour %s) ou un mapping (pour %(nom)s).
            cur.execute(translated, effective)
        return _PgCursorResult(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "_PgConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Sémantique sqlite3.Connection comme context manager : commit si pas
        # d'exception, rollback sinon — PUIS on ferme (le code onix-actions ouvre
        # une connexion par `with`, comme avec sqlite).
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()


def connect():
    """Ouvre une connexion selon le backend actif. Utilisable en context manager
    (`with connect() as conn:`) exactement comme `sqlite3.connect(...)`.

    - SQLite : renvoie la `sqlite3.Connection` native (comportement historique).
    - Postgres : renvoie un `_PgConnection` qui traduit le SQL et expose la même
      interface (execute/fetch*/rowcount/commit/context manager).
    """
    if is_postgres():
        import psycopg  # import paresseux : psycopg n'est requis qu'en mode postgres

        raw = psycopg.connect(postgres_dsn(), **_pg_connect_kwargs())
        return _PgConnection(raw)
    return _connect_sqlite()


# Alias historique : le code existant importe `_connect` depuis admin_state, qui
# le réexporte depuis ici. On expose les deux noms.
_connect = connect
