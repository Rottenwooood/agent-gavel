"""M3 高级浏览器能力验收：上传/拖拽/iframe/dialog/popup/下载/存储/网络。

docs/tech-plan.md §8（M3）与 §11.3 必测项。
"""

import asyncio

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def sess(gavel_tools, webapp_server):
    r = await gavel_tools["session_create"]()
    return (gavel_tools, r["session_id"], r["context_id"], r["page_id"],
            webapp_server)


async def test_select_check_uncheck(sess):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/forms.html")
    r = await tools["locator_select"](
        page_id=pid, target={"by": "css", "value": "#color"}, value="g",
        assertions={"v": {"read": "value", "selector": "#color",
                          "op": "eq", "value": "g"}})
    assert r["status"] == "pass"
    r = await tools["locator_select"](
        page_id=pid, target={"by": "css", "value": "#color"}, value="Red",
        select_by="label",
        assertions={"v": {"read": "value", "selector": "#color",
                          "op": "eq", "value": "r"}})
    assert r["status"] == "pass"
    assert (await tools["locator_check"](
        page_id=pid, target={"by": "css", "value": "#agree"},
        assertions={"c": {"read": "expr",
                          "expr": "document.querySelector('#agree').checked",
                          "op": "eq", "value": True}}))["status"] == "pass"
    assert (await tools["locator_uncheck"](
        page_id=pid, target={"by": "css", "value": "#agree"},
        assertions={"c": {"read": "expr",
                          "expr": "document.querySelector('#agree').checked",
                          "op": "eq", "value": False}}))["status"] == "pass"


async def test_upload(sess, tmp_path):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/forms.html")
    f = tmp_path / "up.txt"
    f.write_text("payload")
    r = await tools["locator_upload"](
        page_id=pid, target={"by": "css", "value": "#upload"}, files=[str(f)],
        assertions={"n": {"read": "expr",
                          "expr": "document.querySelector('#upload').files.length",
                          "op": "eq", "value": 1}})
    assert r["status"] == "pass"


async def test_drag(sess):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/drag.html")
    r = await tools["locator_drag"](
        page_id=pid, target={"by": "css", "value": "#drag-src"},
        destination={"by": "css", "value": "#drop-zone"},
        assertions={"r": {"selector": "#drop-res", "op": "contains",
                          "value": "dropped"}})
    assert r["status"] == "pass"


async def test_iframe(sess):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/iframe.html")
    rd = await tools["page_read"](page_id=pid, page_features={
        "h": "document.querySelector('#frame').contentDocument"
             ".querySelector('#heading').innerText"})
    assert rd["features"]["h"] == "Hello"
    # 直接对 frame 内的元素操作 + 断言（验证 frame 根生效）
    r = await tools["locator_fill"](
        page_id=pid,
        target={"frame": "#frame", "by": "css", "value": "#text-input"},
        value="in-frame",
        assertions={"v": {"read": "value", "selector": "#text-input",
                          "frame": "#frame", "op": "eq", "value": "in-frame"}})
    assert r["status"] == "pass"


async def test_download_full_cycle(sess, tmp_path):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/download.html")
    await tools["locator_click"](page_id=pid,
                                 target={"by": "css", "value": "#dl"})
    dl = await tools["page_wait_for_download"](page_id=pid, timeout_s=10)
    assert dl["status"] == "ok"
    assert dl["suggested_filename"] == "hello.txt"
    assert dl["mime_type"] == "text/plain"
    assert len(dl["sha256"]) == 64
    aid = dl["artifact_id"]

    # 文本抽取
    txt = await tools["download_read_text"](artifact_id=aid)
    assert txt["extracted"] and "hello agent-gavel" in txt["text"]
    # get / list
    assert (await tools["download_get"](artifact_id=aid))["artifact_id"] == aid
    assert any(i["artifact_id"] == aid
               for i in (await tools["download_list"](page_id=pid))["items"])
    # 保存到本地
    saved = await tools["download_save"](artifact_id=aid, dest_dir=str(tmp_path))
    assert (tmp_path / "hello.txt").exists()
    assert saved["size_bytes"] > 0
    # artifact_export 直连
    exp_dir = tmp_path / "exp"
    exp = await tools["artifact_export"](artifact_id=aid, dest_dir=str(exp_dir),
                                         filename="renamed.txt")
    assert (exp_dir / "renamed.txt").exists()
    assert exp["size_bytes"] > 0
    # 不暴露绝对路径
    meta = await tools["artifact_get"](artifact_id=aid)
    assert "path" not in meta
    # 删除
    assert (await tools["download_delete"](artifact_id=aid))["deleted"] is True
    assert (await tools["artifact_get"](artifact_id=aid))["status"] == "error"


