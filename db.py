import aiosqlite

DB_PATH = "xp.sqlite"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS xp (
  guild_id TEXT NOT NULL,
  user_id  TEXT NOT NULL,
  xp       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild_id, user_id)
);
"""

GET_USER_SQL = "SELECT xp FROM xp WHERE guild_id = ? AND user_id = ?;"
UPSERT_USER_SQL = """
INSERT INTO xp (guild_id, user_id, xp) VALUES (?, ?, ?)
ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = excluded.xp;
"""
TOP_USERS_SQL = """
SELECT user_id, xp FROM xp
WHERE guild_id = ?
ORDER BY xp DESC
LIMIT ?;
"""

async def init_db():
    conn = await aiosqlite.connect(DB_PATH)
    await conn.execute(CREATE_TABLE_SQL)
    await conn.commit()
    return conn

async def get_xp(conn, guild_id: str, user_id: str) -> int:
    async with conn.execute(GET_USER_SQL, (guild_id, user_id)) as cur:
        row = await cur.fetchone()
        return row[0] if row else 0

async def set_xp(conn, guild_id: str, user_id: str, xp: int) -> None:
    await conn.execute(UPSERT_USER_SQL, (guild_id, user_id, xp))
    await conn.commit()

async def get_top(conn, guild_id: str, limit: int = 10):
    async with conn.execute(TOP_USERS_SQL, (guild_id, limit)) as cur:
        return await cur.fetchall()
