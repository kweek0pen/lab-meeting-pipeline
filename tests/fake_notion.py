"""테스트용 가짜 Notion 서버 (httpx.MockTransport). 실제 API 를 호출하지 않는다."""

from __future__ import annotations

import json
import re
import uuid

import httpx

MEETING_DB = "11111111-1111-1111-1111-111111111111"
MEETING_DS = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AI_DB = "22222222-2222-2222-2222-222222222222"
AI_DS = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class FakeNotion:
    def __init__(self, dual: bool = True):
        self.dual = dual
        rel_meeting = {"data_source_id": AI_DS, "database_id": AI_DB}
        rel_ai = {"data_source_id": MEETING_DS, "database_id": MEETING_DB}
        if dual:
            rel_meeting |= {"type": "dual_property", "dual_property": {"synced_property_name": "회의록", "synced_property_id": "r2"}}
            rel_ai |= {"type": "dual_property", "dual_property": {"synced_property_name": "AI 정리본", "synced_property_id": "r1"}}
        else:
            rel_meeting |= {"type": "single_property", "single_property": {}}
            rel_ai |= {"type": "single_property", "single_property": {}}
        self.data_sources = {
            MEETING_DS: {"object": "data_source", "id": MEETING_DS, "properties": {
                "이름": {"id": "title", "name": "이름", "type": "title", "title": {}},
                "AI 정리본": {"id": "r1", "name": "AI 정리본", "type": "relation", "relation": rel_meeting},
                "날짜": {"id": "d1", "name": "날짜", "type": "date", "date": {}},
            }},
            AI_DS: {"object": "data_source", "id": AI_DS, "properties": {
                "제목": {"id": "title", "name": "제목", "type": "title", "title": {}},
                "회의록": {"id": "r2", "name": "회의록", "type": "relation", "relation": rel_ai},
            }},
        }
        self.databases = {MEETING_DB: MEETING_DS, AI_DB: AI_DS}
        self.pages: dict[str, dict] = {}
        self.children: dict[str, list[dict]] = {}
        self.failures: list[dict] = []  # {"method","pattern","status","times"}
        self.calls: list[tuple[str, str]] = []
        self.auth_headers: list[str] = []

    # ---- 테스트 도우미 ----
    def fail(self, method: str, pattern: str, status: int = 500, times: int = 1, code: str = "internal_server_error"):
        self.failures.append({"method": method, "pattern": pattern, "status": status, "times": times, "code": code})

    def pages_in(self, ds_id: str) -> list[dict]:
        return [p for p in self.pages.values() if p["parent"]["data_source_id"] == ds_id and not p["in_trash"]]

    def relation(self, page_id: str, prop: str) -> list[str]:
        return [r["id"] for r in self.pages[page_id]["properties"][prop]["relation"]]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # ---- 내부 ----
    @staticmethod
    def _rt(rich: list[dict]) -> list[dict]:
        return [{"type": "text", "plain_text": r["text"]["content"], "text": r["text"]} for r in rich]

    def _store_block(self, block: dict) -> dict:
        b = json.loads(json.dumps(block))
        b["id"] = str(uuid.uuid4())
        body = b[b["type"]]
        if "rich_text" in body:
            body["rich_text"] = self._rt(body["rich_text"])
        body.pop("children", None)
        return b

    def _resp(self, status: int, data: dict) -> httpx.Response:
        return httpx.Response(status, json=data)

    def _err(self, status: int, code: str, msg: str = "error") -> httpx.Response:
        return self._resp(status, {"object": "error", "status": status, "code": code, "message": msg})

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path.removeprefix("/v1")
        self.calls.append((method, path))
        self.auth_headers.append(request.headers.get("authorization", ""))
        for f in self.failures:
            if f["times"] > 0 and f["method"] == method and re.search(f["pattern"], path):
                f["times"] -= 1
                return self._err(f["status"], f["code"])
        body = json.loads(request.content) if request.content else {}

        if m := re.fullmatch(r"/databases/([\w-]+)", path):
            if m[1] not in self.databases:
                return self._err(404, "object_not_found")
            return self._resp(200, {"object": "database", "id": m[1], "data_sources": [{"id": self.databases[m[1]], "name": "x"}]})
        if m := re.fullmatch(r"/data_sources/([\w-]+)", path):
            if m[1] not in self.data_sources:
                return self._err(404, "object_not_found")
            return self._resp(200, self.data_sources[m[1]])
        if m := re.fullmatch(r"/data_sources/([\w-]+)/query", path):
            prop, want = body["filter"]["property"], body["filter"]["title"]["equals"]
            res = [p for p in self.pages_in(m[1])
                   if "".join(t["plain_text"] for t in p["properties"][prop]["title"]) == want]
            return self._resp(200, {"results": res, "has_more": False})
        if path == "/pages" and method == "POST":
            ds_id = body["parent"]["data_source_id"]
            ds = self.data_sources[ds_id]
            pid = str(uuid.uuid4())
            props = {}
            for name, schema in ds["properties"].items():
                val = body["properties"].get(name)
                if schema["type"] == "title":
                    props[name] = {"id": schema["id"], "type": "title", "title": self._rt(val["title"]) if val else []}
                elif schema["type"] == "relation":
                    props[name] = {"id": schema["id"], "type": "relation", "relation": (val or {}).get("relation", []),
                                   "has_more": False}
                elif schema["type"] == "date":
                    props[name] = {"id": schema["id"], "type": "date", "date": (val or {}).get("date")}
            page = {"object": "page", "id": pid, "url": f"https://www.notion.so/{pid.replace('-', '')}",
                    "parent": {"type": "data_source_id", "data_source_id": ds_id}, "properties": props,
                    "in_trash": False, "archived": False}
            self.pages[pid] = page
            self.children[pid] = [self._store_block(b) for b in body.get("children", [])]
            return self._resp(200, page)
        if m := re.fullmatch(r"/pages/([\w-]+)", path):
            page = self.pages.get(m[1])
            if not page:
                return self._err(404, "object_not_found")
            if method == "PATCH":
                for name, val in body.get("properties", {}).items():
                    if name not in page["properties"]:
                        return self._err(400, "validation_error", f"{name} is not a property")
                    if "relation" in val:
                        page["properties"][name]["relation"] = val["relation"]
                        self._sync_dual(page, name, val["relation"])
            return self._resp(200, page)
        if m := re.fullmatch(r"/blocks/([\w-]+)/children", path):
            if m[1] not in self.children:
                return self._err(404, "object_not_found")
            if method == "PATCH":
                if len(body["children"]) > 100:
                    return self._err(400, "validation_error", "children length should be ≤ 100")
                self.children[m[1]] += [self._store_block(b) for b in body["children"]]
                return self._resp(200, {"results": []})
            size = int(request.url.params.get("page_size", 100))
            return self._resp(200, {"results": self.children[m[1]][:size], "has_more": False})
        return self._err(404, "object_not_found", f"unhandled {method} {path}")

    def _sync_dual(self, page: dict, prop: str, relation: list[dict]) -> None:
        ds = self.data_sources[page["parent"]["data_source_id"]]
        rel = ds["properties"][prop]["relation"]
        if rel.get("type") != "dual_property":
            return
        other_prop = rel["dual_property"]["synced_property_name"]
        for r in relation:
            other = self.pages.get(r["id"])
            if other and not any(x["id"] == page["id"] for x in other["properties"][other_prop]["relation"]):
                other["properties"][other_prop]["relation"].append({"id": page["id"]})
