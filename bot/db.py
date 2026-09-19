from datetime import datetime, timezone

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    role TEXT NOT NULL DEFAULT 'user'
        CHECK(role IN ('admin', 'assistant', 'user')),
    is_blocked INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    is_paid INTEGER NOT NULL DEFAULT 0,
    requests_limit INTEGER,
    requests_used_today INTEGER NOT NULL DEFAULT 0,
    requests_reset_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);

CREATE TABLE IF NOT EXISTS requests_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK(kind IN ('search', 'topic')),
    query_text TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_log_user_created
    ON requests_log(user_id, created_at);

CREATE TABLE IF NOT EXISTS topics_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    query_hash TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    topic_text TEXT NOT NULL,
    post_text TEXT NOT NULL,
    media_suggestion TEXT,
    model_used TEXT,
    created_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def register_user(
        self, tg_id: int, username: str | None
    ) -> tuple[aiosqlite.Row, bool]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            return row, False
        await self._conn.execute(
            "INSERT INTO users (tg_id, username, created_at) VALUES (?, ?, ?)",
            (tg_id, username, _now_iso()),
        )
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            return await cur.fetchone(), True

    async def get_user_by_tg_id(self, tg_id: int) -> aiosqlite.Row | None:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM users WHERE tg_id = ?", (tg_id,)
        ) as cur:
            return await cur.fetchone()

    async def save_topics_cache(
        self, user_id: int, query_hash: str, topics_json: str
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM topics_cache WHERE user_id = ?", (user_id,)
        )
        await self._conn.execute(
            "INSERT INTO topics_cache (user_id, query_hash, topics_json, created_at)"
            " VALUES (?, ?, ?, ?)",
            (user_id, query_hash, topics_json, _now_iso()),
        )
        await self._conn.commit()

    async def get_topics_cache(
        self, user_id: int, query_hash: str
    ) -> str | None:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT topics_json FROM topics_cache"
            " WHERE user_id = ? AND query_hash = ?",
            (user_id, query_hash),
        ) as cur:
            row = await cur.fetchone()
        return row["topics_json"] if row else None

    async def log_request(
        self, user_id: int, kind: str, query_text: str | None
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO requests_log (user_id, kind, query_text, created_at)"
            " VALUES (?, ?, ?, ?)",
            (user_id, kind, (query_text or "")[:500], _now_iso()),
        )
        await self._conn.commit()

    async def save_post(
        self,
        user_id: int,
        topic_text: str,
        post_text: str,
        media_suggestion: str | None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO posts (user_id, topic_text, post_text,"
            " media_suggestion, model_used, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, topic_text[:500], post_text,
             media_suggestion, None, _now_iso()),
        )
        await self._conn.commit()

    async def sync_admins(self, admin_ids: list[int]) -> None:
        """Выдаёт роль admin пользователям из ADMIN_IDS;
        снимает роль с тех, кого убрали из env."""
        assert self._conn is not None
        ids = list(admin_ids)
        if ids:
            placeholders = ",".join("?" * len(ids))
            await self._conn.execute(
                f"UPDATE users SET role = 'admin'"
                f" WHERE tg_id IN ({placeholders}) AND role != 'admin'",
                ids,
            )
            await self._conn.execute(
                f"UPDATE users SET role = 'user'"
                f" WHERE role = 'admin' AND tg_id NOT IN ({placeholders})",
                ids,
            )
        else:
            await self._conn.execute(
                "UPDATE users SET role = 'user' WHERE role = 'admin'"
            )
        await self._conn.commit()

    async def list_users(self) -> list[aiosqlite.Row]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM users WHERE is_deleted = 0 ORDER BY id"
        ) as cur:
            return await cur.fetchall()

    async def set_role(self, user_pk: int, role: str) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_pk)
        )
        await self._conn.commit()

    async def set_blocked(self, user_pk: int, blocked: bool) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE users SET is_blocked = ? WHERE id = ?",
            (1 if blocked else 0, user_pk),
        )
        await self._conn.commit()

    async def soft_delete(self, user_pk: int) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE users SET is_deleted = 1, is_blocked = 1 WHERE id = ?",
            (user_pk,),
        )
        await self._conn.commit()

    async def check_and_consume_quota(
        self, user: aiosqlite.Row, default_limit: int
    ) -> bool:
        """True = запрос разрешён (счётчик увеличен).
        Админы, ассистенты и is_paid — без лимита."""
        assert self._conn is not None
        if user["role"] in ("admin", "assistant") or user["is_paid"]:
            return True
        limit = (
            user["requests_limit"]
            if user["requests_limit"] is not None
            else default_limit
        )
        today = datetime.now(timezone.utc).date().isoformat()
        used = user["requests_used_today"]
        if user["requests_reset_at"] != today:
            used = 0
        if used >= limit:
            return False
        await self._conn.execute(
            "UPDATE users SET requests_used_today = ?, requests_reset_at = ?"
            " WHERE id = ?",
            (used + 1, today, user["id"]),
        )
        await self._conn.commit()
        return True

    async def get_stats(self) -> list[aiosqlite.Row]:
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT u.username, u.tg_id,
                   COUNT(CASE WHEN date(r.created_at) = date('now') THEN 1 END)
                       AS requests_today,
                   COUNT(r.id) AS requests_total
            FROM users u
            LEFT JOIN requests_log r ON r.user_id = u.id
            WHERE u.is_deleted = 0
            GROUP BY u.id
            ORDER BY requests_total DESC
            """
        ) as cur:
            return await cur.fetchall()
