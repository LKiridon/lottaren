
# language: python
"""ui_admin.py

Admin-sidor:
- /admin/login
- /admin

Innehåller:
- knappar för att rensa (allt / bara resultat)
- uppladdning av artiklar via Excel/CSV
- översikt
- se röster per deltagare
- ta bort registrerad deltagare
- köra dragning och se resultat
"""

from __future__ import annotations

import time
from typing import Dict, List

from nicegui import ui, app

import oldcore.core as core


def require_admin() -> None:
    if not app.storage.user.get('is_admin', False):
        ui.navigate.to('/admin/login')


def register_admin_pages() -> None:

    @ui.page('/admin/login')
    def admin_login():
        ui.colors(primary='#2563eb')
        ui.markdown('# Admin\nLogga in för att ladda upp artiklar och köra dragning.')
        with ui.card().classes('w-full max-w-md'):
            pw = ui.input('Lösenord', password=True, password_toggle_button=True)

            def do_login():
                if (pw.value or '') == core.ADMIN_PASSWORD:
                    app.storage.user['is_admin'] = True
                    ui.navigate.to('/admin')
                else:
                    ui.notify('Fel lösenord', color='negative')

            ui.button('Logga in', on_click=do_login).classes('w-full')

    @ui.page('/admin')
    def admin_page():
        ui.colors(primary='#2563eb')
        require_admin()
        ui.markdown('# Admin')

        with ui.row().classes('gap-3'):
            ui.button(
                'Logga ut',
                on_click=lambda: (app.storage.user.pop('is_admin', None), ui.navigate.to('/')),
            )

        # 1) Controls
        with ui.card().classes('w-full'):
            ui.label('1) Kontroller: rensa och ladda upp').classes('text-lg font-medium')

            with ui.row().classes('gap-3 items-center'):
                ui.button(
                    'Rensa allt (artiklar + röster + resultat)',
                    on_click=lambda: (
                        core.clear_items_and_votes_and_allocations(),
                        ui.notify('Allt rensat', color='warning'),
                        ui.navigate.to('/admin'),
                    ),
                )
                ui.button(
                    'Rensa ENDAST resultat (dragning)',
                    on_click=lambda: (
                        core.clear_allocations(),
                        ui.notify('Resultat rensat', color='warning'),
                        ui.navigate.to('/admin'),
                    ),
                )

            ui.separator()
            ui.label('Ladda upp artiklar (Excel/CSV)').classes('text-md font-semibold')
            ui.markdown('Förväntade kolumner: `name`, `category` (valfri), `quantity` (valfri).')

            status = ui.label()

            async def handle_upload(e):
                try:
                    # 1) Hämta filnamn
                    name = getattr(e, 'name', None)
                    if not name and hasattr(e, 'file'):
                        name = getattr(e.file, 'name', None)
                    if not name:
                        raise ValueError('Kan inte läsa filnamn från upload-eventet')

                    # 2) Hämta bytes-innehåll (stöd för flera NiceGUI-versioner)
                    data = None

                    # Äldre/stabilt exempel: e.content.read() förekommer ofta i exempel <inref id="460adb18"/>
                    if hasattr(e, 'content') and e.content is not None:
                        data = e.content.read()

                    # Nyare format: e.file = SmallFileUpload(..., _data=b'...') syns i diskussioner <inref id="008e2517"/>
                    elif hasattr(e, 'file') and e.file is not None:
                        f = e.file
                        if hasattr(f, 'read') and callable(f.read):
                            data = await f.read()
                        elif hasattr(f, 'content'):
                            data = f.content  # kan vara bytes
                        elif hasattr(f, '_data'):
                            data = f._data
                        else:
                            raise ValueError('Kan inte läsa filinnehåll från e.file')

                    else:
                        raise ValueError('Upload-eventet saknar både content och file')

                    if not isinstance(data, (bytes, bytearray)):
                        raise ValueError(f'Fel datatyp på uppladdad fil: {type(data)}')

                    # 3) Parsea + importera
                    df = core.parse_items_file(bytes(data), name)
                    core.clear_items_and_votes_and_allocations()

                    params = [
                        (str(r['name']), str(r.get('category', '')), int(r.get('quantity', 1)))
                        for _, r in df.iterrows()
                    ]
                    core.exec_many('INSERT INTO items(name, category, quantity) VALUES(?, ?, ?)', params)

                    ui.notify(f'Artiklar importerade: {len(df)} rader. Röster/resultat rensades.', color='positive')
                    ui.navigate.to('/admin')

                except Exception as ex:
                    ui.notify(str(ex), color='negative')

            ui.upload(on_upload=handle_upload, auto_upload=True).props('accept=.xlsx,.xls,.csv')

        # 2) Overview + participant management
        with ui.card().classes('w-full'):
            ui.label('2) Översikt och deltagare').classes('text-lg font-medium')
            items = core.list_items()
            parts = core.list_participants()

            ui.label(f'Artiklar: {len(items)}')
            ui.label(f'Deltagare: {len(parts)}')

            submitted = 0
            for p in parts:
                pid = int(p['id'])
                if core.participant_has_submitted(pid) and core.vote_sum_for_participant(pid) == core.POINT_BUDGET:
                    submitted += 1
            ui.label(f'Inlämnade (summa = {core.POINT_BUDGET}): {submitted}/{len(parts)}')

            ui.separator()
            ui.label('Ta bort registrerad deltagare').classes('text-md font-semibold')
            ui.markdown('Tar bort deltagaren och deras röster (och ev. resultat de vunnit).')

            name_to_id = {str(p['name']): int(p['id']) for p in parts}
            del_sel = ui.select(
                label='Välj deltagare att ta bort',
                options=list(name_to_id.keys()),
                value=None,
            ).classes('w-full max-w-md')

            def do_delete():
                if not del_sel.value:
                    ui.notify('Välj en deltagare', color='negative')
                    return
                pid2 = name_to_id.get(str(del_sel.value))
                if pid2 is None:
                    ui.notify('Hittar inte deltagaren', color='negative')
                    return
                core.delete_participant(pid2)
                ui.notify(f'Tog bort: {del_sel.value}', color='warning')
                ui.navigate.to('/admin')

            ui.button('Ta bort deltagare', on_click=do_delete).props('color=negative')

        # 3) Artiklar + poängsumma – live
        with ui.card().classes('w-full'):
            ui.label('3) Artiklar (totala poäng)').classes('text-lg font-medium')
            ui.markdown('Uppdateras automatiskt. Visar hela artikel-listan samt totalpoäng (summa av alla deltagares poäng).')

            @ui.refreshable
            def items_view() -> None:
                items = core.list_items_with_point_totals()
                item_rows = [
                    {
                        'Kategori': r['category'] or '',
                        'Artikel': r['name'],
                        'Antal': int(r['quantity']),
                        'Totalpoäng': int(r['total_points']),
                        'Antal röstande': int(r['voters']),
                    }
                    for r in items
                ]

                ui.table(
                    columns=[
                        {'name': 'Kategori', 'label': 'Kategori', 'field': 'Kategori', 'sortable': True},
                        {'name': 'Artikel', 'label': 'Artikel', 'field': 'Artikel', 'sortable': True},
                        {'name': 'Antal', 'label': 'Antal', 'field': 'Antal', 'sortable': True},
                        {'name': 'Totalpoäng', 'label': 'Totalpoäng', 'field': 'Totalpoäng', 'sortable': True},
                        {'name': 'Antal röstande', 'label': 'Antal röstande', 'field': 'Antal röstande', 'sortable': True},
                    ],
                    rows=item_rows,
                    row_key='Artikel',
                ).classes('w-full')

            items_view()
            ui.timer(1.0, items_view.refresh)

        # 4) Röster (översikt) – live-uppdatering via ui.refreshable (NiceGUI 3.8)
        with ui.card().classes('w-full'):
            ui.label('4) Röster (översikt)').classes('text-lg font-medium')
            ui.markdown('Uppdateras automatiskt. Välj en deltagare för att se deras poäng per artikel.')

            # Behåll urval mellan uppdateringar
            state = {'selected_name': None}

            @ui.refreshable
            def votes_view() -> None:
                parts = core.list_participants()
                names = [str(p['name']) for p in parts]
                name_by_id = {int(p['id']): str(p['name']) for p in parts}

                # Välj default om inget valt / valt namn finns inte längre
                if not state['selected_name'] or state['selected_name'] not in names:
                    state['selected_name'] = names[0] if names else None

                rows = []
                for p in parts:
                    pid = int(p['id'])
                    s = core.vote_sum_for_participant(pid)
                    rows.append({
                        'Deltagare': str(p['name']),
                        'Summa': s,
                        'Inlämnad': 'Ja' if s == core.POINT_BUDGET else 'Nej',
                    })

                ui.table(
                    columns=[
                        {'name': 'Deltagare', 'label': 'Deltagare', 'field': 'Deltagare', 'sortable': True},
                        {'name': 'Summa', 'label': 'Summa', 'field': 'Summa', 'sortable': True},
                        {'name': 'Inlämnad', 'label': 'Inlämnad', 'field': 'Inlämnad', 'sortable': True},
                    ],
                    rows=rows,
                    row_key='Deltagare',
                ).classes('w-full')

                ui.separator()
                sel = ui.select(
                    label='Visa röster för deltagare',
                    options=names,
                    value=state['selected_name'],
                ).classes('w-full max-w-md')

                details = ui.column().classes('w-full')

                def render_details() -> None:
                    details.clear()
                    selected_name = sel.value
                    state['selected_name'] = selected_name
                    if not selected_name:
                        return

                    pid = None
                    for k, v in name_by_id.items():
                        if v == selected_name:
                            pid = k
                            break
                    if pid is None:
                        return

                    votes = core.get_votes_detailed(pid)
                    dv = [
                        {'Kategori': r['category'] or '', 'Artikel': r['item_name'], 'Poäng': int(r['points'])}
                        for r in votes
                        if int(r['points']) > 0
                    ]

                    with details:
                        ui.label(f'Röster för: {selected_name}').classes('text-md font-semibold')
                        ui.label(f'Summa: {core.vote_sum_for_participant(pid)}/{core.POINT_BUDGET}')
                        if not dv:
                            ui.label('Inga poäng satta (eller allt är 0).')
                        else:
                            ui.table(
                                columns=[
                                    {'name': 'Kategori', 'label': 'Kategori', 'field': 'Kategori', 'sortable': True},
                                    {'name': 'Artikel', 'label': 'Artikel', 'field': 'Artikel', 'sortable': True},
                                    {'name': 'Poäng', 'label': 'Poäng', 'field': 'Poäng', 'sortable': True},
                                ],
                                rows=dv,
                                row_key='Artikel',
                            ).classes('w-full')

                sel.on('update:model-value', lambda e: render_details())
                render_details()

            votes_view()
            ui.timer(1.0, votes_view.refresh)

        # 5) Kör dragning
        with ui.card().classes('w-full'):
            ui.label('5) Kör dragning').classes('text-lg font-medium')
            seed_in = ui.input('Seed (valfri men rekommenderas)', value=str(int(time.time())))

            def do_draw():
                try:
                    seed = seed_in.value or str(int(time.time()))
                    res = core.run_draw(seed)
                    ui.notify(f'Dragning klar (seed={res.seed})', color='positive')
                    ui.navigate.to('/admin')
                except Exception as ex:
                    ui.notify(str(ex), color='negative')

            ui.button('Dra vinnare', on_click=do_draw).classes('w-full')

        # 6) Resultat
        with ui.card().classes('w-full'):
            ui.label('6) Resultat').classes('text-lg font-medium')
            run_id = core.get_latest_run_id()
            if not run_id:
                ui.label('Ingen dragning gjord ännu.')
                return

            rows = core.get_results(run_id)

            ui.label('Per artikel').classes('text-md font-semibold')
            per_item = []
            for r in rows:
                per_item.append({'Kategori': r['category'] or '', 'Artikel': r['item_name'], 'Vinnare': r['participant_name'] or '(resthög)'})
            ui.table(
                columns=[
                    {'name': 'Kategori', 'label': 'Kategori', 'field': 'Kategori', 'sortable': True},
                    {'name': 'Artikel', 'label': 'Artikel', 'field': 'Artikel', 'sortable': True},
                    {'name': 'Vinnare', 'label': 'Vinnare', 'field': 'Vinnare', 'sortable': True},
                ],
                rows=per_item,
                row_key='Artikel',
            ).classes('w-full')

            ui.separator()
            ui.label('Per deltagare').classes('text-md font-semibold')
            by_p: Dict[str, List[str]] = {}
            for r in rows:
                pn = r['participant_name'] or '(resthög)'
                by_p.setdefault(pn, []).append(r['item_name'])
            per_p = [{'Deltagare': k, 'Antal': len(v), 'Artiklar': ', '.join(sorted(v))} for k, v in by_p.items()]
            per_p.sort(key=lambda x: (-x['Antal'], x['Deltagare'].lower()))
            ui.table(
                columns=[
                    {'name': 'Deltagare', 'label': 'Deltagare', 'field': 'Deltagare', 'sortable': True},
                    {'name': 'Antal', 'label': 'Antal', 'field': 'Antal', 'sortable': True},
                    {'name': 'Artiklar', 'label': 'Artiklar', 'field': 'Artiklar'},
                ],
                rows=per_p,
                row_key='Deltagare',
            ).classes('w-full')
