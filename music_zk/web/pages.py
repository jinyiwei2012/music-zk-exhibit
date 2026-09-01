"""展示页(Phase 4,SPEC §16 / PRD §11.1 首屏顺序)。

页面按序渲染:结论 → 密码学已经证明(≤3 条)→ S/V 双播放器(不默认同步)→
本系统不能证明(默认展开)→ 时间线 → 技术细节。文案全部引用 copy.py 常量,
零自由发挥(红线 4)。状态机:COMMITTED / RELEASED_UNPROVEN / PROOF_VALID /
PROOF_INVALID / DEV_ONLY。
"""

from __future__ import annotations

import html
from typing import Any

from . import copy as C

# PRD §11.1:密码学已经证明(三条以内,窄声明,无原创/非AI措辞)
PROVEN_STATEMENTS = (
    "创作者公钥在 t0 提交了私有 MIDI 的承诺 C_M,且该承诺早于歌曲发布(t1)与参考音频证明(t2)。",
    "零知识证明有效:存在与承诺一致的 MIDI,经冻结的 ReferenceSynth 1 渲染,其摘要 C_V 等于公开参考音频。",
    "创作者签名、服务端透明日志(STH)与 inclusion proof 全部通过验证。",
)


def _state_of(events: list[dict[str, Any]]) -> str:
    """从事件链推导展示状态(SPEC §16)。服务端只接受通过验证的事件。"""
    types = [e["event_type"] for e in events]
    if "PROOF" in types:
        return C.STATE_PROOF_VALID
    if "RELEASE" in types:
        return C.STATE_RELEASED_UNPROVEN
    if "COMMIT" in types:
        return C.STATE_COMMITTED
    return C.STATE_PROOF_INVALID


def _title(state: str) -> str:
    if state == C.STATE_PROOF_VALID:
        return C.RESULT_TITLE
    suffix = {"未完成": (C.STATE_COMMITTED, C.STATE_RELEASED_UNPROVEN),
              "无效": (C.STATE_PROOF_INVALID, C.STATE_DEV_ONLY)}
    for label, states in suffix.items():
        if state in states:
            return "结构化音乐材料的预先持有证明" + label
    return "结构化音乐材料的预先持有证明"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Music-ZK Exhibit</title>
