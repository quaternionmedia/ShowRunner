from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Header, Footer, Placeholder

import csv
import io

CSV = ""

with open("data.csv") as f:
    CSV = f.read()

class Script(DataTable):
    pass

class MuteSheet(DataTable):
    pass

class ShowRunner(App):
    TITLE = "ShowRuner"
    CSS_PATH = "styles.scss"
    BINDINGS = [
        Binding("ctrl+c,ctrl+q", "app.quit", "Quit", show=True),
    ]

    script: Script
    mute_sheet: MuteSheet

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.script = Script()
        self.mute_sheet = MuteSheet()


    def on_mount(self) -> None:
        rows = csv.reader(io.StringIO(CSV))
        self.script.add_columns(*next(rows))
        self.script.add_rows(rows)
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield self.script
        yield Footer()

if __name__ == "__main__":
    app = ShowRunner()
    app.run()
