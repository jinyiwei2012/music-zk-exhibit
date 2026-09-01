"""页面文案常量(SPEC §16 / PRD §1/§11 / AGENTS.md §3.7)——集中单一权威,逐字复制,零自由发挥。

红线 4:页面只引用本模块常量;禁止出现"原创已验证""非 AI(已认证)"及任何意思
等价的徽章;"本系统不能证明"默认展开;`S/V 相似性`永远显示"未由系统判断"。
"""

from __future__ import annotations

# --- 结论与声明(AGENTS.md §3.7,一个字符都不许改) ---

RESULT_TITLE = "结构化音乐材料的预先持有证明有效"

LIMITATION = (
    "本证明不判断公开歌曲 S 与参考音频 V 是否相似;不证明 MIDI 的原创性、获得方式、"
    "完整 DAW 工程的存在、版权归属或创作者未使用 SUNO 等生成式工具;也不排除创作者"
    "在发布前根据生成音频或其他来源扒谱制作 MIDI。S 与 V 的音乐对应关系由听众自行判断。"
)

# 单列、固定输出(SPEC §15 步骤 11 / §16)
SIMILARITY = "S/V similarity: not evaluated by this system"

DEV_WARNING = "不是密码学证明"  # 红色,DEV_ONLY 状态

NOT_PROVEN_HEADER = "本系统不能证明"  # 默认展开,不藏 tooltip/页脚

# --- 状态机(SPEC §16 / PRD §11.2) ---

STATE_COMMITTED = "COMMITTED"
STATE_RELEASED_UNPROVEN = "RELEASED_UNPROVEN"
STATE_PROOF_VALID = "PROOF_VALID"
STATE_PROOF_INVALID = "PROOF_INVALID"
STATE_DEV_ONLY = "DEV_ONLY"

STATE_LABEL = {
    STATE_COMMITTED: "已提交承诺(无有效证明)",
    STATE_RELEASED_UNPROVEN: "已发布,尚无有效证明",
    STATE_PROOF_VALID: "证明有效",
    STATE_PROOF_INVALID: "证明无效",
    STATE_DEV_ONLY: "仅开发执行",
}

# --- PRD §11.2 分项状态 ---

CHECK_ITEMS = (
    "创作者身份签名",
    "承诺事件早于发布事件",
    "服务端日志与签名",
    "ReferenceSynth 程序身份",
    "零知识证明",
    "公开 V 摘要",
    "S/V 音乐相似性",
)

CHECK_PASS = "通过"
CHECK_FAIL = "失败"
CHECK_NOT_EVALUATED = "未由系统判断"
CHECK_DEV_ONLY = "仅开发执行,不能作为证明"

# --- PRD §11.1 首屏节标题 ---

SECTION_PROVEN = "密码学已经证明"
SECTION_LISTEN = "请听众自行判断"
SECTION_TIMELINE = "时间线"
SECTION_TECH = "技术细节"
