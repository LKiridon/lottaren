
# language: python
"""core.py

Datalager + logik fr artikelutdelning.
- SQLite
- Import av artiklar frn Excel/CSV (pandas)
- Rster/po
ng
- Viktad lottning med win-penalty

Anv
nds av ui_user.py och ui_admin.py.
"""

from __future__ import annotations

import io
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

DB_PATH = os.environ.get('DB_PATH', 'raffle.db')
POINT_BUDGET = int(os.environ.get('POINT_BUDGET', '100'))
MAX_PER_ITEM = int(os.environ.get('MAX_PER_ITEM', '0'))  # 0 = no max
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')

# Win-penalty multipliers (editable)
WIN_MULT = {
    0: 1.00,
    1: 0.60,
    2: 0.35,
    3: 0.20,
}
MULT_AFTER = 0.10  # for 4+


def mult_for_wins(w: int) -> float:
    return WIN_MULT.get(w, MULT_AFTER)


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = db()
    cur = con.cursor()

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT,
        quantity INTEGER NOT NULL DEFAULT 1
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS votes (
        participant_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        points INTEGER NOT NULL,
        PRIMARY KEY (participant_id, item_id),
        FOREIGN KEY (participant_id) REFERENCES participants(id),
        FOREIGN KEY (item_id) REFERENCES items(id)
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        participant_id INTEGER,
        weight_snapshot TEXT,         -- JSON: {participant_id: weight}
        created_at INTEGER NOT NULL,
        FOREIGN KEY (item_id) REFERENCES items(id),
        FOREIGN KEY (participant_id) REFERENCES participants(id)
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        seed TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    """
    )

    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    );
    """
    )

    # ensure version counters exist
    cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('votes_version', 0)")
    cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('items_version', 0)")
    cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('alloc_version', 0)")

    con.commit()
    con.close()


