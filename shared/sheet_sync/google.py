"""Optional Sheets REST adapter. No credentials or network use during import."""
from __future__ import annotations

from urllib.parse import quote

from .engine import TABS, headers


class TransientSyncError(RuntimeError):
    pass


class GoogleSheets:
    def __init__(self, spreadsheet_id: str, session):
        self.base = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id, safe="")
        self.session = session
        self.properties = {}

    @classmethod
    def authenticate(cls, spreadsheet_id, credentials_path):
        from google.auth.exceptions import GoogleAuthError, TransportError
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import AuthorizedSession

        class SafeSession(AuthorizedSession):
            def request(self, *args, **kwargs):
                try:
                    return super().request(*args, **kwargs)
                except TransportError as exc:
                    raise TransientSyncError("Google token transport unavailable") from exc
                except GoogleAuthError as exc:
                    raise ValueError("Google authentication failed; check the service account") from exc

        try:
            creds = Credentials.from_service_account_file(
                str(credentials_path), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        except (GoogleAuthError, ValueError) as exc:
            raise ValueError("Invalid Google service-account credential file") from exc
        return cls(spreadsheet_id, SafeSession(creds))

    def request(self, method, suffix, **kwargs):
        import requests
        try:
            response = self.session.request(method, self.base + suffix, timeout=30, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientSyncError("Google connection failed; will reconcile before retrying") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientSyncError(f"Google HTTP {response.status_code}; synchronization pending")
        if response.status_code >= 400:
            # Never echo response bodies/credential details to console or Sheet.
            raise ValueError(f"Google HTTP {response.status_code}; check access, headers, and validation")
        return response.json()

    def read(self):
        payload = self.request("GET", "", params={
            "fields": "sheets.properties(sheetId,title,gridProperties(rowCount,columnCount))"})
        available = {s["properties"]["title"]: s["properties"] for s in payload["sheets"]}
        result = {}
        for key, title in TABS.items():
            if title not in available:
                raise ValueError(f"Missing sheet: {title}")
            prop = available[title]
            self.properties[key] = prop
            bounds = prop["gridProperties"]
            # Bounded pages; FORMULA allows the planner to preserve formula cells.
            if bounds["rowCount"] > 50000 or bounds["columnCount"] > 100:
                raise ValueError(f"{title} exceeds V1 reporting bounds")
            rows = []
            end_column = column_name(bounds["columnCount"] - 1)
            for start in range(1, bounds["rowCount"] + 1, 500):
                end = min(start + 499, bounds["rowCount"])
                span = f"'{title}'!A{start}:{end_column}{end}"
                data = self.request("GET", "/values/" + quote(span, safe=""),
                                    params={"valueRenderOption": "FORMULA"})
                values = data.get("values", [])
                rows.extend(values + [[] for _ in range(end - start + 1 - len(values))])
            while rows and not any(v != "" for v in rows[-1]):
                rows.pop()
            result[key] = rows
        return result

    def prepare(self, grids):
        cols = headers(grids["stages"])
        if "Stage Attempt ID" in cols:
            return False
        prop = self.properties["stages"]
        # Append after every occupied column, including legacy/manual values.
        column = max(len(row) for row in grids["stages"])
        for row in grids["stages"][1:]:
            if any(v != "" for v in row):
                raise ValueError("Stage log already contains legacy data; add/reconcile Stage Attempt ID manually")
        requests = []
        if column >= prop["gridProperties"]["columnCount"]:
            requests.append({"appendDimension": {"sheetId": prop["sheetId"],
                "dimension": "COLUMNS", "length": 1}})
        requests.append(cell_update(prop["sheetId"], 0, column, "Stage Attempt ID"))
        self.request("POST", ":batchUpdate", json={"requests": requests})
        return True

    def write(self, records):
        # A record's key and fields are in the same atomic Sheets batch. After
        # any uncertain response, the caller starts again with fresh Sheet reads.
        for offset in range(0, len(records), 50):
            batch = records[offset:offset + 50]
            requests = []
            required = {}
            for record in batch:
                required[record["tab"]] = max(required.get(record["tab"], 0), record["row"] + 1)
            for tab, count in required.items():
                prop = self.properties[tab]
                existing = prop["gridProperties"]["rowCount"]
                if count > existing:
                    requests.append({"appendDimension": {"sheetId": prop["sheetId"],
                        "dimension": "ROWS", "length": count - existing}})
            for record in batch:
                sheet_id = self.properties[record["tab"]]["sheetId"]
                requests.extend(cell_update(sheet_id, record["row"], col, value)
                                for col, value in record["edits"])
            self.request("POST", ":batchUpdate", json={"requests": requests})
            for tab, count in required.items():
                bounds = self.properties[tab]["gridProperties"]
                bounds["rowCount"] = max(bounds["rowCount"], count)

    def close(self):
        self.session.close()


def cell_update(sheet_id, row, column, value):
    typed = {"numberValue": value} if isinstance(value, (int, float)) else {"stringValue": str(value)}
    # Explicit stringValue prevents formula injection from persisted error text.
    return {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row,
        "columnIndex": column}, "rows": [{"values": [{"userEnteredValue": typed}]}],
        "fields": "userEnteredValue"}}


def column_name(index):
    result = ""
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result
