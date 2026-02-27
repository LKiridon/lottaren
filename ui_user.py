# language: python
"""ui_user.py

User-sidor:
- /
- /vote
- /totals  (totala poäng per artikel)
- /results (resultat efter dragning)

Funktioner:
- Session-baserad "inloggning" via participant_id
- Byt användare (rensa session)
- Sticky knapp i botten som visas efter godkänd sparning och länkar till /totals
- /totals auto-navigerar till /results så fort admin kört dragning
- /results auto-uppdaterar 1 gång/sekund
"""

from __future__ import annotations

from nicegui import ui, app

import oldcore.core as core


def register_user_pages() -> None:

    @ui.page('/')
    def home():
        ui.colors(primary='#2563eb')
        ui.markdown('# Artikelutdelning')

        pid = app.storage.user.get('participant_id')
        if pid:
            row = core.q_one('SELECT name FROM participants WHERE id = ?', (int(pid),))
            if row:
                ui.markdown(f'Du är registrerad som **{row["name"]}**.')
                with ui.row().classes('gap-3'):
                    ui.button('Fortsätt till mina röster', on_click=lambda: ui.navigate.to('/vote')).classes('w-full')
                    ui.button(
                        'Byt användare',
                        on_click=lambda: (app.storage.user.pop('participant_id', None), ui.navigate.to('/')),
                    ).props('color=warning')
                ui.separator()

        ui.markdown('Registrera dig med namn och fördela poäng.')

        with ui.card().classes('w-full max-w-md'):
            name = ui.input('Namn').props('autofocus')

            def do_register():
                try:
                    pid2 = core.get_or_create_participant(name.value or '')
                except Exception as e:
                    ui.notify(str(e), color='negative')
                    return

                app.storage.user['participant_id'] = pid2
                ui.notify('Klart! Tar dig till din poängsättning.', color='positive')
                ui.navigate.to('/vote')

            ui.button('Gå till poängsättning', on_click=do_register).classes('w-full')

        with ui.row().classes('gap-4'):
            ui.link('Admin', '/admin')

    @ui.page('/vote')
    def vote_page():
        ui.colors(primary='#2563eb')

        pid = app.storage.user.get('participant_id')
        if not pid:
            ui.notify('Skriv in ditt namn igen för att komma tillbaka till din vy.', color='warning')
            ui.navigate.to('/')
            return

        prow = core.q_one('SELECT name FROM participants WHERE id = ?', (int(pid),))
        pname = str(prow['name']) if prow else 'Okänd'

        items = core.list_items()
        if not items:
            ui.markdown(f'# Poängsättning – {pname}')
            ui.markdown('Inga artiklar inlagda ännu. Be admin ladda upp Excel.')
            return

        with ui.card().classes('w-full'):
            ui.markdown('# Poängsättning')
            ui.markdown(f'**Deltagare:** {pname}')
            with ui.row().classes('gap-3'):
                ui.button('Byt användare', on_click=lambda: (app.storage.user.pop('participant_id', None), ui.navigate.to('/')))
                ui.link('Admin', '/admin')

        if core.POINT_BUDGET > 0:
            ui.markdown(f'Fördela exakt **{core.POINT_BUDGET} poäng**.')
        else:
            ui.markdown('Budget är **0** (ingen poängbudget att uppfylla).')

        if core.MAX_PER_ITEM > 0:
            ui.markdown(f'Max per artikel: **{core.MAX_PER_ITEM}**.')

        current = core.get_votes_for_participant(int(pid))

        # Footer som visas efter godkänd sparning
        footer = ui.footer().classes('w-full bg-white/90 backdrop-blur border-t border-gray-200')
        footer.visible = False
        with footer:
            with ui.row().classes('w-full justify-center px-4 py-3'):
                ui.button(
                    'Visa totala poäng (och resultat när dragning körts)',
                    on_click=lambda: ui.navigate.to('/totals'),
                ).classes('w-full max-w-md').props('color=primary')

        def show_footer() -> None:
            footer.visible = True
            try:
                footer.update()
            except Exception:
                pass

        def hide_footer() -> None:
            footer.visible = False
            try:
                footer.update()
            except Exception:
                pass

        with ui.card().classes('w-full'):
            ui.label('Artiklar').classes('text-lg font-medium')
            editors: dict[int, ui.number] = {}

            by_cat: dict[str, list] = {}
            for it in items:
                cat = str(it['category'] or '').strip() or '(okategoriserat)'
                by_cat.setdefault(cat, []).append(it)

            total_label = ui.label()

            def recalc_total() -> int:
                s = 0
                for ed in editors.values():
                    v = int(ed.value or 0)
                    if v < 0:
                        v = 0
                    if core.MAX_PER_ITEM > 0:
                        v = min(v, core.MAX_PER_ITEM)
                    s += v

                if core.POINT_BUDGET > 0:
                    total_label.text = f'Summa: {s}/{core.POINT_BUDGET}'
                    if s == core.POINT_BUDGET:
                        total_label.classes(remove='text-negative')
                        total_label.classes(add='text-positive')
                    else:
                        total_label.classes(remove='text-positive')
                        total_label.classes(add='text-negative')
                else:
                    total_label.text = f'Summa: {s}'
                    total_label.classes(remove='text-negative')
                    total_label.classes(add='text-positive')
                return s

            for cat in sorted(by_cat.keys(), key=lambda x: x.lower()):
                its = sorted(by_cat[cat], key=lambda r: str(r['name']).lower())
                ui.separator()
                ui.label(cat).classes('text-md font-semibold')

                for it in its:
                    iid = int(it['id'])
                    qty = int(it['quantity'] or 1)
                    name = str(it['name'])
                    if qty > 1:
                        name = f'{name} (antal: {qty})'

                    with ui.row().classes('items-center justify-between w-full'):
                        ui.label(name).classes('min-w-[240px]')
                        n = ui.number(
                            label='Poäng',
                            value=int(current.get(iid, 0)),
                            min=0,
                            step=1,
                            format='%d',
                        ).classes('w-32')
                        editors[iid] = n

            recalc_total()

            def on_any_change() -> None:
                hide_footer()
                recalc_total()

            for ed in editors.values():
                ed.on('update:model-value', lambda e, _ed=ed: on_any_change())

            def save():
                MIN_VOTED_ITEMS = 10  # minst så många artiklar måste ha >0 poäng

                votes: dict[int, int] = {}
                s = 0
                voted_count = 0

                for iid, ed in editors.items():
                    v = int(ed.value or 0)
                    if v < 0:
                        v = 0
                    if core.MAX_PER_ITEM > 0:
                        v = min(v, core.MAX_PER_ITEM)
                        ed.value = v

                    votes[iid] = v
                    s += v
                    if v > 0:
                        voted_count += 1

                if core.POINT_BUDGET > 0 and s != core.POINT_BUDGET:
                    ui.notify(f'Summa måste vara exakt {core.POINT_BUDGET} (nu {s}).', color='negative')
                    return

                if voted_count < MIN_VOTED_ITEMS:
                    ui.notify(f'Du måste rösta på minst {MIN_VOTED_ITEMS} artiklar (nu {voted_count}).', color='negative')
                    return

                try:
                    core.upsert_votes(int(pid), votes)
                except Exception as ex:
                    ui.notify(str(ex), color='negative')
                    return

                ui.notify('Sparat!', color='positive')
                show_footer()

            ui.button('Spara', on_click=save).classes('w-full')

    @ui.page('/totals')
    def totals_page():
        ui.colors(primary='#2563eb')

        pid = app.storage.user.get('participant_id')
        if not pid:
            ui.notify('Registrera dig först.', color='warning')
            ui.navigate.to('/')
            return

        prow = core.q_one('SELECT name FROM participants WHERE id = ?', (int(pid),))
        pname = str(prow['name']) if prow else 'Okänd'

        ui.markdown('# Totala poäng per artikel')
        ui.markdown(f'**Deltagare:** {pname}')

        with ui.row().classes('gap-3'):
            ui.button('Tillbaka till mina röster', on_click=lambda: ui.navigate.to('/vote'))
            ui.button('Byt användare', on_click=lambda: (app.storage.user.pop('participant_id', None), ui.navigate.to('/')))
            ui.link('Admin', '/admin')

        info = ui.label('Väntar på att admin ska köra dragning… (sidan uppdateras automatiskt)')

        @ui.refreshable
        def items_view() -> None:
            items2 = core.list_items_with_point_totals()
            item_rows = [
                {
                    'ID': int(r['id']),
                    'Kategori': r['category'] or '',
                    'Artikel': r['name'],
                    'Antal': int(r['quantity']),
                    'Totalpoäng': int(r['total_points']),
                    'Antal röstande': int(r['voters']),
                }
                for r in items2
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
                row_key='ID',
            ).classes('w-full')

        items_view()

        def tick() -> None:
            # 1) uppdatera totals-tabellen
            items_view.refresh()

            # 2) om admin kört dragning -> gå automatiskt till results
            run_id = core.get_latest_run_id()
            if run_id:
                info.text = 'Dragning hittad – öppnar resultat…'
                ui.navigate.to('/results')

        ui.timer(1.0, tick)

    @ui.page('/results')
    def results_page():
        ui.colors(primary='#2563eb')

        pid = app.storage.user.get('participant_id')
        if not pid:
            ui.notify('Registrera dig först.', color='warning')
            ui.navigate.to('/')
            return

        prow = core.q_one('SELECT name FROM participants WHERE id = ?', (int(pid),))
        pname = str(prow['name']) if prow else 'Okänd'

        ui.markdown('# Resultat')
        ui.markdown(f'**Deltagare:** {pname}')

        with ui.row().classes('gap-3'):
            ui.button('Till totalsidan', on_click=lambda: ui.navigate.to('/totals'))
            ui.button('Till mina röster', on_click=lambda: ui.navigate.to('/vote'))
            ui.button('Byt användare', on_click=lambda: (app.storage.user.pop('participant_id', None), ui.navigate.to('/')))
            ui.link('Admin', '/admin')

        status = ui.label()

        @ui.refreshable
        def results_view() -> None:
            run_id = core.get_latest_run_id()
            if not run_id:
                status.text = 'Ingen dragning gjord ännu.'
                ui.label('Väntar på dragning…').classes('text-md')
                return

            rows = core.get_results(run_id)
            status.text = f'Aktuell dragning: {run_id} (auto-uppdateras)'

            ui.separator()
            ui.label('Per artikel').classes('text-md font-semibold')

            per_item = []
            for r in rows:
                per_item.append({
                    'AllocID': int(r['id']),
                    'Kategori': r['category'] or '',
                    'Artikel': r['item_name'],
                    'Vinnare': r['participant_name'] or '(resthög)',
                })

            ui.table(
                columns=[
                    {'name': 'Kategori', 'label': 'Kategori', 'field': 'Kategori', 'sortable': True},
                    {'name': 'Artikel', 'label': 'Artikel', 'field': 'Artikel', 'sortable': True},
                    {'name': 'Vinnare', 'label': 'Vinnare', 'field': 'Vinnare', 'sortable': True},
                ],
                rows=per_item,
                row_key='AllocID',
            ).classes('w-full')

            ui.separator()
            ui.label('Per deltagare').classes('text-md font-semibold')

            by_p: dict[str, list[str]] = {}
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

        results_view()
        ui.timer(1.0, results_view.refresh)