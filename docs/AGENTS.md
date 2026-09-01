# AGENTS.md — docs/ 知识库(文档治理)

> 本目录存放实施计划与动态事实。任何 agent 先读**根目录 AGENTS.md**(唯一执行入口),本文只补充根文件未覆盖的部分,不重复其内容。冲突裁决一律以根 AGENTS.md §1/§8 及上游文档(SPEC/PRD/PLAN)为准。

## OVERVIEW

docs/ 是版本、基准、开放项等动态事实的**唯一目的地**;ENV.md / benchmarks.md / OPEN-QUESTIONS.md 已存在并持续追加;LIVE-USB.md 仍为目标文件,首次写入时创建。

## STRUCTURE

```
docs/
├── PLAN.md               # 存在·实施顺序/Windows 策略/降级路径(根 AGENTS.md §8 路由;v0.2 = Win 原生迁移后)
├── ENV.md                # 存在·环境版本事实表(WSL 侧 guest 构建 + Windows 宿主 + Win 原生迁移要点)
├── benchmarks.md         # 存在·全部基准数字(WSL 历史行 + M0-Win 原生行;模板见 SPEC §18)
├── OPEN-QUESTIONS.md     # 存在·协议冲突与未定义点(image_id 字节序陷阱等)
├── LIVE-USB.md           # 规划中·WSL 不可用降级路径(PLAN.md §6.4)
└── public-evidence-spec/ # .gitignore 已预留,尚未创建
```

## 文件格式约定(仅本文定义;根文件不涉及)

- **ENV.md**:版本事实表,每行 = `组件 | 版本 | 实测日期 | 来源命令`。环境版本变更(如 WSL2 内 rzup 安装、Win 原生迁移要点)追加新行,不改旧行。
- **benchmarks.md**:按 SPEC §18 模板;每条含负载描述、耗时、峰值内存、receipt 大小、机器与日期。新基准追加;修订旧值须注明原因。
- **OPEN-QUESTIONS.md**:每条 = `日期 | 出处(SPEC/PRD/PLAN §N) | 冲突或未定义点 | 建议 | 状态`。解决后更新状态,不删历史。

## 缺失目的地的处理(根文件未覆盖)

- 路由目标不存在时:首次写入**创建文件**,按上方格式约定,创建后向用户报告"已在 docs/ 新建 X.md"。
- 不得因目的地缺失就把版本/基准/开放项改写到其他文档——根 AGENTS.md §8 的路由是唯一的。

## 交叉引用纪律(根文件未覆盖)

- 全文按 `SPEC §N` / `PLAN.md §N` 节号引用;改动任一文档章节编号,必须同步更新全部引用锚点。
- docs/ 内文件是"目的地",不承载协议/产品语义:协议问题进 OPEN-QUESTIONS,版本进 ENV.md,基准进 benchmarks.md。

## 反模式(仅本文追加;根红线见根文件 §1)

- 不重复根 AGENTS.md 的红线与文案常量——它们由根文件单一权威。
- 不发明未约定的文档格式;格式以本文约定为准。
- PLAN.md 是顺序与 Windows 策略的权威,不另起炉灶。
