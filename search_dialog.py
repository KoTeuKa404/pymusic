# search_dialog.py

from kivymd.uix.screen import MDScreen
from kivymd.uix.chip import MDChip
from search_utils import load_search_history
from kivy.properties import ObjectProperty

class SearchDialogScreen(MDScreen):
    main_search_screen = ObjectProperty(None)  # посилання на головний екран

    def on_pre_enter(self):
        self.ids.search_input_dialog.text = ""
        self.show_search_history_dialog()

    def show_search_history_dialog(self):
        box = self.ids.search_history_box_dialog
        box.clear_widgets()
        query = self.ids.search_input_dialog.text.strip()
        if not query:
            return
        history = [q for q in load_search_history() if q and query.lower() in q.lower()]
        for q in history:
            chip = MDChip(
                text=q,
                icon_left="magnify",
                on_release=lambda inst, search=q: self.select_history(search)
            )
            box.add_widget(chip)

    def on_text_change(self, text):
        self.show_search_history_dialog()

    def select_history(self, query):
        # Передати запит на головний екран та закрити діалог
        if self.main_search_screen:
            self.main_search_screen.set_search_and_run(query)
        self.manager.current = "search"

    def perform_search_dialog(self):
        search_query = self.ids.search_input_dialog.text.strip()
        if self.main_search_screen:
            self.main_search_screen.set_search_and_run(search_query)
        self.manager.current = "search"
