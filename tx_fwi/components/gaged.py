# tx_fwi/components/gaged_usgs.py
import pandas as pd
from .base import Component
from ..sources.usgs import fetch_usgs_daily_cfs

class USGSGaged(Component):
    name = "gaged_usgs"

    def update(self) -> pd.DataFrame:
        # Watermark = last processed date for this component
        wm = self.ctx.storage.get_watermark(self.name, default="2015-01-01")
        start = (wm + pd.Timedelta(days=1)) if wm is not None else pd.Timestamp("2015-01-01")

        end = pd.to_datetime(self.ctx.end_date).normalize() if self.ctx.end_date else pd.Timestamp.utcnow().normalize()

        # WS registry: you will maintain this file, replacing the hard-coded dicts in the script [6](https://twdb-my.sharepoint.com/personal/kim_karimi_twdb_texas_gov/Documents/Microsoft%20Copilot%20Chat%20Files/formatting.py)
        # Expected columns: ws_id, estuary, usgs_site_id
        watersheds = gpd.read_file(self.ctx.watershed_path)
        reg = watersheds.dropna(subset=["USGS_ID"])
        

        frames = []
        for _, row in reg.iterrows():
            site = row["USGS_ID"]
            ws_id = str(row["WS_ID"])
            estuary = row["Estuary"]



            s = fetch_usgs_discharge_daily(site, start=start, end=end)
            if s.empty:
                continue

            df = s.reset_index()
            df.columns = ["date", "value_afday"]
            df["ws_id"] = ws_id
            df["estuary"] = estuary
            df["component"] = self.name
            df["source"] = "USGS"
            frames.append(df)

        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        # Update watermark to the max date we successfully produced
        if not out.empty:
            self.ctx.storage.set_watermark(self.name, out["date"].max())

        return out[["date", "ws_id", "estuary", "component", "value_afday", "source"]]
