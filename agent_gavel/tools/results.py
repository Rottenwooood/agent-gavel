"""Result contracts and declarative compact views, never recursive data cleanup."""

import copy
from dataclasses import dataclass, field
from functools import lru_cache

from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class View:
    keep: tuple | None = None
    omit: tuple = ()
    omit_null: tuple = ()
    omit_empty: tuple = ()
    same_as: tuple = ()
    equals_value_of: tuple = ()
    children: dict = field(default_factory=dict)
    items: object = None


def serialize(value, view):
    if view.items is not None:
        return [serialize(item, view.items) for item in value]
    out = {k: copy.deepcopy(v) for k, v in value.items() if view.keep is None or k in view.keep}
    for key, child in view.children.items():
        if key in out:
            out[key] = serialize(out[key], child)
    for key in view.omit:
        out.pop(key, None)
    for key in view.omit_null:
        if key in out and out[key] is None:
            out.pop(key)
    for key in view.omit_empty:
        if key in out and out[key] in ({}, []):
            out.pop(key)
    for key, other in view.same_as:
        if key in out and other in out and out[key] == out[other]:
            out.pop(key)
    for key, other, literal in view.equals_value_of:
        if key in out and other in out and out[key] == (out[other] == literal):
            out.pop(key)
    return out


PAGE_VIEW = View(
    omit_null=("opener_page_id",),
    equals_value_of=(("closed", "page_status", "closed"),),
)
ELEMENT_VIEW = View(
    omit=("bounds", "candidates", "visible"),
    omit_null=("id", "name", "type", "placeholder", "aria", "role", "href"),
)
ACTION_VIEW = View(children={"execution": View(same_as=(("url_before", "url_after"),))})


def type_schema(name):
    if name.endswith("[]"):
        return {"type": "array", "items": type_schema(name[:-2])}
    primitives = {
        "str": "string",
        "int": "integer",
        "num": "number",
        "bool": "boolean",
        "map": "object",
        "list": "array",
    }
    if name == "JSON":
        return {}
    if name in primitives:
        return {"type": primitives[name]}
    return {"$ref": "#/$defs/" + name}


@dataclass(frozen=True)
class Result:
    fields: str
    view: View = field(default_factory=View)
    untrusted: bool = False
    keep_elapsed: bool = False
    base: str | None = None

    @property
    def shape(self):
        # Field names convey scalar values; retain container/reference types.
        fields = []
        for item in self.fields.split():
            name, kind = item.split(":", 1)
            fields.append(name if kind in ("str", "int", "num", "bool", "JSON") else item)
        return (self.base + "+" if self.base else "") + "{" + ",".join(fields) + "}"

    def schema(self):
        properties, required = {}, []
        for field_name in self.fields.split():
            name, field_type = field_name.split(":", 1)
            optional = name.endswith("?")
            name = name.rstrip("?")
            properties[name] = type_schema(field_type)
            if not optional:
                required.append(name)
        if self.base:
            parent = RESULT_TYPES[self.base].schema()
            properties = {**parent["properties"], **properties}
            required = list(dict.fromkeys(parent["required"] + required))
        return {"type": "object", "properties": properties, "required": required}

    def validate(self, value):
        if not isinstance(value, dict):
            raise TypeError("public tool results must be objects")
        # Failure branches retain their original evidence and error contracts.
        if value.get("status") not in ("ok", "pass"):
            return
        schema = {**self.schema(), "$defs": result_definitions()}
        error = next(Draft202012Validator(schema).iter_errors(value), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path)
            raise TypeError(f"Result contract failed at {path or 'result'} ({error.validator})")


