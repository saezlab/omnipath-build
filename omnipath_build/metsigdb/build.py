"""Build the MetSigDB membership substrate from the build database."""

from __future__ import annotations

import logging
from pathlib import Path

from psycopg2 import sql
import psycopg2.extensions

_SQL_DIR = Path(__file__).with_name('sql')

logger = logging.getLogger(__name__)

TABLE = 'metsigdb_membership'


def _sql_text(name: str) -> str:
    return (_SQL_DIR / name).read_text(encoding='utf-8')


def ensure_membership_table(
    conn: psycopg2.extensions.connection,
    *,
    schema: str = 'public',
) -> None:
    """Create the membership table and its filter indexes if they are absent.

    The DDL is idempotent. Applying it to a populated table leaves both the
    schema and the rows untouched, so a rebuild can call it first without a
    guard.
    """
    with conn.cursor() as cur:
        # Unqualified names in the DDL land in the target schema. public.*
        # still resolves, so the table can reference the canonical layer.
        cur.execute(
            sql.SQL('SET search_path = {}, public').format(sql.Identifier(schema))
        )
        cur.execute(_sql_text('membership_table.sql'))
        cur.execute('RESET search_path')
    conn.commit()
    logger.info('metsigdb: %s.%s is present', schema, TABLE)
