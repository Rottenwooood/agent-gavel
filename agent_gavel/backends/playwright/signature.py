"""页面结构签名与 diff：断言三态的证据来源。"""

import difflib

_SIG_BODY = r"""
  function __sig(root){
    const MAX=6000;
    const bodyScope=(root===document.body);
    const out=[];
    const norm=x=>{const s=(x==null?'':String(x)).replace(/\s+/g,' ').trim();return s;};
    out.push('URL='+location.href);
    out.push('TITLE='+norm(document.title));
    let count=0;
    const w=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);
    let e;
    while((e=w.nextNode())){
      if(++count>MAX)break;
      const tag=e.tagName.toLowerCase();
      let line=tag+(e.id?'#'+e.id:'');
      if(e.classList&&e.classList.length){
        const cls=Array.from(e.classList).filter(c=>!/^(sc-|jss|css-)/.test(c)).sort().join('.');
        if(cls)line+='.'+cls;
      }
      for(const a of ['name','type','role','aria-expanded','aria-hidden','placeholder','alt']){
        const av=e.getAttribute(a); if(av)line+='['+a+'='+norm(av).slice(0,60)+']';
      }
      if(e.tagName==='INPUT'||e.tagName==='TEXTAREA'){line+='='+norm(e.value).slice(0,200);}
      else if(e.tagName==='SELECT'){const so=e.selectedOptions&&e.selectedOptions[0];line+='='+norm(so?so.text:'');}
      if(!e.children.length){const tx=norm(e.textContent||e.getAttribute('placeholder')||'');if(tx)line+='::'+tx.slice(0,120);}
      out.push(line);
    }
    return {lines:out, bodyScope:bodyScope, scope:(root===document.body?'body':(root.tagName+(root.id?'#'+root.id:'')))};
  }
"""
_SIG_PAGE_JS = "() => {" + _SIG_BODY + " return __sig(document.body); }"
_SIG_SCOPE_JS = (
    "el => {" + _SIG_BODY + " let root=null; let n=el;"
    " while(n&&n!==document.body&&n.parentElement){"
    "   if(/^(FORM|MAIN|SECTION|ARTICLE|DIV|UL|OL|TABLE|NAV)$/.test(n.tagName)){root=n;break;}"
    "   n=n.parentElement; }"
    " if(!root) root=el.parentElement||el;"
    " return __sig(root); }"
)


async def page_signature(backend, handle, target=None):
    """动作前后快照：作用域内逐节点结构签名（tag#id.class+属性+值+叶子文本），
    含地址/标题两行头。给了 target 就取"目标最近的容器"作用域（更细、
    噪音更少），否则整页 body。返回 dict，供 diff_summary 比较。"""
    if target:
        try:
            loc = (await backend.resolve(handle.page, target)).first
            return await loc.evaluate(_SIG_SCOPE_JS)
        except Exception:
            pass  # silent-ok: best-effort，失败不影响主流程
    try:
        return await handle.page.evaluate(_SIG_PAGE_JS)
    except Exception:
        return None


def diff_summary(before, after):
    """对比前后结构签名，给出可读证据 + 是否"有意义地变化"。

    判定（沿用旧通道语义）：
      - 地址/标题头变化 → 一定是导航级变化；
      - 局部作用域（小而纯净）→ 任意 1 处增删即有意义；
      - 整页作用域（噪音多）→ 需 >=4 处增删，或变化占比 >=2%。
    """
    bl = before.get("lines") if isinstance(before, dict) else None
    al = after.get("lines") if isinstance(after, dict) else None
    if not bl or not al:
        same = before == after
        return {"changed": not same, "meaningful": not same}
    sm = difflib.SequenceMatcher(None, bl, al)
    added = removed = 0
    s_add, s_rem = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            added += j2 - j1
            s_add += al[j1:j2]
        if tag in ("delete", "replace"):
            removed += i2 - i1
            s_rem += bl[i1:i2]
    header_changed = bl[:2] != al[:2]
    body = bool(before.get("bodyScope")) or bool(after.get("bodyScope"))
    n_changed = added + removed
    if header_changed:
        meaningful = True
    elif body:
        total = max(len(bl), len(al))
        meaningful = n_changed >= 4 or (total > 0 and n_changed / total >= 0.02)
    else:
        meaningful = n_changed >= 1
    return {
        "changed": n_changed > 0,
        "meaningful": meaningful,
        "added": added,
        "removed": removed,
        "scope": before.get("scope") or after.get("scope"),
        "samples_added": s_add[:6],
        "samples_removed": s_rem[:6],
    }
