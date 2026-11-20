"""Inicia la aplicación web: python run.py  (http://127.0.0.1:5000)"""
import os

from serrate_bi import create_app

app = create_app()

if __name__ == "__main__":
    # use_reloader=False: evita duplicar el programador semanal en segundo plano.
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=False, use_reloader=False, threaded=True)