RESULT_TYPES = {
    "Page": Result(
        "page_id:str context_id:str url:JSON page_status:str opener_page_id?:JSON closed?:bool",
        PAGE_VIEW,
    ),
    "Pages": Result("items:Page[]", View(children={"items": View(items=PAGE_VIEW)})),
    "Location": Result("page_id:str url:str"),
    "PageClosed": Result("page_id:str closed:bool"),
    "Navigation": Result(
        "page_id:str final_url:str navigated:bool redirected:bool title:str status_code:JSON "
        "assertions?:list warning?:str text_len?:int text?:str text_truncated?:bool anchors?:list "
        "page_status?:str http_error?:bool http_status?:int artifact_id?:str kind?:str hint?:str "
        "suggested_filename?:str mime_type?:str download_url?:str diff?:map",
        View(omit=("diff",), same_as=(("requested_url", "final_url"),)),
    ),
    "Snapshot": Result(
        "page_id:str url:str title:str ready:str counts:map elements?:list html?:str",
        untrusted=True,
    ),
    "Text": Result(
        "text?:JSON links?:list anchors?:list total?:int truncated?:bool mode?:str selector?:JSON",
        View(omit_null=("selector",)),
        untrusted=True,
    ),
    "Read": Result("features:map errors?:map complete?:bool empty?:list", untrusted=True),
    "Explore": Result(
        "items:list total?:int", View(children={"items": View(items=ELEMENT_VIEW)}), untrusted=True
    ),
    "Meta": Result(
        "url:str title:str description?:JSON canonical?:JSON og_title?:JSON h1?:list h2?:list",
        View(omit_null=("description", "canonical", "og_title")),
        untrusted=True,
    ),
    "Action": Result("page_id:str execution:map assertions?:list diff?:JSON", ACTION_VIEW),
    "Assertions": Result("page_id:str assertions:list"),
    "AssertionSchema": Result("forms:map read:list ops:list defaults:str"),
    "WaitLoad": Result("page_id:str state:str url:str"),
    "WaitSelector": Result("page_id:str selector:str state:str"),
    "WaitText": Result("page_id:str text:str state:str url:str"),
    "WaitResponse": Result("page_id:str url:str http_status:int method:str"),
    "Download": Result(
        "page_id:str artifact_id:str suggested_filename:str size_bytes:int mime_type:JSON sha256:str"
    ),
    "Popup": Result("page_id:str new_page_id:str opener_page_id?:JSON url?:JSON new_page?:JSON"),
    "Event": Result("event:map", untrusted=True),
    "Dialog": Result(
        "page_id:str policy:str accepted?:bool dialog?:JSON dialog_type?:str message?:str"
    ),
    "Image": Result(
        "page_id:str artifact_id:str width:int height:int device_scale_factor?:JSON exported?:bool exported_filename?:str",
        untrusted=True,
    ),
    "ArtifactRef": Result("page_id:str artifact_id:str exported?:bool exported_filename?:str"),
    "Artifact": Result(
        "artifact_id:str kind:str suggested_filename:str size_bytes:int mime_type:JSON sha256:str"
    ),
    "Artifacts": Result("items:Artifact[] total:int"),
    "Export": Result("artifact_id:str filename:str exported:bool size_bytes:JSON exported_to?:str"),
    "Extracted": Result(
        "artifact_id:str extracted:bool kind:JSON text?:str chars?:int hint?:str", untrusted=True
    ),
    "Deleted": Result("artifact_id:str deleted:bool"),
    "Cleanup": Result("removed:list count:int"),
    "Context": Result(
        "context_id:str container:str page_ids:list active_page_id:JSON "
        "busy_owner:JSON ephemeral_warning?:str"
    ),
    "ContextClosed": Result("context_id:str container:str closed?:bool detached?:bool"),
    "Cookies": Result("context_id:str cookies:list count:int"),
    "CookiesAdded": Result("context_id:str added:int"),
    "CookiesCleared": Result("context_id:str cleared:bool"),
    "Headers": Result("context_id:str headers:map"),
    "Permissions": Result("context_id:str permissions:list"),
    "Browser": Result("process_id:str mode:str alive:bool headless?:JSON"),
    "BrowserStatus": Result(
        "counts:map active_pages:map processes:list domain_restricted:bool "
        "recording:bool runs:map profile:map",
        View(
            keep=(
                "status",
                "counts",
                "active_pages",
                "processes",
                "domain_restricted",
                "recording",
                "runs",
                "profile",
                "warning",
            ),
            children={
                "processes": View(items=View(keep=("process_id", "mode", "alive", "headless")))
            },
        ),
    ),
    "BrowserClosed": Result("closed:bool"),
    "ProfileCopied": Result("created:str profiles:list manifest:map reused?:bool"),
    "Profiles": Result("default:str items:list"),
    "ProfileDeleted": Result("deleted:str profiles:list"),
    "StorageExport": Result("context_id:str path?:str storage_state?:map"),
    "WorkflowSaved": Result(
        "template_id:str version:int steps:int parameters:map warnings?:list "
        "recorded_steps?:int template?:JSON"
    ),
    "WorkflowDeleted": Result("template_id:str deleted:bool"),
    "Validation": Result("valid:bool errors:list warnings:list normalized:JSON"),
    "Workflows": Result("items:list"),
    "Template": Result("template:map"),
    "Published": Result("template_id:str path?:str template?:map"),
    "WorkflowStats": Result("stats:JSON"),
    "Recording": Result("context_id:JSON page_id:JSON keep_all:bool steps:int"),
    "WorkflowRun": Result(
        "run_id:str template_id:str steps_completed:int steps_total:int checkpoints_passed:int "
        "artifacts?:list steps?:list observed?:map proposed_repairs?:list resume_hint?:str "
        "failed_step?:str failed_step_index?:int reason?:str version?:int",
        keep_elapsed=True,
    ),
    "RunControl": Result("run_id:str paused?:bool cancelled?:bool"),
    "Tracing": Result("context_id:str tracing:bool"),
    "TraceArtifact": Result("context_id:str artifact_id:str size_bytes:int"),
    "Network": Result("context_id:str enabled:bool"),
    "NetworkRecords": Result("context_id:str enabled:bool records:list count:int", untrusted=True),
    "Events": Result("items:list total:int", untrusted=True),
    "Metrics": Result("metrics:map"),
    "Shutdown": Result("stopping:bool daemon_pid:int"),
}


@lru_cache(maxsize=1)
def result_definitions():
    return {name: result.schema() for name, result in RESULT_TYPES.items()}


def project(name, original, detail):
    contract = RESULT_TYPES.get(name)
    out = dict(original)
    if contract and contract.untrusted:
        out["untrusted"] = True
    if detail == "full":
        return out
    out.pop("operation_id", None)
    if not contract or not contract.keep_elapsed:
        out.pop("elapsed_ms", None)
    if contract and out.get("status") in ("ok", "pass"):
        out = serialize(out, contract.view)
    return out