async def test_popup(sess):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/popup.html")
    await tools["locator_click"](page_id=pid,
                                 target={"by": "css", "value": "#open-tab"})
    pp = await tools["page_wait_for_popup"](page_id=pid, timeout_s=10)
    assert pp["status"] == "ok"
    assert pp["opener_page_id"] == pid
    assert pp["new_page_id"] != pid
    # 新页面已登记
    listing = await tools["page_list"]()
    assert any(p["page_id"] == pp["new_page_id"] for p in listing["items"])


async def test_dialog_auto_and_manual(sess):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/dialog.html")

    await tools["page_handle_dialog"](page_id=pid, policy="auto_accept")
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#confirm-btn"},
        assertions={"r": {"selector": "#res", "op": "eq", "value": "yes"}})
    assert r["status"] == "pass"

    await tools["page_handle_dialog"](page_id=pid, policy="auto_dismiss")
    r = await tools["locator_click"](
        page_id=pid, target={"by": "css", "value": "#confirm-btn"},
        assertions={"r": {"selector": "#res", "op": "eq", "value": "no"}})
    assert r["status"] == "pass"

    # manual：延迟触发对话框，等事件后手动处理
    await tools["page_handle_dialog"](page_id=pid, policy="manual")
    await tools["page_read"](page_id=pid, page_features={
        "x": "(()=>{setTimeout(()=>{window.__r=confirm('ok?')},100);"
             " return 'scheduled'})()"})
    ev = await tools["page_wait_for_event"](kinds=["dialog_opened"],
                                            page_id=pid, timeout_s=5)
    assert ev["event"]["dialog_type"] == "confirm"
    assert (await tools["page_handle_dialog"](page_id=pid,
                                              action="accept"))["status"] == "ok"
    await asyncio.sleep(0.2)
    rd = await tools["page_read"](page_id=pid,
                                 page_features={"r": "window.__r"})
    assert rd["features"]["r"] is True


async def test_wait_for_response(sess):
    tools, _sid, _cid, pid, base = sess
    await tools["page_navigate"](page_id=pid, url=f"{base}/basic.html")
    await tools["locator_click"](page_id=pid,
                                 target={"by": "css", "value": "#fetch-slow"})
    r = await tools["page_wait_for_response"](page_id=pid, url="/slow",
                                              timeout_s=10)
    assert r["status"] == "ok"
    assert r["url"].endswith("/slow")
    assert r["http_status"] == 200


async def test_context_cookies_headers_permissions(sess):
    tools, _sid, cid, _pid, base = sess
    await tools["context_cookies_set"](
        context_id=cid, cookies=[{"name": "k", "value": "v", "url": base}])
    ck = await tools["context_cookies_get"](context_id=cid, url=base)
    assert any(c["name"] == "k" for c in ck["cookies"])
    assert (await tools["context_cookies_clear"](context_id=cid))["cleared"]
    assert (await tools["context_cookies_get"](context_id=cid,
                                               url=base))["count"] == 0
    assert (await tools["context_set_headers"](
        context_id=cid, headers={"X-Test": "1"}))["status"] == "ok"
    assert (await tools["context_set_permissions"](
        context_id=cid, permissions=["geolocation"], origin=base))["status"] == "ok"
    assert (await tools["context_set_permissions"](
        context_id=cid, permissions=[]))["status"] == "ok"


async def test_artifact_cleanup(sess):
    tools, _sid, _cid, pid, base = sess
    ss = await tools["page_screenshot"](page_id=pid)
    assert ss["status"] == "ok"
    assert (await tools["artifact_list"]())["total"] >= 1
    res = await tools["artifact_cleanup"](ttl_days=0)
    assert res["status"] == "ok"
    assert (await tools["artifact_list"]())["total"] == 0


async def test_session_state_context_and_reset(gavel_tools, webapp_server,
                                               tmp_path):
    tools = gavel_tools
    r = await tools["session_create"]()
    sid, cid = r["session_id"], r["context_id"]
    await tools["context_cookies_set"](
        context_id=cid, cookies=[{"name": "auth", "value": "1",
                                  "url": webapp_server}])
    path = str(tmp_path / "state.json")
    ex = await tools["session_export_state"](session_id=sid, path=path)
    assert ex["status"] == "ok" and (tmp_path / "state.json").exists()

    imp = await tools["session_import_state"](path=path)
    assert imp["status"] == "ok"
    ck = await tools["context_cookies_get"](context_id=imp["context_id"],
                                            url=webapp_server)
    assert any(c["name"] == "auth" for c in ck["cookies"])

    c2 = await tools["context_create"](session_id=imp["session_id"])
    assert c2["status"] == "ok" and c2["context_id"] != imp["context_id"]

    rs = await tools["session_reset"](session_id=sid)
    assert rs["status"] == "ok"
    assert rs["session_id"] != sid
    assert rs["context_ids"]


def test_docx_text_extraction():
    import io

    from docx import Document

    from agent_gavel.runtime.artifacts import extract_text
    d = Document()
    d.add_paragraph("hello docx 内容")
    buf = io.BytesIO()
    d.save(buf)
    text, kind = extract_text(buf.getvalue())
    assert kind == "docx"
    assert "hello docx" in text
