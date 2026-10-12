
import sqlite3

#RLock matters when a locked method calls another method that also acquires the same lock
from threading import RLock

from models import ServiceError


class WatchlistService:
    def __init__(self, database_path: str):
        self._db = sqlite3.connect(
            database_path,

            #threads other than the one that created database, can also access the table
            check_same_thread=False,
        )

        self._db.execute("PRAGMA foreign_keys = ON")

        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entries (
                list_id INTEGER NOT NULL
                    REFERENCES watchlists(id) ON DELETE CASCADE,
                movie_id INTEGER NOT NULL,
                PRIMARY KEY (list_id, movie_id)
            );
            """
        )

        #in entries , the primary key is composite

        #creating lock to be used by other functions
        self._lock = RLock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str):
            raise ServiceError(400, "invalid_name")

        name = name.strip()

        if not 1 <= len(name) <= 80:
            raise ServiceError(400, "invalid_name")

        return name

    #we use the staticmethod decorator when we dont need to access self

    @staticmethod
    def _validate_movie_id(movie_id: int) -> None:
        if (
            type(movie_id) is not int
            or not 1 <= movie_id <= 2147483647
        ):
            raise ServiceError(400, "invalid_movie")

    #retrieves watchlist to a particular user
    def _get_locked(self, user: str, list_id: int) -> dict:
        row = self._db.execute(
            """
            SELECT id, owner, name
            FROM watchlists
            WHERE id = ? AND owner = ?
            """,
            (list_id, user),
        ).fetchone()

        if row is None:
            raise ServiceError(404, "list_not_found")

        movie_rows = self._db.execute(
            """
            SELECT movie_id
            FROM entries
            WHERE list_id = ?
            ORDER BY movie_id ASC
            """,
            (list_id,),
        ).fetchall()

        return {
            "id": row[0],
            "owner": row[1],
            "name": row[2],
            "movies": [movie[0] for movie in movie_rows],
        }

    def get(self, user: str, list_id: int) -> dict:
        with self._lock:
            return self._get_locked(user, list_id)

    def create(self, user: str, name: str) -> tuple[int, dict]:
        name = self._name(name)

        #ensures that only one thread at a time can execute this section
        with self._lock:

            #commits the transaction on successful completion or rolls it back
            with self._db:
                cursor = self._db.execute(
                    """
                    INSERT INTO watchlists(owner, name)
                    VALUES (?, ?)
                    """,
                    (user, name),
                )

                list_id = cursor.lastrowid

                if list_id is None:
                    raise RuntimeError("Failed to obtain watchlist ID")

                result = self._get_locked(user, list_id)

            #the transaction has committed before returning.
            return 201, result

    def rename(
        self, user: str, list_id: int, name: str
    ) -> tuple[int, dict]:
        name = self._name(name)

        with self._lock:
            with self._db:
                #check ownership before changing anything.
                self._get_locked(user, list_id)

                self._db.execute(
                    """
                    UPDATE watchlists
                    SET name = ?
                    WHERE id = ? AND owner = ?
                    """,
                    (name, list_id, user),
                )

                result = self._get_locked(user, list_id)

            return 200, result

    def delete(self, user: str, list_id: int) -> tuple[int, None]:
        with self._lock:
            with self._db:
                #raises list_not_found if missing or owned by someone else.
                self._get_locked(user, list_id)

                cursor = self._db.execute(
                    """
                    DELETE FROM watchlists
                    WHERE id = ? AND owner = ?
                    """,
                    (list_id, user),
                )

                if cursor.rowcount != 1:
                    raise ServiceError(404, "list_not_found")

            #foreign-key cascading deletes this list's movie memberships.
            return 204, None

    def add_movie(
        self, user: str, list_id: int, movie_id: int
    ) -> tuple[int, dict]:
        #validation must happen before the ownership check.
        self._validate_movie_id(movie_id)

        with self._lock:
            with self._db:
                self._get_locked(user, list_id)

                cursor = self._db.execute(
                    """
                    INSERT OR IGNORE INTO entries(list_id, movie_id)
                    VALUES (?, ?)
                    """,
                    (list_id, movie_id),
                )

                # rowcount is 1 for an insert and 0 for an ignored duplicate.
                status = 201 if cursor.rowcount == 1 else 200

                result = self._get_locked(user, list_id)

            return status, result

    def remove_movie(
        self, user: str, list_id: int, movie_id: int
    ) -> tuple[int, None]:
        #validate the movie ID before checking list ownership.
        self._validate_movie_id(movie_id)

        with self._lock:
            with self._db:
                self._get_locked(user, list_id)

                cursor = self._db.execute(
                    """
                    DELETE FROM entries
                    WHERE list_id = ? AND movie_id = ?
                    """,
                    (list_id, movie_id),
                )

                if cursor.rowcount == 0:
                    raise ServiceError(404, "movie_not_found")

            return 204, None