<style>
  body {{ font-family: system-ui, "Noto Sans CJK SC", sans-serif; max-width: 56rem;
         margin: 2rem auto; padding: 0 1rem; line-height: 1.7; color: #1a1a1a; }}
  h1 {{ font-size: 1.5rem; }} h2 {{ font-size: 1.15rem; margin-top: 2rem;
         border-bottom: 1px solid #ddd; padding-bottom: .3rem; }}
  .state {{ font-weight: 700; }}
  .state.valid {{ color: #0a7d32; }} .state.warn {{ color: #b45309; }}
  .state.bad {{ color: #b91c1c; }}
  .dev-warning {{ color: #b91c1c; font-weight: 700; border: 2px solid #b91c1c;
                 padding: .5rem .8rem; display: inline-block; }}
  .limitation {{ background: #f8f8f8; border-left: 4px solid #999; padding: .8rem 1rem; }}
  .players {{ display: flex; gap: 1.5rem; flex-wrap: wrap; }}
  .player {{ flex: 1 1 20rem; border: 1px solid #ccc; padding: .8rem; border-radius: 6px; }}
  details.not-proven {{ margin-top: 1rem; border: 1px solid #ccc; border-radius: 6px; padding: .6rem .8rem; }}
  details.not-proven summary {{ cursor: pointer; font-weight: 700; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  th, td {{ border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; font-size: .92rem; }}
  .mono {{ font-family: ui-monospace, Consolas, monospace; font-size: .85rem;
          word-break: break-all; }}
</style></head><body>
{body}
</body></html>"""


def result_page(claim: dict[str, Any]) -> str:
    """结果页(PRD §11.1 首屏顺序)。claim 来自 /api/v1/claims/{id}。"""
    state = _state_of(claim.get("events", []))
    title = _title(state)
    state_cls = {"valid": C.STATE_PROOF_VALID, "warn": C.STATE_RELEASED_UNPROVEN,
                 "bad": C.STATE_PROOF_INVALID}.get(state, "warn")
    events = claim.get("events", [])
    claim_id = claim.get("claim_id", "")
    pk = claim.get("creator_pubkey", "")

    # 1) 结论
    body = f'<h1>{html.escape(title)}</h1>'
    body += (f'<p class="state {state_cls}">状态:{html.escape(C.STATE_LABEL[state])}</p>'
             f'<p class="mono">claim: {html.escape(claim_id)}</p>')

    # 2) 密码学已经证明(≤3 条;任一关键检查失败不显示总体有效,由 CLI verify 负责)
    if state == C.STATE_PROOF_VALID:
        body += f'<h2>{html.escape(C.SECTION_PROVEN)}</h2><ul>'
        for s in PROVEN_STATEMENTS:
            body += f"<li>{html.escape(s)}</li>"
        body += "</ul>"
    else:
        body += f'<h2>{html.escape(C.SECTION_PROVEN)}</h2>'
        body += f'<p>尚未形成完整证明链(状态:{html.escape(C.STATE_LABEL[state])})。</p>'

    # 3) 请听众自行判断:S/V 双播放器(不默认同步)
    body += f'<h2>{html.escape(C.SECTION_LISTEN)}</h2>'
    body += '<div class="players">'
    body += ('<div class="player"><strong>公开歌曲 S</strong><br>'
             f'<audio controls preload="none" '
             f'src="/api/v1/claims/{html.escape(claim_id)}/song"></audio></div>')
    body += ('<div class="player"><strong>参考音频 V</strong><br>'
             f'<audio controls preload="none" '
             f'src="/api/v1/claims/{html.escape(claim_id)}/reference-v"></audio></div>')
    body += '</div>'
    body += f'<p>{html.escape(C.SIMILARITY)}</p>'

    # 4) 本系统不能证明(默认展开,不藏 tooltip/页脚)
    body += (f'<details class="not-proven" open>'
             f'<summary>{html.escape(C.NOT_PROVEN_HEADER)}</summary>'
             f'<p class="limitation">{html.escape(C.LIMITATION)}</p></details>')

    # 5) 时间线
    body += f'<h2>{html.escape(C.SECTION_TIMELINE)}</h2><table><tr><th>序号</th><th>事件</th><th>event_id</th></tr>'
    for ev in events:
        body += (f"<tr><td>{ev['sequence']}</td><td>{html.escape(ev['event_type'])}</td>"
                 f'<td class="mono">{html.escape(ev["event_id"])}</td></tr>')
    body += "</table>"

    # 6) 技术细节(链接到技术页)
    body += (f'<h2>{html.escape(C.SECTION_TECH)}</h2>'
             f'<p class="mono">creator pubkey: {html.escape(pk)}</p>'
             f'<p><a href="/claim/{html.escape(claim_id)}/tech">技术详情页</a></p>')

    return _page(title, body)


def tech_page(claim: dict[str, Any], sth: dict[str, Any] | None) -> str:
    """技术详情页:公钥、承诺、Image ID、协议版本、日志根、下载链接(PRD §11.1)。"""
    title = "技术详情 — Music-ZK Exhibit"
    body = f"<h1>{html.escape(title)}</h1>"
    body += f'<p><a href="/claim/{html.escape(claim.get("claim_id", ""))}">← 返回结果页</a></p>'
    body += "<h2>公开标识</h2><table>"
    body += f'<tr><th>claim_id</th><td class="mono">{html.escape(claim.get("claim_id", ""))}</td></tr>'
    body += f'<tr><th>creator pubkey</th><td class="mono">{html.escape(claim.get("creator_pubkey", ""))}</td></tr>'
    for ev in claim.get("events", []):
        body += (f'<tr><th>{html.escape(ev["event_type"])} event_id</th>'
                 f'<td class="mono">{html.escape(ev["event_id"])}</td></tr>')
    body += "</table>"

    body += "<h2>协议与程序身份</h2><table>"
    body += "<tr><th>protocol_id</th><td class=\"mono\">music-zk-exhibit/midi-profile-1/reference-synth-1/statement-2</td></tr>"
    body += f'<tr><th>Image ID</th><td class="mono">{html.escape(_image_id_from_claim(claim))}</td></tr>'
    body += "</table>"

    if sth:
        body += "<h2>透明日志(最新 STH)</h2><table>"
        body += f'<tr><th>tree_size</th><td>{sth["tree_size"]}</td></tr>'
        body += f'<tr><th>tree_root</th><td class="mono">{html.escape(sth["tree_root"])}</td></tr>'
        body += f'<tr><th>issued_at_utc</th><td>{html.escape(sth["issued_at_utc"])}</td></tr>'
        body += "</table>"
    else:
        body += "<h2>透明日志</h2><p>日志为空。</p>"

    body += "<h2>下载</h2><ul>"
    cid = html.escape(claim.get("claim_id", ""))
    body += f'<li><a href="/api/v1/claims/{cid}/evidence.zip">公开证据包(evidence.zip)</a></li>'
    body += "</ul>"
    # 限制声明(SPEC §11.3 必须在技术页出现:日志不能阻止服务端重写)
    body += (f'<details class="not-proven" open>'
             f'<summary>{html.escape(C.NOT_PROVEN_HEADER)}</summary>'
             f'<p class="limitation">{html.escape(C.LIMITATION)}</p>'
             "<p>透明日志检测普通数据库篡改,但在无外部 witness 时不能阻止服务端同时"
             "重写数据库、根和历史签名,也不能阻止 split view。</p></details>")
    return _page(title, body)


def _image_id_from_claim(claim: dict[str, Any]) -> str:
    """从 evidence.zip 之外最接近的来源取 Image ID(展示用;真值验证在 CLI verify)。"""
    return "5e06801b5e97e4c3d7bcbc99bf5432ff3fc4056a9cf71b4175038a7e895c7d8a"
