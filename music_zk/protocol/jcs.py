"""RFC 8785 JCS 规范化 JSON(Phase 3,SPEC §10.2)。

事件签名、event_id、Merkle 叶都依赖 JCS(event body 规范化)。权威向量见
`rust/reference-core/src/jcs_vectors.rs`(serde_jcs 生成);本模块通过 `jcs` 包
(PyPI,纯 Python 实现 RFC 8785)得到同一字节流——tests/test_jcs.py 逐字节对拍。

行为要点(RFC 8785):
- 键按 UTF-16 码元字典序排序;输出 UTF-8,非 ASCII 不转义;
- 仅转义必须转义的字符(引号/反斜杠/控制字符),控制字符用 `\\uXXXX` 小写 hex;
- 数字按 ES6 Number 格式化(-0.0 → 0、1e3 → 1000)。
"""

from __future__ import annotations

from typing import Any

from jcs import canonicalize as _canonicalize


class JcsError(ValueError):
    """JCS 规范化失败(如含 NaN/Infinity 的 float,JSON 无法表示)。"""


def canonicalize(obj: Any) -> bytes:
    """返回 obj 的 RFC 8785 规范化字节流。

    入参必须是 JSON 可表示的结构(dict/list/str/int/float/bool/None);
    dict 键必须为 str。float 含 NaN/±Infinity 时抛 JcsError。
    """
    try:
        return _canonicalize(obj)
    except Exception as e:  # noqa: BLE001 - jcs 包错误类型不定,统一包装
        raise JcsError(f"JCS 规范化失败: {e}") from e


def to_string(obj: Any) -> str:
    """返回 obj 的 RFC 8785 规范化字符串(UTF-8 解码)。"""
    return canonicalize(obj).decode("utf-8")
