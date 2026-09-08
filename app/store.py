"""SQLite persistence for product records and derived LLM output."""
import json
import os
import sqlite3
import hashlib
from typing import List, Optional

from .models import Report, now_iso

DB_PATH = os.environ.get(
    "AMAZING_KIMI_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "app.db"),
)

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            input_json TEXT NOT NULL,
            report_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_cache (
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            url TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            result_json TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            PRIMARY KEY (provider, model, url, text_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            subject_key TEXT NOT NULL,
            category TEXT DEFAULT '', maker TEXT DEFAULT '', object_name TEXT DEFAULT '',
            series TEXT DEFAULT '', model TEXT DEFAULT '', dimensions TEXT DEFAULT '',
            sale_type TEXT DEFAULT 'UNKNOWN', sold_at TEXT,
            price REAL NOT NULL, currency TEXT DEFAULT 'EUR',
            normalized_eur REAL, fx_rate REAL, fx_source TEXT DEFAULT '',
            auction_house TEXT DEFAULT '', source_name TEXT DEFAULT '',
            source_url TEXT DEFAULT '', source_tier INTEGER DEFAULT 0,
            price_basis TEXT DEFAULT 'UNKNOWN', comparable_level TEXT DEFAULT '',
            is_active_listing INTEGER DEFAULT 0, listing_checked_at TEXT,
            discovered_at TEXT NOT NULL, raw_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            initial_decision TEXT,
            initial_offer REAL,
            analyst_decision TEXT NOT NULL,
            analyst_opening REAL,
            analyst_target_low REAL,
            analyst_target_high REAL,
            analyst_walkaway REAL,
            final_decision TEXT,
            final_offer REAL,
            final_transaction_price REAL,
            transaction_status TEXT,
            decision_changed TEXT,
            notes TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            maker TEXT NOT NULL,
            target TEXT NOT NULL,
            keywords TEXT DEFAULT '',
            price_low REAL,
            price_high REAL,
            currency TEXT DEFAULT 'EUR',
            markets TEXT DEFAULT 'GLOBAL',
            frequency TEXT DEFAULT 'MANUAL',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            last_checked TEXT,
            baseline_at TEXT,
            baseline_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watch_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            mode TEXT DEFAULT 'BASELINE',
            results_seen INTEGER DEFAULT 0,
            new_items INTEGER DEFAULT 0,
            events INTEGER DEFAULT 0,
            action_alerts INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            errors_json TEXT DEFAULT '[]'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,
            title TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            source_name TEXT DEFAULT '',
            seller TEXT DEFAULT '',
            price REAL,
            currency TEXT DEFAULT 'EUR',
            sale_type TEXT DEFAULT 'UNKNOWN',
            year TEXT,
            attributes_json TEXT DEFAULT '{}',
            source_tier INTEGER DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            opportunity_score INTEGER DEFAULT 0,
            alert_level TEXT DEFAULT 'IGNORE',
            UNIQUE(watch_id, fingerprint)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watch_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            item_id INTEGER,
            event_type TEXT NOT NULL,
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            opportunity_score INTEGER DEFAULT 0,
            alert_level TEXT DEFAULT 'IGNORE',
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


# ================================================================ Price evidence

def price_record_upsert(record: dict) -> int:
    """Persist one auditable sold/listing record. URL+lot+price dedupes repeated scans."""
    raw_key = "|".join(str(record.get(k) or "") for k in
                       ("source_url", "lot", "sold_at", "price", "currency", "object_name"))
    fingerprint = record.get("fingerprint") or hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM price_records WHERE fingerprint=?", (fingerprint,)).fetchone()
        values = (
            record.get("subject_key", ""), record.get("category", ""), record.get("maker", ""),
            record.get("object_name", ""), record.get("series", ""), record.get("model", ""),
            record.get("dimensions", ""), record.get("sale_type", "UNKNOWN"), record.get("sold_at"),
            record.get("price"), record.get("currency", "EUR"), record.get("normalized_eur"),
            record.get("fx_rate"), record.get("fx_source", ""), record.get("auction_house", ""),
            record.get("source_name", ""), record.get("source_url", ""),
            record.get("source_tier", 0), record.get("price_basis", "UNKNOWN"),
            record.get("comparable_level", ""), int(bool(record.get("is_active_listing"))),
            record.get("listing_checked_at"), record.get("discovered_at") or now_iso(),
            json.dumps(record.get("raw") or record, ensure_ascii=False),
        )
        if row:
            conn.execute(
                "UPDATE price_records SET subject_key=?,category=?,maker=?,object_name=?,series=?,model=?,dimensions=?,sale_type=?,sold_at=?,price=?,currency=?,normalized_eur=?,fx_rate=?,fx_source=?,auction_house=?,source_name=?,source_url=?,source_tier=?,price_basis=?,comparable_level=?,is_active_listing=?,listing_checked_at=?,discovered_at=?,raw_json=? WHERE id=?",
                values + (row[0],),
            )
            rid = row[0]
        else:
            cur = conn.execute(
                "INSERT INTO price_records (fingerprint,subject_key,category,maker,object_name,series,model,dimensions,sale_type,sold_at,price,currency,normalized_eur,fx_rate,fx_source,auction_house,source_name,source_url,source_tier,price_basis,comparable_level,is_active_listing,listing_checked_at,discovered_at,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fingerprint,) + values,
            )
            rid = cur.lastrowid
        conn.commit()
        return rid
    finally:
        conn.close()


def price_records(subject_key: str = "", sale_type: Optional[str] = None) -> List[dict]:
    """Read structured evidence. Exact subject_key prevents cross-series contamination."""
    conn = _conn()
    try:
        sql = "SELECT * FROM price_records WHERE 1=1"
        vals = []
        if subject_key:
            sql += " AND subject_key=?"
            vals.append(subject_key)
        if sale_type:
            sql += " AND sale_type=?"
            vals.append(sale_type)
        sql += " ORDER BY COALESCE(sold_at, discovered_at) DESC, id DESC"
        rows = conn.execute(sql, vals).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM price_records").description]
        out = []
        for row in rows:
            item = dict(zip(cols, row))
            try:
                item["raw"] = json.loads(item.pop("raw_json") or "{}")
            except json.JSONDecodeError:
                item["raw"] = {}
            out.append(item)
        return out
    finally:
        conn.close()


# ================================================================ 0.5 Watch CRUD

def watch_create(w: dict) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO watches (category, maker, target, keywords, price_low, price_high, "
            "currency, markets, frequency, notes, status, created_at, baseline_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (w.get("category", "ART"), w.get("maker", ""), w.get("target", ""),
             w.get("keywords", ""), w.get("price_low"), w.get("price_high"),
             w.get("currency", "EUR"), w.get("markets", "GLOBAL"),
             w.get("frequency", "MANUAL"), w.get("notes", ""),
             w.get("status", "ACTIVE"), now_iso(),
             json.dumps(w.get("baseline") or {}, ensure_ascii=False)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def watch_update(wid: int, fields: dict) -> None:
    allowed = {"category", "maker", "target", "keywords", "price_low", "price_high",
               "currency", "markets", "frequency", "notes", "status",
               "last_checked", "baseline_at", "baseline"}
    conn = _conn()
    try:
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "baseline":
                sets.append("baseline_json=?")
                vals.append(json.dumps(v or {}, ensure_ascii=False))
            else:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.append(wid)
            conn.execute(f"UPDATE watches SET {', '.join(sets)} WHERE id=?", vals)
            conn.commit()
    finally:
        conn.close()


def watch_get(wid: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM watches").description]
        d = dict(zip(cols, row))
        try:
            d["baseline"] = json.loads(d.pop("baseline_json") or "{}")
        except json.JSONDecodeError:
            d["baseline"] = {}
        return d
    finally:
        conn.close()


def watch_list() -> List[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM watches ORDER BY id DESC").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM watches").description]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            try:
                d["baseline"] = json.loads(d.pop("baseline_json") or "{}")
            except json.JSONDecodeError:
                d["baseline"] = {}
            d["changes_count"] = _watch_changes_count(conn, d["id"])
            out.append(d)
        return out
    finally:
        conn.close()


def _watch_changes_count(conn, wid: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM watch_events WHERE watch_id=? AND alert_level != 'IGNORE'",
        (wid,),
    ).fetchone()
    return row[0] if row else 0


def watch_delete(wid: int) -> None:
    """删除 Watch。History（runs/events）不删除（25 节：History 不删除）。"""
    conn = _conn()
    try:
        conn.execute("DELETE FROM watch_items WHERE watch_id=?", (wid,))
        conn.execute("DELETE FROM watches WHERE id=?", (wid,))
        conn.commit()
    finally:
        conn.close()


def watch_run_create(run: dict) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO watch_runs (watch_id, started_at, finished_at, mode, results_seen, "
            "new_items, events, action_alerts, duration_ms, errors_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run["watch_id"], run.get("started_at") or now_iso(), run.get("finished_at"),
             run.get("mode", "CHECK"), run.get("results_seen", 0), run.get("new_items", 0),
             run.get("events", 0), run.get("action_alerts", 0), run.get("duration_ms", 0),
             json.dumps(run.get("errors") or [], ensure_ascii=False)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def watch_item_upsert(item: dict) -> tuple:
    """按 (watch_id, fingerprint) upsert。返回 (item_id, is_new)。

    is_new = True 表示首次出现（NEW_LISTING / NEW_SOLD 的判定基础）。
    价格历史不覆盖旧值——变化通过 watch_events append-only 记录。
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, price, sale_type, last_seen FROM watch_items "
            "WHERE watch_id=? AND fingerprint=?",
            (item["watch_id"], item["fingerprint"]),
        ).fetchone()
        if row:
            item_id, old_price, old_sale, _ = row
            conn.execute(
                "UPDATE watch_items SET title=?, source_url=?, source_name=?, seller=?, "
                "price=?, currency=?, sale_type=?, year=?, attributes_json=?, "
                "source_tier=?, last_seen=?, opportunity_score=?, alert_level=? "
                "WHERE id=?",
                (item.get("title", ""), item.get("source_url", ""),
                 item.get("source_name", ""), item.get("seller", ""),
                 item.get("price"), item.get("currency", "EUR"),
                 item.get("sale_type", "UNKNOWN"), item.get("year"),
                 json.dumps(item.get("attributes") or {}, ensure_ascii=False),
                 item.get("source_tier", 0), item.get("last_seen") or now_iso(),
                 item.get("opportunity_score", 0), item.get("alert_level", "IGNORE"),
                 item_id),
            )
            conn.commit()
            return item_id, False, old_price, old_sale
        cur = conn.execute(
            "INSERT INTO watch_items (watch_id, fingerprint, title, source_url, source_name, "
            "seller, price, currency, sale_type, year, attributes_json, source_tier, "
            "first_seen, last_seen, opportunity_score, alert_level) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item["watch_id"], item["fingerprint"], item.get("title", ""),
             item.get("source_url", ""), item.get("source_name", ""),
             item.get("seller", ""), item.get("price"), item.get("currency", "EUR"),
             item.get("sale_type", "UNKNOWN"), item.get("year"),
             json.dumps(item.get("attributes") or {}, ensure_ascii=False),
             item.get("source_tier", 0), item.get("first_seen") or now_iso(),
             item.get("last_seen") or now_iso(), item.get("opportunity_score", 0),
             item.get("alert_level", "IGNORE")),
        )
        conn.commit()
        return cur.lastrowid, True, None, None
    finally:
        conn.close()


def watch_event_add(ev: dict) -> int:
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO watch_events (watch_id, item_id, event_type, old_value, new_value, "
            "opportunity_score, alert_level, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ev["watch_id"], ev.get("item_id"), ev["event_type"], ev.get("old_value", ""),
             ev.get("new_value", ""), ev.get("opportunity_score", 0),
             ev.get("alert_level", "IGNORE"), ev.get("created_at") or now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def watch_items(wid: int) -> List[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM watch_items WHERE watch_id=? ORDER BY opportunity_score DESC",
            (wid,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM watch_items").description]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            try:
                d["attributes"] = json.loads(d.pop("attributes_json") or "{}")
            except json.JSONDecodeError:
                d["attributes"] = {}
            out.append(d)
        return out
    finally:
        conn.close()


def watch_events(wid: int, limit: int = 100) -> List[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM watch_events WHERE watch_id=? ORDER BY id DESC LIMIT ?",
            (wid, limit),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM watch_events").description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def watch_events_recent(limit: int = 50) -> List[dict]:
    """跨监控读取最近变化，供首页今日机会展示。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT e.*, w.target AS watch_target, w.maker AS watch_maker, "
            "i.title AS item_title, i.source_url AS item_source_url, i.price AS item_price, "
            "i.currency AS item_currency, i.sale_type AS item_sale_type "
            "FROM watch_events e JOIN watches w ON w.id=e.watch_id "
            "LEFT JOIN watch_items i ON i.id=e.item_id "
            "WHERE e.alert_level != 'IGNORE' ORDER BY e.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT e.*, w.target AS watch_target, w.maker AS watch_maker, "
            "i.title AS item_title, i.source_url AS item_source_url, i.price AS item_price, "
            "i.currency AS item_currency, i.sale_type AS item_sale_type "
            "FROM watch_events e JOIN watches w ON w.id=e.watch_id LEFT JOIN watch_items i ON i.id=e.item_id").description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def watch_price_history(item_id: int) -> List[dict]:
    """item 的价格历史（append-only，供 23 节 price history 展示）。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT old_value, new_value, event_type, created_at FROM watch_events "
            "WHERE item_id=? AND event_type IN ('PRICE_DROP','PRICE_INCREASE','NEW_LISTING') "
            "ORDER BY id",
            (item_id,),
        ).fetchall()
        return [{"event": r[2], "old": r[0], "new": r[1], "at": r[3]} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- Decision Memory

def save_decision_memory(dm: dict) -> int:
    """保存/更新一条决策记录（report_id 唯一，不覆盖原始报告）。"""
    conn = _conn()
    try:
        cols = (
            "report_id, created_at, initial_decision, initial_offer, "
            "analyst_decision, analyst_opening, analyst_target_low, analyst_target_high, "
            "analyst_walkaway, final_decision, final_offer, final_transaction_price, "
            "transaction_status, decision_changed, notes"
        )
        placeholders = ", ".join("?" * 15)
        conn.execute(
            f"INSERT OR REPLACE INTO decision_memory ({cols}) VALUES ({placeholders})",
            (
                dm["report_id"], dm.get("created_at") or now_iso(),
                dm.get("initial_decision"), dm.get("initial_offer"),
                dm["analyst_decision"], dm.get("analyst_opening"),
                dm.get("analyst_target_low"), dm.get("analyst_target_high"),
                dm.get("analyst_walkaway"), dm.get("final_decision"),
                dm.get("final_offer"), dm.get("final_transaction_price"),
                dm.get("transaction_status"), dm.get("decision_changed"),
                dm.get("notes") or "",
            ),
        )
        conn.commit()
        return conn.execute("SELECT id FROM decision_memory WHERE report_id=?",
                            (dm["report_id"],)).fetchone()[0]
    finally:
        conn.close()


def get_decision_memory(report_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM decision_memory WHERE report_id=?", (report_id,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM decision_memory").description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def history() -> List[dict]:
    """历史列表：decision_memory JOIN reports，按报告时间倒序。"""
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT dm.report_id, dm.created_at, dm.initial_decision, dm.initial_offer,
                   dm.analyst_decision, dm.final_decision, dm.final_offer,
                   dm.final_transaction_price, dm.transaction_status, dm.decision_changed,
                   r.input_json, r.created_at AS report_created_at
            FROM decision_memory dm
            LEFT JOIN reports r ON r.id = dm.report_id
            ORDER BY dm.created_at DESC
            """
        ).fetchall()
        out = []
        for row in rows:
            try:
                inp = json.loads(row[10] or "{}")
            except json.JSONDecodeError:
                inp = {}
            out.append({
                "report_id": row[0],
                "created_at": row[11] or row[1],
                "object": f"{inp.get('artist', '')} {inp.get('artwork', '')}".strip(),
                "asking": inp.get("asking_price"),
                "currency": inp.get("currency", "EUR"),
                "analyst_decision": row[4],
                "initial_decision": row[2],
                "initial_offer": row[3],
                "final_decision": row[5],
                "final_offer": row[6],
                "final_transaction_price": row[7],
                "transaction_status": row[8],
                "decision_changed": row[9],
            })
        return out
    finally:
        conn.close()


def history_stats() -> dict:
    """历史统计（任务书第十一节）。decision_changed 是衡量系统价值的核心指标。"""
    rows = history()
    total = len(rows)
    completed = sum(1 for r in rows if r["final_decision"])
    changed = sum(1 for r in rows if r["decision_changed"] == "YES")
    bought = sum(1 for r in rows if r["transaction_status"] == "BOUGHT")
    passed = sum(1 for r in rows if r["transaction_status"] == "PASSED")
    discounts = [
        (r["initial_offer"] - r["final_offer"]) / r["initial_offer"] * 100
        for r in rows if r["initial_offer"] and r["final_offer"] and r["initial_offer"] > 0
    ]
    avg_discount = round(sum(discounts) / len(discounts), 1) if discounts else None
    change_rate = round(changed / completed * 100) if completed else None
    return {
        "total_opportunities": total,
        "decisions_completed": completed,
        "decision_changed": changed,
        "bought": bought,
        "passed": passed,
        "avg_negotiated_discount": avg_discount,
        "decision_change_rate": change_rate,
    }

def history_delete(report_ids: List[int]) -> int:
    """Delete selected decision ledger rows; reports themselves remain intact."""
    ids = [int(x) for x in report_ids if str(x).isdigit()]
    if not ids: return 0
    conn = _conn()
    try:
        q = ','.join('?' for _ in ids)
        cur = conn.execute(f"DELETE FROM decision_memory WHERE report_id IN ({q})", ids)
        conn.commit(); return cur.rowcount
    finally: conn.close()


# ---------------------------------------------------------------- LLM 精读缓存

def llm_cache_get(provider: str, model: str, url: str, text_hash: str) -> Optional[dict]:
    """按 (provider, model, url, 裁剪后正文 hash) 取缓存。命中即不再花钱调用 LLM。"""
    if not (provider and model and url):
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT result_json FROM llm_cache WHERE provider=? AND model=? AND url=? AND text_hash=?",
            (provider, model, url, text_hash),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None
    finally:
        conn.close()


def llm_cache_put(provider: str, model: str, url: str, text_hash: str, result: dict) -> None:
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (provider, model, url, text_hash, result_json, retrieved_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (provider, model, url, text_hash,
                 json.dumps(result, ensure_ascii=False), now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # 缓存失败绝不阻塞分析
        pass


def save_report(report: Report) -> Report:
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO reports (created_at, input_json, report_json) VALUES (?, ?, ?)",
            (report.created_at, report.input.model_dump_json(), report.model_dump_json(exclude={"id"})),
        )
        report.id = cur.lastrowid
        conn.commit()
        return report
    finally:
        conn.close()


def list_reports() -> List[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, created_at, input_json FROM reports ORDER BY id DESC LIMIT 100"
        ).fetchall()
        out = []
        for rid, created_at, input_json in rows:
            inp = json.loads(input_json)
            out.append({
                "id": rid,
                "created_at": created_at,
                "artist": inp.get("artist", ""),
                "artwork": inp.get("artwork", ""),
                "asking_price": inp.get("asking_price"),
                "currency": inp.get("currency", "EUR"),
            })
        return out
    finally:
        conn.close()


def get_report(rid: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT report_json FROM reports WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        data["id"] = rid
        return data
    finally:
        conn.close()


# ================================================================ 0.6 Scout

def _scout_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scout_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            categories_json TEXT NOT NULL DEFAULT '[]',
            budget_low REAL, budget_high REAL,
            currency TEXT DEFAULT 'EUR', markets TEXT DEFAULT 'GLOBAL',
            opportunity_types_json TEXT DEFAULT '[]',
            interests TEXT DEFAULT '', avoid TEXT DEFAULT '',
            seeds_artists TEXT DEFAULT '', seeds_models TEXT DEFAULT '',
            seeds_keywords TEXT DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scout_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL, finished_at TEXT,
            results_raw INTEGER DEFAULT 0, duplicates_removed INTEGER DEFAULT 0,
            known_removed INTEGER DEFAULT 0, profile_removed INTEGER DEFAULT 0,
            source_removed INTEGER DEFAULT 0, weak_removed INTEGER DEFAULT 0,
            candidates INTEGER DEFAULT 0, top_shown INTEGER DEFAULT 0,
            llm_calls INTEGER DEFAULT 0, llm_tokens INTEGER DEFAULT 0,
            estimated_cost_rmb REAL DEFAULT 0, duration_ms INTEGER DEFAULT 0,
            errors_json TEXT DEFAULT '[]'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scout_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER, fingerprint TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'ART', maker TEXT DEFAULT '', object TEXT DEFAULT '',
            asking_price REAL, currency TEXT DEFAULT 'EUR',
            source_url TEXT DEFAULT '', source_name TEXT DEFAULT '',
            source_tier INTEGER DEFAULT 0, sale_type TEXT DEFAULT 'UNKNOWN',
            profile_match INTEGER DEFAULT 0, price_anomaly INTEGER DEFAULT 0,
            scarcity INTEGER DEFAULT 0, source_quality INTEGER DEFAULT 0,
            freshness INTEGER DEFAULT 0, evidence_strength INTEGER DEFAULT 0,
            scout_score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'NEW', notes TEXT DEFAULT '',
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scout_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            feedback TEXT NOT NULL, why_not TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # ---- 0.6.2 列迁移：旧库补新列（CREATE TABLE IF NOT EXISTS 不会给旧表加列）----
    for tbl, col, ddl in (
        ("scout_profiles", "structured_interest_json", "TEXT DEFAULT '{}'"),
        ("scout_profiles", "generated_seeds_json", "TEXT DEFAULT '{}'"),
        ("scout_profiles", "parser_version", "TEXT DEFAULT ''"),
        ("scout_profiles", "interests_hash", "TEXT DEFAULT ''"),
        ("scout_runs", "status", "TEXT DEFAULT 'COMPLETED'"),
        ("scout_runs", "parse_info_json", "TEXT DEFAULT '{}'"),
        ("scout_runs", "search_plan_json", "TEXT DEFAULT '[]'"),
        ("scout_runs", "brief_json", "TEXT DEFAULT '{}'"),
        ("scout_runs", "near_misses_json", "TEXT DEFAULT '[]'"),
    ):
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
        except Exception:
            pass  # 列已存在
    conn.commit()


def scout_profile_get() -> Optional[dict]:
    conn = _conn()
    try:
        _scout_tables(conn)
        row = conn.execute("SELECT * FROM scout_profiles ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_profiles").description]
        d = dict(zip(cols, row))
        try:
            d["categories"] = json.loads(d.pop("categories_json") or "[]")
        except json.JSONDecodeError:
            d["categories"] = []
        try:
            d["opportunity_types"] = json.loads(d.pop("opportunity_types_json") or "[]")
        except json.JSONDecodeError:
            d["opportunity_types"] = []
        try:
            d["structured_interest"] = json.loads(d.pop("structured_interest_json") or "{}")
        except json.JSONDecodeError:
            d["structured_interest"] = {}
        try:
            d["generated_seeds"] = json.loads(d.pop("generated_seeds_json") or "{}")
        except json.JSONDecodeError:
            d["generated_seeds"] = {}
        return d
    finally:
        conn.close()


def scout_profile_save(p: dict) -> int:
    conn = _conn()
    try:
        _scout_tables(conn)
        existing = conn.execute("SELECT id FROM scout_profiles ORDER BY id DESC LIMIT 1").fetchone()
        categories = json.dumps(p.get("categories") or [], ensure_ascii=False)
        opp_types = json.dumps(p.get("opportunity_types") or [], ensure_ascii=False)
        structured = json.dumps(p.get("structured_interest") or {}, ensure_ascii=False)
        seeds = json.dumps(p.get("generated_seeds") or {}, ensure_ascii=False)
        if existing:
            conn.execute(
                "UPDATE scout_profiles SET categories_json=?, budget_low=?, budget_high=?, "
                "currency=?, markets=?, opportunity_types_json=?, interests=?, avoid=?, "
                "seeds_artists=?, seeds_models=?, seeds_keywords=?, updated_at=?, "
                "structured_interest_json=?, generated_seeds_json=?, parser_version=?, "
                "interests_hash=? WHERE id=?",
                (categories, p.get("budget_low"), p.get("budget_high"),
                 p.get("currency", "EUR"), p.get("markets", "GLOBAL"), opp_types,
                 p.get("interests", ""), p.get("avoid", ""),
                 p.get("seeds_artists", ""), p.get("seeds_models", ""),
                 p.get("seeds_keywords", ""), now_iso(),
                 structured, seeds, p.get("parser_version", ""),
                 p.get("interests_hash", ""), existing[0]),
            )
            conn.commit()
            return existing[0]
        cur = conn.execute(
            "INSERT INTO scout_profiles (categories_json, budget_low, budget_high, currency, "
            "markets, opportunity_types_json, interests, avoid, seeds_artists, seeds_models, "
            "seeds_keywords, created_at, updated_at, structured_interest_json, "
            "generated_seeds_json, parser_version, interests_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (categories, p.get("budget_low"), p.get("budget_high"),
             p.get("currency", "EUR"), p.get("markets", "GLOBAL"), opp_types,
             p.get("interests", ""), p.get("avoid", ""),
             p.get("seeds_artists", ""), p.get("seeds_models", ""),
             p.get("seeds_keywords", ""), now_iso(), now_iso(),
             structured, seeds, p.get("parser_version", ""),
             p.get("interests_hash", "")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def scout_run_create(run: dict) -> int:
    conn = _conn()
    try:
        _scout_tables(conn)
        cur = conn.execute(
            "INSERT INTO scout_runs (started_at, finished_at, results_raw, duplicates_removed, "
            "known_removed, profile_removed, source_removed, weak_removed, candidates, top_shown, "
            "llm_calls, llm_tokens, estimated_cost_rmb, duration_ms, errors_json, "
            "status, parse_info_json, search_plan_json, brief_json, near_misses_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run.get("started_at") or now_iso(), run.get("finished_at"),
             run.get("results_raw", 0), run.get("duplicates_removed", 0),
             run.get("known_removed", 0), run.get("profile_removed", 0),
             run.get("source_removed", 0), run.get("weak_removed", 0),
             run.get("candidates", 0), run.get("top_shown", 0),
             run.get("llm_calls", 0), run.get("llm_tokens", 0),
             run.get("estimated_cost_rmb", 0.0), run.get("duration_ms", 0),
             json.dumps(run.get("errors") or [], ensure_ascii=False),
             run.get("status") or "COMPLETED",
             json.dumps(run.get("parse_info") or {}, ensure_ascii=False),
             json.dumps(run.get("search_plan") or [], ensure_ascii=False),
             json.dumps(run.get("brief") or {}, ensure_ascii=False),
             json.dumps(run.get("near_misses") or [], ensure_ascii=False)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def scout_candidate_upsert(c: dict) -> int:
    """按 fingerprint upsert。返回 candidate_id。"""
    conn = _conn()
    try:
        _scout_tables(conn)
        row = conn.execute(
            "SELECT id, asking_price, status FROM scout_candidates WHERE fingerprint=?",
            (c["fingerprint"],),
        ).fetchone()
        if row:
            cid, old_price, status = row
            # 曾被 NOT_INTERESTED/WRONG_MATCH 且价格降 ≥20% → MATERIAL CHANGE（14 节）
            new_status = c.get("status", status)
            if status in ("NOT_INTERESTED", "WRONG_MATCH") and old_price and c.get("asking_price"):
                if c["asking_price"] <= old_price * 0.8:
                    new_status = "MATERIAL_CHANGE"
            conn.execute(
                "UPDATE scout_candidates SET run_id=?, category=?, maker=?, object=?, "
                "asking_price=?, currency=?, source_url=?, source_name=?, source_tier=?, "
                "sale_type=?, profile_match=?, price_anomaly=?, scarcity=?, source_quality=?, "
                "freshness=?, evidence_strength=?, scout_score=?, status=?, notes=?, last_seen=? "
                "WHERE id=?",
                (c.get("run_id"), c.get("category", "ART"), c.get("maker", ""),
                 c.get("object", ""), c.get("asking_price"), c.get("currency", "EUR"),
                 c.get("source_url", ""), c.get("source_name", ""), c.get("source_tier", 0),
                 c.get("sale_type", "UNKNOWN"), c.get("profile_match", 0),
                 c.get("price_anomaly", 0), c.get("scarcity", 0), c.get("source_quality", 0),
                 c.get("freshness", 0), c.get("evidence_strength", 0),
                 c.get("scout_score", 0), new_status, c.get("notes", ""), now_iso(), cid),
            )
            conn.commit()
            return cid
        cur = conn.execute(
            "INSERT INTO scout_candidates (run_id, fingerprint, category, maker, object, "
            "asking_price, currency, source_url, source_name, source_tier, sale_type, "
            "profile_match, price_anomaly, scarcity, source_quality, freshness, "
            "evidence_strength, scout_score, status, notes, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c.get("run_id"), c["fingerprint"], c.get("category", "ART"),
             c.get("maker", ""), c.get("object", ""), c.get("asking_price"),
             c.get("currency", "EUR"), c.get("source_url", ""), c.get("source_name", ""),
             c.get("source_tier", 0), c.get("sale_type", "UNKNOWN"),
             c.get("profile_match", 0), c.get("price_anomaly", 0), c.get("scarcity", 0),
             c.get("source_quality", 0), c.get("freshness", 0), c.get("evidence_strength", 0),
             c.get("scout_score", 0), c.get("status", "NEW"), c.get("notes", ""),
             now_iso(), now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def scout_candidates(limit: int = 50, min_score: int = 0) -> List[dict]:
    conn = _conn()
    try:
        _scout_tables(conn)
        rows = conn.execute(
            "SELECT * FROM scout_candidates WHERE scout_score >= ? "
            "ORDER BY scout_score DESC LIMIT ?", (min_score, limit),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_candidates").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def scout_candidate_get(cid: int) -> Optional[dict]:
    conn = _conn()
    try:
        _scout_tables(conn)
        row = conn.execute("SELECT * FROM scout_candidates WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_candidates").description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def scout_candidate_feedback(cid: int, feedback: str, why_not: str = "") -> None:
    conn = _conn()
    try:
        _scout_tables(conn)
        conn.execute(
            "INSERT INTO scout_feedback (candidate_id, feedback, why_not, created_at) "
            "VALUES (?, ?, ?, ?)", (cid, feedback, why_not, now_iso()),
        )
        conn.execute(
            "UPDATE scout_candidates SET status=? WHERE id=?",
            (feedback if feedback != "" else "SHOWN", cid),
        )
        conn.commit()
    finally:
        conn.close()


def scout_feedback_of(cid: int) -> List[dict]:
    conn = _conn()
    try:
        _scout_tables(conn)
        rows = conn.execute(
            "SELECT * FROM scout_feedback WHERE candidate_id=? ORDER BY id DESC", (cid,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_feedback").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def scout_latest_run() -> Optional[dict]:
    conn = _conn()
    try:
        _scout_tables(conn)
        row = conn.execute("SELECT * FROM scout_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_runs").description]
        d = dict(zip(cols, row))
        try:
            d["errors"] = json.loads(d.pop("errors_json") or "[]")
        except json.JSONDecodeError:
            d["errors"] = []
        try:
            d["parse_info"] = json.loads(d.pop("parse_info_json") or "{}")
        except json.JSONDecodeError:
            d["parse_info"] = {}
        try:
            d["search_plan"] = json.loads(d.pop("search_plan_json") or "[]")
        except json.JSONDecodeError:
            d["search_plan"] = []
        try:
            d["brief"] = json.loads(d.pop("brief_json") or "{}")
        except json.JSONDecodeError:
            d["brief"] = {}
        try:
            d["near_misses"] = json.loads(d.pop("near_misses_json") or "[]")
        except json.JSONDecodeError:
            d["near_misses"] = []
        return d
    finally:
        conn.close()


def scout_runs_list(limit: int = 20) -> List[dict]:
    """Scout 巡视历史（0.6.3 P1）：HISTORY 页回看每次巡视。"""
    conn = _conn()
    try:
        _scout_tables(conn)
        rows = conn.execute(
            "SELECT * FROM scout_runs ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_runs").description]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            try:
                d["parse_info"] = json.loads(d.pop("parse_info_json") or "{}")
            except json.JSONDecodeError:
                d["parse_info"] = {}
            try:
                d["brief"] = json.loads(d.pop("brief_json") or "{}")
            except json.JSONDecodeError:
                d["brief"] = {}
            out.append(d)
        return out
    finally:
        conn.close()

def scout_runs_delete(run_ids: List[int]) -> int:
    ids = [int(x) for x in run_ids if str(x).isdigit()]
    if not ids: return 0
    conn = _conn()
    try:
        q = ','.join('?' for _ in ids)
        cur = conn.execute(f"DELETE FROM scout_runs WHERE id IN ({q})", ids)
        conn.commit(); return cur.rowcount
    finally: conn.close()


def scout_known_fingerprints() -> set:
    """Scout 不重复劳动（17 节）：已在 Watch / 已分析 / 已拒绝的对象集合。"""
    conn = _conn()
    fps = set()
    try:
        _scout_tables(conn)
        for fp in conn.execute("SELECT fingerprint FROM watch_items").fetchall():
            fps.add(fp[0])
        for fp in conn.execute("SELECT fingerprint FROM scout_candidates "
                               "WHERE status IN ('NOT_INTERESTED','WRONG_MATCH','ALREADY_KNOW')"
                               ).fetchall():
            fps.add(fp[0])
    finally:
        conn.close()
    return fps


def scout_candidates_by_run(run_id: int) -> List[dict]:
    """只取指定 run 的候选（today 接口用——避免把历史候选当"今天"展示）。"""
    conn = _conn()
    try:
        _scout_tables(conn)
        rows = conn.execute(
            "SELECT * FROM scout_candidates WHERE run_id=? ORDER BY scout_score DESC",
            (run_id,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM scout_candidates").description]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()
