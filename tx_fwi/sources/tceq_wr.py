# tx_fwi/sources/tceq_water_rights.py

import requests
import certifi


class TCEQWaterRightsSource:
    name= "TCEQ"

    RIGHTS_URL = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer/13/query"

    POINTS_URL = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer/3/query"
    MONTH_MAP = {"JAN_DIV":1,"FEB_DIV":2,"MAR_DIV":3,"APR_DIV":4,"MAY_DIV":5,"JUN_DIV":6,
                     "JUL_DIV":7,"AUG_DIV":8,"SEPT_DIV":9,"OCT_DIV":10,"NOV_DIV":11,"DEC_DIV":12}
  # monthly columns 
    MONTHLY_COLS = list(MONTH_MAP.keys())
        

    def __init__(self, ctx):

        self.ctx = ctx

    def _fetch_features(self, url: str, where: str = "1=1", out_fields: str = "*", batch_size: int = 2000):
        print (url)
        count = requests.get(
            url,
            verify=certifi.where(),
            params={"where": where, "returnCountOnly": "true", "f": "json"},
            timeout=60,
        ).json()["count"]
        records = []

        offset = 0

        while True:
            params = {"where": where, "outFields": out_fields, "f": "json",
                      "resultOffset": offset, "resultRecordCount": batch_size}
            payload = requests.get(url, verify= certifi.where(), params=params, timeout=60).json()
            batch = payload.get("features", [])  
            records.extend(batch)

            if len(batch) < batch_size:
                break

            offset += batch_size
            if len(records) >= count:
                break

        return records

    def fetch (self,start,end):

        rights = self._fetch_features(self.RIGHTS_URL)

        diversion_points = self._fetch_features(self.POINTS_URL)

        return {
            "rights": rights,
            "points": diversion_points,
        }
