# storage.py
import pandas as pd
from pathlib import Path
import json

class Storage:

    def __init__(self, root):
        self.root = Path(root)
        self.file = self.root / "fwi_timeseries.parquet"
        self.state = self.root / "watermarks.json"

    def append(self, df_new):
        if self.file.exists():
            df_old = pd.read_parquet(self.file)
            df = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df = df_new

        df.drop_duplicates(
            subset=["date","id","component","flow_role"],
            inplace=True
        )

        df.to_parquet(self.file, index=False)

    def get_watermark(self, key):
        if self.state.exists():
            wm = json.loads(self.state.read_text())
            if key in wm:
                return pd.to_datetime(wm[key])
        return None

    def set_watermark(self, key, date):
        wm = {}
        if self.state.exists():
            wm = json.loads(self.state.read_text())

        wm[key] = str(date.date())
        self.state.write_text(json.dumps(wm, indent=2))