def q_all(sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
    con = db()
    rows = con.execute(sql, params).fetchall()
    con.close()
    return rows


def q_one(sql: str, params: Tuple = ()) -> Optional[sqlite3.Row]:
    con = db()
    row = con.execute(sql, params).fetchone()
    con.close()
    return row


def exec_sql(sql: str, params: Tuple = ()) -> None:
    con = db()
    con.execute(sql, params)
    con.commit()
    con.close()


def exec_many(sql: str, params_list: List[Tuple]) -> None:
    con = db()
    con.executemany(sql, params_list)
    con.commit()
    con.close()


def bump_meta(key: str) -> None:
    con = db()
    cur = con.cursor()
    cur.execute("UPDATE meta SET value = value + 1 WHERE key = ?", (key,))
    con.commit()
    con.close()


def get_meta(key: str) -> int:
    row = q_one('SELECT value FROM meta WHERE key = ?', (key,))
    return int(row['value']) if row else 0


# ---------------- Participants ----------------

def get_or_create_participant(name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError('Tomt namn')
    row = q_one('SELECT id FROM participants WHERE name = ?', (name,))
    if row:
        return int(row['id'])
    con = db()
    cur = con.cursor()
    cur.execute(
        'INSERT INTO participants(name, created_at) VALUES(?, ?)',
        (name, int(time.time())),
    )
    con.commit()
    pid = int(cur.lastrowid)
    con.close()
    return pid


def list_participants() -> List[sqlite3.Row]:
    return q_all('SELECT id, name, created_at FROM participants ORDER BY created_at, name')


def delete_participant(pid: int) -> None:
    """Tar bort en deltagare och allt kopplat (rster + ev. vinster i resultat)."""
    con = db()
    cur = con.cursor()
    cur.execute('DELETE FROM votes WHERE participant_id = ?', (pid,))
    cur.execute('DELETE FROM allocations WHERE participant_id = ?', (pid,))
    cur.execute('DELETE FROM participants WHERE id = ?', (pid,))
    con.commit()
    con.close()
    bump_meta('votes_version')
    bump_meta('alloc_version')


# ---------------- Items ----------------

def list_items() -> List[sqlite3.Row]:
    return q_all('SELECT id, name, category, quantity FROM items ORDER BY category, name')


def list_items_with_point_totals() -> List[sqlite3.Row]:
    """Returnerar alla artiklar med totalpoäng (summa av alla deltagares poäng per artikel)."""
    return q_all(
        """
        SELECT i.id, i.name, i.category, i.quantity,
               COALESCE(SUM(v.points), 0) AS total_points,
               COALESCE(SUM(CASE WHEN v.points > 0 THEN 1 ELSE 0 END), 0) AS voters
        FROM items i
        LEFT JOIN votes v ON v.item_id = i.id
        GROUP BY i.id, i.name, i.category, i.quantity
        ORDER BY (COALESCE(SUM(v.points), 0)) DESC, i.category, i.name
        """
    )


def parse_items_file(content: bytes, filename: str) -> pd.DataFrame:
    """Lser .xlsx/.xls/.csv till DataFrame med kolumner: name, category, quantity."""
    fn = filename.lower()
    if fn.endswith('.xlsx') or fn.endswith('.xls'):
        df = pd.read_excel(io.BytesIO(content))
    elif fn.endswith('.csv'):
        df = pd.read_csv(io.BytesIO(content))
    else:
        raise ValueError('Stder bara .xlsx/.xls/.csv')

    df.columns = [str(c).strip().lower() for c in df.columns]
    if 'name' not in df.columns:
        raise ValueError('Kolumnen "name" saknas i filen')

    if 'category' not in df.columns:
        df['category'] = ''
    if 'quantity' not in df.columns:
        df['quantity'] = 1

    df = df[['name', 'category', 'quantity']].copy()
    df['name'] = df['name'].astype(str).str.strip()
    df['category'] = df['category'].fillna('').astype(str).str.strip()
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int)

    df = df[df['name'] != '']
    df.loc[df['quantity'] < 1, 'quantity'] = 1
    return df


# ---------------- Votes ----------------

def get_votes_for_participant(pid: int) -> Dict[int, int]:
    rows = q_all('SELECT item_id, points FROM votes WHERE participant_id = ?', (pid,))
    return {int(r['item_id']): int(r['points']) for r in rows}


def get_votes_detailed(pid: int) -> List[sqlite3.Row]:
    return q_all(
        """
        SELECT i.category, i.name AS item_name, v.points
        FROM votes v
        JOIN items i ON i.id = v.item_id
        WHERE v.participant_id = ?
        ORDER BY i.category, i.name
        """,
        (pid,),
    )


def upsert_votes(pid: int, votes: Dict[int, int]) -> None:
    params = [(pid, int(item_id), int(points)) for item_id, points in votes.items()]
    con = db()
    cur = con.cursor()
    cur.execute('DELETE FROM votes WHERE participant_id = ?', (pid,))
    cur.executemany('INSERT INTO votes(participant_id, item_id, points) VALUES(?, ?, ?)', params)
    con.commit()
    con.close()
    bump_meta('votes_version')


def vote_sum_for_participant(pid: int) -> int:
    row = q_one(
        'SELECT COALESCE(SUM(points), 0) AS s FROM votes WHERE participant_id = ?',
        (pid,),
    )
    return int(row['s']) if row else 0


def participant_has_submitted(pid: int) -> bool:
    row = q_one('SELECT COUNT(*) AS c FROM votes WHERE participant_id = ?', (pid,))
    return int(row['c']) > 0 or POINT_BUDGET == 0


# ---------------- Clears ----------------

def clear_items_and_votes_and_allocations() -> None:
    con = db()
    cur = con.cursor()
    cur.execute('DELETE FROM allocations')
    cur.execute('DELETE FROM runs')
    cur.execute('DELETE FROM votes')
    cur.execute('DELETE FROM items')
    con.commit()
    con.close()
    bump_meta('items_version')
    bump_meta('votes_version')
    bump_meta('alloc_version')


def clear_allocations() -> None:
    con = db()
    cur = con.cursor()
    cur.execute('DELETE FROM allocations')
    cur.execute('DELETE FROM runs')
    con.commit()
    con.close()
    bump_meta('alloc_version')


# ---------------- Draw / results ----------------

@dataclass
class DrawResult:
    run_id: str
    seed: str


def compute_item_competition_scores() -> Dict[int, int]:
    rows = q_all(
        """
        SELECT i.id AS item_id, COALESCE(SUM(v.points), 0) AS s
        FROM items i
        LEFT JOIN votes v ON v.item_id = i.id
        GROUP BY i.id
        """
    )
    return {int(r['item_id']): int(r['s']) for r in rows}


def weighted_choice(rng: random.Random, weights: Dict[int, float]) -> int:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError('No positive weights')
    r = rng.random() * total
    acc = 0.0
    for pid, w in weights.items():
        acc += w
        if acc > r:
            return pid
    return next(iter(weights.keys()))


def run_draw(seed: str) -> DrawResult:
    """Tv8-algoritm:

    Fas A: "alla fr en" (om mjligt)
      - iterera deltagare i slumpad ordning
      - varje deltagare fr hgst 1 artikel baserat p deras pong (viktad slump)
      - en unit tas frn vald artikel (quantity minskar)

    Fas B: dela ut resterande
      - per unit: vikt = points * mult(wins)
      - om ingen rstat p artikeln: ge till slumpad bland de med lgst wins

    Not: Kategorier ignoreras.
    """

    items = list_items()
    participants = list_participants()
    if not items:
        raise ValueError('Inga artiklar inlagda')
    if not participants:
        raise ValueError('Inga deltagare registrerade')

    run_id = f'run_{int(time.time())}'
    rng = random.Random(seed)

    clear_allocations()

    # Preload votes matrix
    votes_by_p: Dict[int, Dict[int, int]] = {}
    for p in participants:
        pid = int(p['id'])
        votes_by_p[pid] = get_votes_for_participant(pid)

    wins: Dict[int, int] = {int(p['id']): 0 for p in participants}
    allocations_to_insert: List[Tuple] = []

    # Remaining quantities per item
    remaining_qty: Dict[int, int] = {}
    for it in items:
        iid = int(it['id'])
        qty = int(it['quantity']) if it['quantity'] is not None else 1
        remaining_qty[iid] = max(1, qty)

    def take_one_unit(item_id: int) -> bool:
        q = remaining_qty.get(item_id, 0)
        if q <= 0:
            return False
        remaining_qty[item_id] = q - 1
        return True

    def remaining_units() -> List[int]:
        units: List[int] = []
        for iid, q in remaining_qty.items():
            if q > 0:
                units.extend([iid] * q)
        return units

    # -------- Fas A: alla fr en frst --------
    pids = [int(p['id']) for p in participants]
    rng.shuffle(pids)

    for pid in pids:
        # Kandidater: artiklar med kvarvarande qty och pts > 0
        item_weights: Dict[int, float] = {}
        pv = votes_by_p.get(pid, {})
        for iid, q in remaining_qty.items():
            if q <= 0:
                continue
            pts = int(pv.get(iid, 0))
            if pts > 0:
                item_weights[iid] = float(pts)

        if not item_weights:
            continue

        chosen_item = weighted_choice(rng, item_weights)
        if not take_one_unit(chosen_item):
            continue

        wins[pid] += 1
        snap = {'phase': 'A', 'item_weights': item_weights}
        allocations_to_insert.append((run_id, chosen_item, pid, json.dumps(snap), int(time.time())))

    # -------- Fas B: dela ut resterande (rttvist) --------
    # Dra mest "konkurrens" frst (hg totalpong), sedan namn
    comp = compute_item_competition_scores()
    name_by_id = {int(r['id']): str(r['name']) for r in items}

    units = remaining_units()
    units.sort(key=lambda iid: (comp.get(int(iid), 0), name_by_id.get(int(iid), '').lower()), reverse=True)

    for iid in units:
        # Kandidater med pts>0
        weight_snapshot: Dict[int, float] = {}
        for pid in wins.keys():
            pts = int(votes_by_p.get(pid, {}).get(iid, 0))
            if pts <= 0:
                continue
            w = pts * mult_for_wins(wins[pid])
            if w > 0:
                weight_snapshot[pid] = float(w)

        if not weight_snapshot:
            # Restartikel: ge till slumpad bland dem med lgst wins
            min_w = min(wins.values()) if wins else 0
            candidates = [pid for pid, w in wins.items() if w == min_w]
            winner = rng.choice(candidates) if candidates else None
            if winner is not None:
                wins[winner] += 1
            snap = {'phase': 'B_rest', 'rule': 'min_wins', 'min_wins': min_w}
            allocations_to_insert.append((run_id, iid, winner, json.dumps(snap), int(time.time())))
            continue

        winner = weighted_choice(rng, weight_snapshot)
        wins[winner] += 1
        snap = {'phase': 'B', 'participant_weights': weight_snapshot}
        allocations_to_insert.append((run_id, iid, winner, json.dumps(snap), int(time.time())))

    exec_sql('INSERT INTO runs(id, seed, created_at) VALUES(?, ?, ?)', (run_id, seed, int(time.time())))
    exec_many(
        'INSERT INTO allocations(run_id, item_id, participant_id, weight_snapshot, created_at) VALUES(?, ?, ?, ?, ?)',
        allocations_to_insert,
    )
    bump_meta('alloc_version')

    return DrawResult(run_id=run_id, seed=seed)


def get_latest_run_id() -> Optional[str]:
    row = q_one('SELECT id FROM runs ORDER BY created_at DESC LIMIT 1')
    return str(row['id']) if row else None


def get_results(run_id: str) -> List[sqlite3.Row]:
    return q_all(
        """
        SELECT a.id, a.item_id, i.name AS item_name, i.category, a.participant_id,
               p.name AS participant_name
        FROM allocations a
        JOIN items i ON i.id = a.item_id
        LEFT JOIN participants p ON p.id = a.participant_id
        WHERE a.run_id = ?
        ORDER BY i.category, i.name
        """,
        (run_id,),
    )
