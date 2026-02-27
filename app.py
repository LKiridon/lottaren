# ============================================================
# Fil: app.py
# ============================================================
"""Startfil.

Install:
  pip install nicegui pandas openpyxl

Kör:
  python app.py
"""

from nicegui import ui

import oldcore.core as core
import ui_user
import ui_admin
import os
import time

# init DB on startup
core.init_db()

# register pages
ui_user.register_user_pages()
ui_admin.register_admin_pages()

# NiceGUI kräver storage_secret för app.storage.user (sessionslagring)
# Lokalt system: hårdkodat.
STORAGE_SECRET = 'lokal-demo-hemlis-12345'

ui.run(
    title='Artikelutdelning',
    port=int(os.environ.get('PORT', '8080')),
    reload=True,
    storage_secret=STORAGE_SECRET,
)
#