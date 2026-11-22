import os
import re
import sqlite3
import time
from collections import Counter

MEDIA_KINDS = ("trailer", "teaser", "pv")


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


class Database:
    # Same headline from another outlet within this window counts as a duplicate.
    TITLE_WINDOW = 3 * 24 * 3600

    def __init__(self, db_file="database/bot.db"):
        os.makedirs(os.path.dirname(db_file) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create()

    def _create(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT UNIQUE NOT NULL,
                title_norm TEXT,
                posted_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title_norm, posted_at);
            """
        )
        # add the statistics columns to databases created by earlier versions
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(articles)")}
        for name, ddl in (
            ("source", "TEXT"),
            ("kind", "TEXT"),
            ("has_video", "INTEGER DEFAULT 0"),
            ("status", "TEXT DEFAULT 'posted'"),  # posted | seeded | skipped | imported
        ):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE articles ADD COLUMN {name} {ddl}")

        # Import history from the very first version's `posted` table so nothing gets re-posted.
        old = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='posted'"
        ).fetchone()
        if old:
            rows = self.conn.execute("SELECT link, title FROM posted").fetchall()
            self.conn.executemany(
                "INSERT OR IGNORE INTO articles (link, title_norm, posted_at, status) VALUES (?, ?, 0, 'imported')",
                [(link, norm_title(title)) for link, title in rows],
            )
        self.conn.commit()

    # ------------------------------------------------------------------ dedup
    def is_empty(self) -> bool:
        return self.conn.execute("SELECT 1 FROM articles LIMIT 1").fetchone() is None

    def is_posted(self, link: str, title: str) -> bool:
        tn = norm_title(title)
        cutoff = int(time.time()) - self.TITLE_WINDOW
        row = self.conn.execute(
            "SELECT 1 FROM articles WHERE link = ? OR (title_norm = ? AND title_norm != '' AND posted_at >= ?)",
            (link, tn, cutoff),
        ).fetchone()
        return row is not None

    def add_posted(self, link: str, title: str, source=None, kind=None, has_video=False, status="posted"):
        self.conn.execute(
            "INSERT OR IGNORE INTO articles (link, title_norm, posted_at, source, kind, has_video, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (link, norm_title(title), int(time.time()), source, kind, int(bool(has_video)), status),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ statistics
    def stats(self, start_ts: int, end_ts: int) -> dict:
        rows = self.conn.execute(
            "SELECT kind, source, has_video FROM articles WHERE status = 'posted' AND posted_at >= ? AND posted_at < ?",
            (start_ts, end_ts),
        ).fetchall()
        media = [r for r in rows if r[0] in MEDIA_KINDS]
        skipped = self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status = 'skipped' AND posted_at >= ? AND posted_at < ?",
            (start_ts, end_ts),
        ).fetchone()[0]
        return {
            "posted": len(rows),
            "media": len(media),
            "with_video": sum(1 for r in media if r[2]),
            "by_source": Counter(r[1] or "Unknown" for r in rows).most_common(),
            "skipped": skipped,
        }

    def total_posted(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM articles WHERE status = 'posted'").fetchone()[0]
