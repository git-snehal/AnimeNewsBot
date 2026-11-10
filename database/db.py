import sqlite3
import os

class Database:
    def __init__(self, db_file="database/bot.db"):
        os.makedirs("database", exist_ok=True)
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_table()

    def create_table(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS posted (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT UNIQUE,
                title TEXT
            )
        ''')
        self.conn.commit()

    def is_posted(self, link: str, title: str) -> bool:
        self.cursor.execute("SELECT 1 FROM posted WHERE link = ? OR title = ?", (link, title))
        return self.cursor.fetchone() is not None

    def add_posted(self, link: str, title: str):
        try:
            self.cursor.execute("INSERT OR IGNORE INTO posted (link, title) VALUES (?, ?)", (link, title))
            self.conn.commit()
        except Exception as e:
            print(f"DB Error: {e}")