"""Ejecución en segundo plano de investigaciones y programador semanal (sin dependencias externas)."""
import json
import queue
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import repo
from .db import connect, get_setting, now_iso
from .research import pipeline


class JobRunner:
    def __init__(self, db_path):
        self.db_path = db_path
        self.q = queue.Queue()
        self._started = False
        self._lock = threading.Lock()

    def start(self, scheduler=True):
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._worker, name="serrate-jobs", daemon=True).start()
        if scheduler:
            threading.Thread(target=self._scheduler, name="serrate-weekly", daemon=True).start()

    def submit(self, kind, company_id=None, params=None):
        conn = connect(self.db_path)
        try:
            # Evita encolar dos veces el mismo trabajo.
            dup = conn.execute("SELECT id FROM research_runs WHERE kind=? AND COALESCE(company_id,0)=? "
                               "AND status IN ('en cola','en curso')", (kind, company_id or 0)).fetchone()
            if dup:
                return dup["id"]
            run_id = repo.create_run(conn, kind, company_id, params)
        finally:
            conn.close()
        self.q.put(run_id)
        return run_id

    def run_now(self, run_id):
        """Ejecución síncrona (CLI y pruebas)."""
        self._execute(run_id)

    def _worker(self):
        while True:
            run_id = self.q.get()
            try:
                self._execute(run_id)
            finally:
                self.q.task_done()

    def _execute(self, run_id):
        conn = connect(self.db_path)
        try:
            run = conn.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
            params = json.loads(run["params"] or "{}")
            conn.execute("UPDATE research_runs SET status='en curso', started_at=? WHERE id=?", (now_iso(), run_id))
            try:
                if run["kind"] == "investigar":
                    stats = pipeline.investigate_company(conn, run["company_id"], run_id)
                elif run["kind"] == "descubrir":
                    stats = pipeline.discover(conn, run_id, params.get("cities"), params.get("sectors"),
                                              params.get("extra_query"), params.get("investigate_new", True),
                                              int(params.get("max_new", 10)))
                elif run["kind"] == "semanal":
                    stats = pipeline.weekly_update(conn, run_id, params.get("discover_new", True))
                else:
                    raise ValueError(f"Tipo de trabajo desconocido: {run['kind']}")
                conn.execute("UPDATE research_runs SET status='terminado', finished_at=?, summary=?, stats_json=? "
                             "WHERE id=?", (now_iso(), pipeline.stats_summary(stats), pipeline.dumps(stats), run_id))
            except Exception as e:
                repo.run_log(conn, run_id, "ERROR: " + "".join(traceback.format_exception_only(type(e), e)).strip())
                conn.execute("UPDATE research_runs SET status='error', finished_at=?, summary=? WHERE id=?",
                             (now_iso(), f"Error: {e}", run_id))
        finally:
            conn.close()

    def _scheduler(self):
        while True:
            try:
                self.check_weekly()
            except Exception:
                traceback.print_exc()
            time.sleep(1800)

    def check_weekly(self):
        conn = connect(self.db_path)
        try:
            if get_setting(conn, "weekly_enabled", "1") != "1":
                return None
            last = get_setting(conn, "last_weekly_run")
            due = True
            if last:
                due = datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(days=7)
            else:
                # Primera ejecución: espera a que haya empresas registradas.
                due = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"] > 0
        finally:
            conn.close()
        if due:
            return self.submit("semanal", params={"discover_new": True})
        return None
