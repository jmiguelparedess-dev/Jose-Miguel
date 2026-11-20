"""Línea de comandos (útil para programar con cron/Programador de tareas de Windows).

  python -m serrate_bi.cli semanal            # actualización semanal completa
  python -m serrate_bi.cli descubrir --ciudades "Santa Cruz,La Paz"
  python -m serrate_bi.cli investigar 12      # INVESTIGAR MÁS sobre la empresa 12
  python -m serrate_bi.cli exportar salida.xlsx
"""
import argparse
import json

from .db import connect, default_db_path, init_db
from .jobs import JobRunner


def main(argv=None):
    p = argparse.ArgumentParser(prog="serrate_bi")
    p.add_argument("--db", default=default_db_path())
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("semanal"); s.add_argument("--sin-descubrir", action="store_true")
    d = sub.add_parser("descubrir"); d.add_argument("--ciudades", default="Santa Cruz,La Paz,Cochabamba")
    d.add_argument("--consulta"); d.add_argument("--max", type=int, default=10)
    i = sub.add_parser("investigar"); i.add_argument("company_id", type=int)
    e = sub.add_parser("exportar"); e.add_argument("archivo")
    a = p.parse_args(argv)
    init_db(a.db)
    runner = JobRunner(a.db)
    if a.cmd == "exportar":
        from .export import build_workbook
        conn = connect(a.db)
        with open(a.archivo, "wb") as fh:
            fh.write(build_workbook(conn).read())
        print(f"Exportado: {a.archivo}")
        return
    if a.cmd == "semanal":
        run_id = runner.submit("semanal", params={"discover_new": not a.sin_descubrir})
    elif a.cmd == "descubrir":
        run_id = runner.submit("descubrir", params={"cities": [c.strip() for c in a.ciudades.split(",")],
                                                    "extra_query": a.consulta, "max_new": a.max})
    else:
        run_id = runner.submit("investigar", a.company_id)
    runner.run_now(run_id)
    conn = connect(a.db)
    r = conn.execute("SELECT status, summary, log FROM research_runs WHERE id=?", (run_id,)).fetchone()
    print(r["log"])
    print(json.dumps({"estado": r["status"], "resumen": r["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
