"""Documentation-only migration metadata; never registers compatibility tools."""

RELOCATED = {
    "page_wait": ("page", "wait_for_load", {}),
    "trace_get": ("artifact", "get", {}),
    "download_list": ("artifact", "list", {"kind": "download"}),
    "download_get": ("artifact", "get", {"expected_kind": "download"}),
    "download_save": ("artifact", "export", {}),
    "download_read_text": ("artifact", "read_text", {}),
    "download_delete": ("artifact", "delete", {}),
    "page_screenshot": ("screenshot", None, {}),
}


def mappings(catalog):
    new_handlers = {"screenshot", "artifact_read_text", "artifact_delete"}
    current = {
        operation.handler.__name__: (operation.tool, operation.op, {})
        for operation in catalog.operations.values()
        if operation.handler.__name__ not in new_handlers
    }
    return {**current, **RELOCATED}
